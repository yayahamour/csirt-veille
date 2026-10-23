"""Collecteur separe de l'API. Aucun travail de collecte dans une requete web."""
import argparse
import hashlib
import json
import logging
import random
import time
import uuid
from datetime import datetime, timedelta, timezone
from xml.etree.ElementTree import ParseError

import httpx
from defusedxml.common import DefusedXmlException

from csirt.config import Settings, load_sources
from csirt.db import claim_source, connect, initialize, sync_sources, touch_incident
from csirt.feeds import Entry, normalize, parse_feed
from csirt.http import fetch_request, retry_after, cache_delay
from csirt.runtime import start_collector, heartbeat, next_cycle_seconds, stop_collector
from csirt.grouping import grouping_key,queue_grouping,find_incident,next_incident_id,repair_duplicate_incidents

log = logging.getLogger(__name__)


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def store_entry(db, entry: Entry, source_id: str, seen: str) -> str:
    rows = db.execute("SELECT * FROM observations WHERE source_id=? AND (external_id=? OR url=?)",
                      (source_id, entry.external_id, entry.url)).fetchall()
    if len(rows) > 1:
        # Un GUID recycle pointant vers l'URL d'une autre observation ne doit pas
        # fusionner deux incidents historiques : conserver le cache non valide.
        raise ValueError("Conflit entre identifiant et URL de publication")
    old = rows[0] if rows else None
    if old and old["content_hash"] == entry.content_hash:
        return "unchanged"
    if old:
        incident_id = old["incident_id"]
        db.execute("UPDATE observations SET external_id=?, url=?, title=?, summary=?, published_at=?, updated_at=?, "
                   "organization=?, kind=?, status=?, categories=?, content_hash=?, last_changed_at=? WHERE id=?",
                   (entry.external_id, entry.url, entry.title, entry.summary, entry.published_at, entry.updated_at,
                    entry.organization, entry.kind, entry.status, json.dumps(entry.categories), entry.content_hash,
                    seen, old["id"]))
        db.execute("UPDATE incidents SET last_changed_at=? WHERE id=?", (seen, incident_id))
        touch_incident(db, incident_id)
        queue_grouping(db,incident_id,db.execute('SELECT organization FROM incidents WHERE id=?',(incident_id,)).fetchone()[0])
        return "updated"

    key = normalize(entry.organization or "") or None
    incident = None
    if key and entry.published_at:
        incident=find_incident(db,entry.organization,entry.published_at,source_id,entry.kind,entry=entry)
    if incident:
        incident_id = incident["id"]
        db.execute("UPDATE incidents SET last_changed_at=? WHERE id=?", (seen, incident_id))
    else:
        incident_id = db.execute(
            "INSERT INTO incidents(id,organization, organization_key, title, reference_at, first_seen_at, last_changed_at,grouping_key) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (next_incident_id(db),entry.organization, key, entry.title, entry.published_at, seen, seen,grouping_key(entry.organization))).lastrowid
    db.execute(
        "INSERT INTO observations(incident_id, source_id, external_id, url, title, summary, published_at, updated_at, "
        "organization, kind, status, categories, content_hash, first_seen_at, last_changed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (incident_id, source_id, entry.external_id, entry.url, entry.title, entry.summary, entry.published_at,
         entry.updated_at, entry.organization, entry.kind, entry.status, json.dumps(entry.categories),
         entry.content_hash, seen, seen))
    touch_incident(db, incident_id)
    queue_grouping(db,incident_id,db.execute('SELECT organization FROM incidents WHERE id=?',(incident_id,)).fetchone()[0])
    return "created"


def fetch(client: httpx.Client, source: dict, settings: Settings):
    validators = {}
    if source["etag"]:
        validators["If-None-Match"] = source["etag"]
    if source["last_modified"]:
        validators["If-Modified-Since"] = source["last_modified"]
    return fetch_request(client, json.loads(source["config_json"]), settings, validators=validators)


