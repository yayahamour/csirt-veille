import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx
from fastapi.testclient import TestClient

from csirt.anssi_collect import claim, collect_anssi, queue
from csirt.anssi_sources import CERT, CVE_API, EPSS, KEV, NVD, publication
from csirt.anssi_store import store_publication
from csirt.api import create_app
from csirt.collector import collect_source
from csirt.db import connect
from csirt.retention import prune
from conftest import rss

REFERENCE = "CERTFR-2026-ALE-001"
CVE1 = "CVE-2026-12345"
CVE2 = "CVE-2026-7890"
HEADERS = {"X-CSIRT-Client": "observatoire"}
NOW = "2026-11-19T12:00:00+00:00"


def bulletin():
    return {
        "reference": REFERENCE,
        "title": "Vulnérabilités dans deux produits",
        "summary": f"Description réseau · {CVE2} <script>contenu à ignorer</script>",
        "cves": [{"name": CVE1}, {"name": CVE1}],
        "affected_systems": [
            {"description": "Versions antérieures à 3.0", "product": {"name": "Routeur", "vendor": {"name": "Éditeur A"}}},
            {"description": "Versions 4 et 5", "product": {"name": "Portail", "vendor": {"name": "Éditeur B"}}},
        ],
        "revisions": [
            {"revision_date": "2026-10-30T08:00:00.000000", "description": "Version initiale"},
            {"revision_date": "2026-11-19T12:00:00.000000", "description": "Ajout du correctif"},
        ],
        "content": f"<h2>Solution</h2><p>Des correctifs sont disponibles. Utiliser la version 3.0.</p>"
                   f"<h2>Contournement provisoire</h2><p>Restreindre l’accès.</p>"
                   f"<p>{CVE2} est activement exploitée. Une preuve de concept publique est disponible.</p>",
    }


class Sources:
    def __init__(self):
        self.bulletin = bulletin()
        self.revision = NOW
        self.unavailable = False
        self.calls = []

    def __call__(self, request):
        url = str(request.url).partition("?")[0]
        self.calls.append(url)
        if self.unavailable:
            return httpx.Response(503, headers={"Retry-After": "1800"})
        if url == f"{CERT}/alerte/json/":
            return httpx.Response(200, json=[{"reference": REFERENCE, "last_revision_date": self.revision,
                                               "json_url": "https://example.invalid/ignored"}])
        if url == f"{CERT}/alerte/{REFERENCE}/json/":
            return httpx.Response(200, json=self.bulletin)
        if url == f"{CERT}/avis/json/":
            return httpx.Response(200, json=[])
        if url in (f"{CERT}/cti/feed/", f"{CERT}/ioc/feed/"):
            kind = request.url.path.split("/")[1]
            reference = f"CERTFR-2026-{kind.upper()}-001"
            return httpx.Response(200, content=rss(
                title=f"Publication {kind}", url=f"https://www.cert.cyber.gouv.fr/{kind}/{reference}/"))
        if url == KEV:
            return httpx.Response(200, json={"count": 1, "vulnerabilities": [{"cveID": CVE2, "dateAdded": "2026-11-19"}]})
        if url == EPSS:
            return httpx.Response(200, json={"status": "OK", "data": [{"cve": CVE1, "epss": "0.024", "date": "2026-11-19"}]})
        if url == NVD:
            cve = request.url.params["cveId"]
            return httpx.Response(200, json={"vulnerabilities": [{"cve": {
                "id": cve, "descriptions": [{"lang": "en", "value": "A vulnerability"}],
                "metrics": {"cvssMetricV31": [{"cvssData": {"baseScore": 9.8, "version": "3.1"}}]},
            }}]})
        if url.startswith(CVE_API):
            return httpx.Response(200, json={"cveMetadata": {"cveId": url.removeprefix(CVE_API)},
                                             "containers": {"cna": {}}})
        raise AssertionError(f"URL inattendue {url}")


def run(settings, sources):
    with httpx.Client(transport=httpx.MockTransport(sources)) as client:
        return collect_anssi(settings, client)


def make_due(settings):
    with connect(settings) as db:
        db.execute("UPDATE anssi_sync SET finished_at='2020-01-01T00:00:00+00:00'")


