import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx
import pytest

from csirt.collector import collect_due, collect_source, retry_after
from csirt.db import claim_source, connect
from conftest import rss

SOURCE = "bonjour-la-fuite"


def client_for(body=None, status=200, headers=None):
    return httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(status, content=body or rss(), headers=headers)))


def due(settings, source=SOURCE):
    with connect(settings) as db:
        db.execute("UPDATE sources SET next_poll_at=0 WHERE id=?", (source,))


def state(settings, source=SOURCE):
    with connect(settings) as db:
        return dict(db.execute("SELECT * FROM sources WHERE id=?", (source,)).fetchone())


def test_idempotence_no_requests_before_due_and_conditional_get(settings):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, content=rss(), headers={"ETag": '"v1"', "Last-Modified": "Tue, 20 Oct 2026 10:00:00 GMT"})
        assert request.headers["If-None-Match"] == '"v1"'
        assert "If-Modified-Since" in request.headers
        return httpx.Response(304)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert collect_source(settings, client, SOURCE)["created"] == 1
        assert collect_source(settings, client, SOURCE)["result"] == "not_due"
        assert len(calls) == 1
        due(settings)
        assert collect_source(settings, client, SOURCE)["created"] == 0
    with connect(settings) as db:
        assert db.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1
    assert state(settings)["etag"] == '"v1"'


def test_same_body_skips_processing_and_changed_entry_updates(settings):
    with client_for() as client:
        collect_source(settings, client, SOURCE)
        due(settings)
        assert collect_source(settings, client, SOURCE)["unchanged"] == 0
    due(settings)
    with client_for(rss(summary="Nouvelles informations")) as client:
        assert collect_source(settings, client, SOURCE)["updated"] == 1
    with connect(settings) as db:
        assert db.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1
        assert db.execute("SELECT summary FROM observations").fetchone()[0] == "Nouvelles informations"


def test_url_fallback_handles_guid_change(settings):
    with client_for() as client:
        collect_source(settings, client, SOURCE)
    due(settings)
    with client_for(rss(guid="changed-guid")) as client:
        assert collect_source(settings, client, SOURCE)["updated"] == 1
    with connect(settings) as db:
        assert db.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1


def test_retry_after_exponential_backoff_and_recovery(settings):
    before = time.time()
    with client_for(status=429, headers={"Retry-After": "20000"}) as client:
        assert collect_source(settings, client, SOURCE)["result"] == "error"
    first = state(settings)
    assert first["failures"] == 1
    assert first["next_poll_at"] >= before + 20000
    due(settings)
    with client_for(status=503) as client:
        collect_source(settings, client, SOURCE)
    assert state(settings)["next_poll_at"] >= before + settings.poll_seconds * 4
    due(settings)
    with client_for() as client:
        collect_source(settings, client, SOURCE)
    assert state(settings)["failures"] == 0
    assert state(settings)["last_error"] is None


def test_retry_after_http_date():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    value = format_datetime(now + timedelta(hours=2), usegmt=True)
    assert retry_after(value, now.timestamp()) == 7200
    assert retry_after("invalid", now.timestamp()) == 0


def test_bad_xml_or_partial_feed_does_not_poison_cache(settings):
    with client_for(headers={"ETag": '"good"'}) as client:
        collect_source(settings, client, SOURCE)
    initial_hash = state(settings)["body_hash"]
    for body in (b"<broken", rss().replace(b"</channel>", b"<item/></channel>")):
        due(settings)
        with client_for(body, headers={"ETag": '"bad"'}) as client:
            assert collect_source(settings, client, SOURCE)["result"] == "error"
        assert state(settings)["body_hash"] == initial_hash
        assert state(settings)["etag"] == '"good"'
    with connect(settings) as db:
        assert db.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1


def test_feed_size_bound_and_redirects_not_followed(settings):
    small = replace(settings, max_bytes=20)
    with client_for() as client:
        assert collect_source(small, client, SOURCE)["result"] == "error"
    due(settings)
    with client_for(status=302, headers={"Location": "https://example.org/"}) as client:
        assert collect_source(settings, client, SOURCE)["result"] == "error"


def test_ttl_survives_unchanged_body(settings):
    # Contrat historique optionnel : respecter le TTL annonce plutot que
    # l'intervalle fixe utilise pour les quatre sources de veille.
    with connect(settings) as db:
        import json
        row=state(settings)
        config=json.loads(row['config_json']);config['fixed_poll_interval']=False
        db.execute('UPDATE sources SET config_json=? WHERE id=?',(json.dumps(config),SOURCE))
    with client_for(rss(ttl=240)) as client:
        collect_source(settings, client, SOURCE)
        assert state(settings)["next_poll_at"] >= time.time() + 14399
        due(settings)
        collect_source(settings, client, SOURCE)
        assert state(settings)["next_poll_at"] >= time.time() + 14399


def test_half_hour_fixed_poll_ignores_advisory_cache_and_ttl_but_not_retry_after(settings):
    before=time.time()
    with client_for(rss(ttl=240),headers={'Cache-Control':'max-age=10800','ETag':'"v1"'}) as client:
        collect_source(settings,client,SOURCE)
    first=state(settings)
    assert before+1800 <= first['next_poll_at'] <= time.time()+1800
    due(settings)
    with client_for(status=304,headers={'Retry-After':'7200','Cache-Control':'max-age=10800'}) as client:
        collect_source(settings,client,SOURCE)
    assert state(settings)['next_poll_at']>=time.time()+7199
    assert state(settings)['retry_not_before']>=time.time()+7199


def test_atomic_lease_allows_one_claim_even_after_crash(settings):
    def claim(n):
        return claim_source(settings, SOURCE, str(n), now=1000)

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(claim, range(2)))
    assert sum(c is not None for c in claims) == 1
    # Le bail expire, mais l'echeance protege encore le serveur apres un crash.
    assert claim_source(settings, SOURCE, "later", now=1200) is None
    assert claim_source(settings, SOURCE, "next", now=5000) is not None


def test_source_failure_does_not_stop_others(settings):
    def handler(req):
        return httpx.Response(503 if req.url.host == "bonjourlafuite.eu.org" else 200, content=rss(title="Exemple"))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        results = collect_due(settings, client)
    assert results[0]["result"] == "error"
    assert all(r["result"] == "ok" for r in results[1:])


def test_possible_group_only_for_exact_org_near_date_and_other_source(settings):
    for source, body in [
        (SOURCE, rss()),
        ("frenchbreaches", rss(title="Exemple", guid="fb1")),
        ("fuites-infos", rss(title="Exemple : une fuite", guid="fi1", date="Tue, 01 Sep 2026 10:00:00 +0000")),
    ]:
        with client_for(body) as client:
            collect_source(settings, client, source)
    with connect(settings) as db:
        assert db.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(DISTINCT incident_id) FROM observations WHERE source_id IN (?, ?)",
                          (SOURCE, "frenchbreaches")).fetchone()[0] == 1
    due(settings)
    with client_for(rss(guid="separate", url="https://example.org/another")) as client:
        collect_source(settings, client, SOURCE)
    with connect(settings) as db:
        assert db.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 3