def collect_source(settings: Settings, client: httpx.Client, source_id: str) -> dict:
    token = uuid.uuid4().hex
    source = claim_source(settings, source_id, token)
    counts = {"created": 0, "updated": 0, "unchanged": 0, "rejected": 0}
    if source is None:
        return {"source": source_id, "result": "not_due"}
    config = json.loads(source["config_json"])
    result, parsed, body_hash, error = None, None, None, None
    try:
        result = fetch(client, source, settings)
        if result.status == 304:
            if not source["body_hash"]:
                raise ValueError("Reponse 304 avant le premier flux valide")
        elif result.status == 200:
            body_hash = hashlib.sha256(result.body).hexdigest()
            if body_hash != source["body_hash"]:
                parsed = parse_feed(result.body, source_id, settings.max_entries,
                                    fields=config["response"], organization_mode=config["organization_mode"])
                # Eviter de memoriser un flux partiellement lu et perdre ses entrees.
                if parsed.rejected:
                    raise ValueError(f"{parsed.rejected} entree(s) RSS invalide(s)")
        else:
            raise ValueError(f"Reponse HTTP {result.status}")
    except (httpx.HTTPError, ValueError, ParseError, DefusedXmlException) as exc:
        error = (f"{type(exc).__name__}: requete RSS echouee" if isinstance(exc, httpx.HTTPError)
                 else f"{type(exc).__name__}: {str(exc)[:350]}")

    now = time.time()
    with connect(settings) as db:
        db.execute("BEGIN IMMEDIATE")
        owned = db.execute("SELECT 1 FROM sources WHERE id=? AND lease_token=? AND lease_until>?",
                           (source_id, token, now)).fetchone()
        if not owned:
            return {"source": source_id, "result": "lease_expired"}
        if not error and parsed:
            # Tout le lot et le cache sont valides ensemble, ou aucun ne l'est.
            db.execute("SAVEPOINT feed_batch")
            try:
                for entry in parsed.entries:
                    counts[store_entry(db, entry, source_id, timestamp())] += 1
            except ValueError as exc:
                db.execute("ROLLBACK TO feed_batch")
                counts = dict.fromkeys(counts, 0)
                error = str(exc)
            finally:
                db.execute("RELEASE feed_batch")
        headers = result.headers if result else httpx.Headers()
        retry_delay=retry_after(headers.get("retry-after"), now)
        fixed=config.get("fixed_poll_interval",False)
        remote_delay = max(retry_delay, 0 if fixed else cache_delay(headers))
        if error:
            failures = source["failures"] + 1
            delay = max(min(86400, source["poll_seconds"] * 2 ** min(failures, 10)), remote_delay)
            db.execute("UPDATE sources SET failures=?, last_error=?, next_poll_at=?, retry_not_before=?, last_http_status=?, "
                       "lease_token=NULL, lease_until=NULL WHERE id=?",
                       (failures, error, now + delay + random.uniform(0, 300),now+delay,
                        result.status if result else None, source_id))
        else:
            ttl = parsed.ttl_seconds if parsed else source["feed_ttl_seconds"]
            delay = max(source["poll_seconds"], remote_delay, 0 if fixed else ttl)
            # Une reponse 200 sans validateurs efface les anciens ; 304 les conserve.
            etag = headers.get("etag") if result.status == 200 else headers.get("etag", source["etag"])
            modified = (headers.get("last-modified") if result.status == 200
                        else headers.get("last-modified", source["last_modified"]))
            db.execute("UPDATE sources SET failures=0, last_error=NULL, last_success_at=?, next_poll_at=?, retry_not_before=?, "
                       "etag=?, last_modified=?, body_hash=?, feed_ttl_seconds=?, last_http_status=?, lease_token=NULL, lease_until=NULL WHERE id=?",
                       (now, now + delay + (0 if fixed else random.uniform(0, min(300, delay * 0.1))),now+retry_delay, etag, modified,
                        body_hash or source["body_hash"], ttl, result.status, source_id))
    outcome = {"source": source_id, "result": "error" if error else "ok", **counts}
    if error:
        outcome["error"] = error
    log.info(json.dumps(outcome, ensure_ascii=True))
    return outcome


def collect_due(settings: Settings, client: httpx.Client | None = None) -> list[dict]:
    if client is None:
        with httpx.Client(trust_env=False) as owned_client:
            return collect_due(settings, owned_client)
    configs = sync_sources(settings)
    return [collect_source(settings, client, source["id"]) for source in configs if source["kind"] == "rss" and source["enabled"]]


def main():
    parser=argparse.ArgumentParser(description='Collecte RSS CSIRT — production bridée uniquement')
    parser.add_argument('--once',action='store_true')
    parser.add_argument('--check-config',action='store_true')
    args=parser.parse_args()
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    settings=Settings.from_env()
    if args.check_config:
        print(json.dumps({'valid':True,'mode':'production','sources':[s['id'] for s in load_sources(settings)]}))
        return
    initialize(settings)
    start_collector(settings)
    try:
        with httpx.Client(trust_env=False) as client:
            while True:
                heartbeat(settings)
                try:
                    outcomes=collect_due(settings,client)
                    repair_duplicate_incidents(settings)
                except (ValueError,OSError) as exc:
                    if args.once: parser.exit(1,f'Collecte invalide : {exc}\n')
                    log.error('Collecte invalide : %s',exc)
                    time.sleep(30)
                    continue
                if args.once:
                    raise SystemExit(1 if any(o['result'] in {'error','lease_expired'} for o in outcomes) else 0)
                time.sleep(next_cycle_seconds(settings))
    finally:
        stop_collector(settings)

if __name__=='__main__': main()