def test_normalization_handles_multiple_products_cves_and_unconfirmed_claims():
    raw = bulletin()
    item = publication(raw, "alerte")
    assert item["cves"] == [CVE1, CVE2]
    assert [p["name"] for p in item["products"]] == ["Routeur", "Portail"]
    assert item["published_at"].startswith("2026-10-30")
    assert item["updated_at"] == NOW
    assert "contenu à ignorer" not in item["description"]
    assert "3.0" in item["remediation"]["patch"][0]
    assert "Restreindre" in item["remediation"]["workaround"][0]
    assert item["evidence"] == {"patch": True, "active": True, "poc": True}
    assert item["evidence_cves"] == {"active": [CVE2]}
    raw["content"] = "<p>Aucun correctif n’est disponible. Une preuve de concept est disponible en interne.</p>"
    assert publication(raw, "alerte")["evidence"] == {}


def test_collects_updates_and_retains_publications_and_cache_on_failure(settings):
    sources = Sources()
    assert run(settings, sources) == "success"
    assert run(settings, sources) is None
    make_due(settings)
    sources.calls.clear()
    assert run(settings, sources) == "success"
    assert KEV not in sources.calls
    assert NVD not in sources.calls
    assert f"{CERT}/alerte/{REFERENCE}/json/" not in sources.calls
    make_due(settings)
    sources.revision = "2026-11-20T12:00:00+00:00"
    sources.bulletin["title"] = "Avis révisé"
    assert run(settings, sources) == "success"
    make_due(settings)
    sources.unavailable = True
    assert run(settings, sources) == "failed"
    with connect(settings) as db:
        assert {row[0] for row in db.execute("SELECT kind FROM anssi_publications")} == {"alerte", "cti", "ioc"}
        assert db.execute("SELECT COUNT(*) FROM anssi_publications").fetchone()[0] == 3
        change = db.execute("SELECT * FROM anssi_changes").fetchone()
        assert json.loads(change["fields"]) == ["title", "updated_at"]
        assert json.loads(change["previous"])["title"] == bulletin()["title"]
        assert db.execute("SELECT COUNT(*) FROM anssi_kev").fetchone()[0] == 1
        assert "HTTP 503" in db.execute("SELECT errors FROM anssi_sync ORDER BY id DESC LIMIT 1").fetchone()[0]


def test_queue_and_claim_are_exclusive_and_recover_an_expired_job(settings):
    with ThreadPoolExecutor(max_workers=4) as executor:
        ids = list(executor.map(lambda _: queue(settings)["id"], range(4)))
    assert len(set(ids)) == 1
    with ThreadPoolExecutor(max_workers=4) as executor:
        jobs = list(executor.map(lambda _: claim(settings), range(4)))
    assert jobs.count(ids[0]) == 1
    with connect(settings) as db:
        db.execute("UPDATE anssi_sync SET lease_until=0")
    assert claim(settings) is None
    with connect(settings) as db:
        assert db.execute("SELECT status FROM anssi_sync").fetchone()[0] == "failed"


def test_api_filters_detail_provenance_and_tracking(settings):
    with TestClient(create_app(settings)) as client:
        home = client.get("/").text
        assert 'href="#cve"' in home
        assert 'href="#memory"' in home
        assert 'class="metrics' not in home
        resources = client.get("/resources")
        assert resources.status_code == 200
        assert resources.json()["database_bytes"] > 0
        assert resources.json()["disk"]["available"] <= resources.json()["disk"]["total"]
        assert client.get("/assets/anssi.js").status_code == 200
        base = "/anssi/publications"
        detail = f"{base}/{REFERENCE}"
        assert client.get(detail).status_code == 404
        assert client.post("/anssi/sync").status_code == 403
        assert client.post("/anssi/sync", headers=HEADERS).json()["status"] == "queued"
        assert run(settings, Sources()) == "success"
        counts = client.get("/anssi/summary").json()
        assert (counts["total"], counts["new_alerts"], counts["critical_alerts"], counts["cves"]) == (3, 1, 1, 2)
        params = {"kind": "alerte", "q": "reseau", "product": "editeur b", "cve": CVE1.lower(),
                  "severity": "critical", "patch": True, "active": True, "poc": True,
                  "since": "2026-10-30", "until": "2026-10-30", "consultation": "unread"}
        assert client.get(base, params=params).json()["items"][0]["reference"] == REFERENCE
        assert client.get(base, params={"product": "réseau"}).json()["total"] == 0
        assert client.get(base, params={"poc": False}).json()["total"] == 2
        assert client.get(base, params={"cve": "invalide"}).status_code == 422
        assert client.get(base, params={"since": "2026-10-30", "until": "2026-10-29"}).status_code == 422
        data = client.get(detail).json()
        assert data["severity_source"] == "CVSS / NVD"
        assert data["official_evidence_by_cve"][CVE2] == ["active"]
        assert data["enrichment"][CVE2]["CISA KEV"]["data"]["listed"]
        assert not data["enrichment"][CVE1]["CISA KEV"]["data"]["listed"]
        assert data["enrichment"][CVE1]["NVD"]["retrieved_at"]
        assert data["enrichment"][CVE1]["EPSS"]["data"]["score"] == 0.024
        assert client.patch(detail + "/tracking", json={"read": True}).status_code == 403
        assert client.patch(detail + "/tracking", headers=HEADERS, json={"severity": "medium"}).status_code == 422
        tracked = {"read": True, "watch": True, "important": True, "status": "review", "note": "<b>Analyse</b>",
                   "severity": "medium", "severity_reason": "Actif isolé"}
        assert client.patch(detail + "/tracking", headers=HEADERS, json=tracked).status_code == 200
        assert client.get(base, params={"consultation": "tracked", "treatment": "review"}).json()["total"] == 1
        assert client.patch(detail + "/tracking", headers=HEADERS, json={"read": False}).status_code == 200
        data = client.get(detail).json()
        assert data["severity_source"] == "Interne"
        assert not data["tracking"]["read"]
        assert data["tracking"]["note"] == tracked["note"]
        assert data["tracking"]["watch"]


def test_severity_from_explicit_official_data_has_priority(settings):
    raw = bulletin()
    raw.update(severity="Critique", severity_reason="Évaluation explicite")
    with connect(settings) as db:
        store_publication(db, publication(raw, "alerte"), NOW)
    with TestClient(create_app(settings)) as client:
        item = client.get(f"/anssi/publications/{REFERENCE}").json()
    assert item["severity"] == "critical"
    assert item["severity_source"] == "CERT-FR"
    assert item["severity_reason"] == raw["severity_reason"]


def test_retention_prunes_independently_and_refetches_unchanged_rss(settings):
    now = datetime.now(timezone.utc)
    age = now - timedelta(days=40)
    body = rss(date=format_datetime(age))

    def transport(request):
        assert "if-none-match" not in request.headers
        return httpx.Response(200, headers={"ETag": '"same"'}, content=body)

    def due():
        with connect(settings) as db:
            db.execute("UPDATE sources SET next_poll_at=0,retry_not_before=0 WHERE id=?", (source,))

    with connect(settings) as db:
        source = db.execute("SELECT id FROM sources ORDER BY id LIMIT 1").fetchone()[0]
        raw = bulletin()
        raw["published_at"] = age.isoformat()
        store_publication(db, publication(raw, "alerte"), now.isoformat())
    with httpx.Client(transport=httpx.MockTransport(transport)) as feed, TestClient(create_app(settings)) as client:
        assert collect_source(settings, feed, source)["created"] == 1
        change = {"incidents_days": 30, "cve_days": None}
        assert client.put("/retention", json=change).status_code == 403
        assert client.put("/retention", headers=HEADERS, json=change).status_code == 200
        prune(settings)
        assert client.get("/incidents").json()["total"] == 0
        assert client.get("/anssi/publications").json()["total"] == 1
        change = {"incidents_days": 60, "cve_days": 30}
        assert client.put("/retention", headers=HEADERS, json=change).json() == change
        prune(settings)
        assert client.get("/anssi/publications").json()["total"] == 0
        due()
        assert collect_source(settings, feed, source)["created"] == 1
        assert client.get("/retention").json() == change
        assert client.get("/incidents").json()["total"] == 1


def test_cve_retention_widens_on_next_sync(settings):
    now = datetime.now(timezone.utc)
    published = (now - timedelta(days=40)).isoformat()
    sources = Sources()
    sources.revision = now.isoformat()
    sources.bulletin["published_at"] = published
    with TestClient(create_app(settings)) as client:
        def set_days(days):
            change = {"incidents_days": None, "cve_days": days}
            assert client.put("/retention", headers=HEADERS, json=change).status_code == 200

        set_days(30)
        assert run(settings, sources) == "success"
        assert client.get(f"/anssi/publications/{REFERENCE}").status_code == 404
        set_days(60)
        assert run(settings, sources) == "success"
        assert client.get(f"/anssi/publications/{REFERENCE}").status_code == 200
