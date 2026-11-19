"""Synchronisation indépendante, exécutée par le processus collecteur."""
import logging
import time
from datetime import datetime

import httpx
from defusedxml.common import DefusedXmlException
from xml.etree.ElementTree import ParseError

from csirt.anssi_sources import (
    CERT, CVE, CVE_API, EPSS, KEV, NVD, SourceError, array, cve_record, date,
    feed_publications, matches, nvd_record, object_or_empty, publication, request,
)
from csirt.anssi_store import encode, store_publication, timestamp, transaction, write
from csirt.db import connect
from csirt.retention import cutoff, options

LOG = logging.getLogger(__name__)
DAY = 86400
MANUAL_COOLDOWN = 300
ERRORS = (ValueError, OverflowError, httpx.HTTPError, ParseError, DefusedXmlException)


def expire(db, now):
    db.execute("""UPDATE anssi_sync SET status='failed', finished_at=?, lease_until=NULL, errors=?
                  WHERE status='running' AND lease_until<=?""",
               (timestamp(), encode(["Collecteur interrompu"]), now))


def queue(settings):
    with write(settings) as db:
        now = time.time()
        expire(db, now)
        job = db.execute("SELECT * FROM anssi_sync ORDER BY id DESC LIMIT 1").fetchone()
        if job and (job["status"] in ("queued", "running") or
                    (job["finished_at"] and db.execute(
                        "SELECT unixepoch(?) > ?", (job["finished_at"], now - MANUAL_COOLDOWN)
                    ).fetchone()[0])):
            return dict(job)
        job_id = db.execute("INSERT INTO anssi_sync(requested_at,status) VALUES(?,'queued')",
                            (timestamp(),)).lastrowid
        return dict(db.execute("SELECT * FROM anssi_sync WHERE id=?", (job_id,)).fetchone())


def claim(settings):
    with write(settings) as db:
        now = time.time()
        expire(db, now)
        job = db.execute("SELECT * FROM anssi_sync ORDER BY id DESC LIMIT 1").fetchone()
        if job and job["status"] == "running":
            return None
        if job and job["status"] == "queued":
            job_id = job["id"]
        else:
            if job and not options(db)["cve_pending"] and db.execute("SELECT unixepoch(?) > ?", (
                job["finished_at"], now - settings.anssi_poll_seconds,
            )).fetchone()[0]:
                return None
            job_id = db.execute("INSERT INTO anssi_sync(requested_at,status) VALUES(?,'queued')",
                                (timestamp(),)).lastrowid
        db.execute("UPDATE anssi_sync SET status='running',started_at=?,lease_until=? WHERE id=?",
                   (timestamp(), now + settings.lease_seconds, job_id))
        db.execute("UPDATE retention_options SET cve_pending=0 WHERE id=1")
        return job_id


class Synchronization:
    def __init__(self, settings, client, db, job):
        self.settings = settings
        self.client = client
        self.job = job
        self.db = db
        self.errors = []
        self.saved = 0
        self.sources_ok = 0
        self.deadline = time.monotonic() + 100

    def write(self):
        return transaction(self.db, self.job)

    def get(self, url, params=None, *, rss=False):
        with self.write() as db:
            db.execute("UPDATE anssi_sync SET lease_until=? WHERE id=?",
                       (time.time() + self.settings.lease_seconds, self.job))
        return request(self.client, self.settings, url, params, rss=rss)

    def error(self, source, exc):
        LOG.warning("CERT-FR %s : %s", source, type(exc).__name__)
        message = str(exc) if isinstance(exc, SourceError) else type(exc).__name__
        self.errors.append(f"{source} · {message}")

    def blocked(self, source):
        return self.db.execute("""SELECT 1 FROM anssi_cache WHERE source=? AND cache_key='*'
                                  AND next_attempt>?""", (source, time.time())).fetchone() is not None

    def cache(self, source, key, value=None, *, delay=DAY):
        with self.write() as db:
            db.execute("""INSERT INTO anssi_cache VALUES(?,?,?,?,?)
                          ON CONFLICT(source,cache_key) DO UPDATE SET next_attempt=excluded.next_attempt,
                          data=COALESCE(excluded.data,anssi_cache.data),
                          retrieved_at=COALESCE(excluded.retrieved_at,anssi_cache.retrieved_at)""",
                       (source, key, encode(value) if value is not None else None,
                        timestamp() if value is not None else None, time.time() + delay))

    def penalty(self, source, exc):
        if isinstance(exc, SourceError) and (exc.delay or str(exc) in ("HTTP 429", "HTTP 503")):
            self.cache(source, "*", delay=max(300, exc.delay))

    def store(self, db, item, revision=None):
        threshold = cutoff(options(db)["cve_days"])
        published = item["published_at"]
        if threshold and published and published < threshold:
            if item["kind"] in ("alerte", "avis"):
                db.execute("INSERT OR REPLACE INTO anssi_cache VALUES(?,?,?,?,?)",
                           ("CERT-FR hors plage", item["reference"],
                            encode({"revision": revision, "published": published}), None, 0))
            return
        db.execute("DELETE FROM anssi_cache WHERE source='CERT-FR hors plage' AND cache_key=?",
                   (item["reference"],))
        self.saved += store_publication(db, item, timestamp(), revision)

    def feed(self, kind):
        if self.blocked("CERT-FR"):
            self.errors.append(f"{kind} · délai de reprise du CERT-FR en cours")
            return
        try:
            payload = self.get(f"{CERT}/{kind}/feed/", rss=True)
            items = list(feed_publications(payload, self.settings, kind))
            if not items:
                raise SourceError("Aucune référence officielle reconnue dans le flux")
            with self.write() as db:
                for item in items:
                    self.store(db, item)
            self.sources_ok += 1
        except ERRORS as exc:
            self.error(kind, exc)
            self.penalty("CERT-FR", exc)

    def index(self, kind):
        if self.blocked("CERT-FR"):
            self.errors.append(f"{kind} · délai de reprise du CERT-FR en cours")
            return
        try:
            rows = self.get(f"{CERT}/{kind}/json/")
            if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
                raise SourceError("Index CERT-FR invalide")
            candidates = {row["reference"]: date(row.get("last_revision_date")) for row in rows
                          if matches(row.get("reference"), kind)}
            if rows and not candidates:
                raise SourceError("Aucune référence officielle reconnue dans l’index")
            retention = options(self.db)
            threshold = cutoff(retention["cve_days"])
            recent = sorted(candidates, key=lambda ref: (candidates[ref] or "", ref), reverse=True)
            if threshold:
                exclusions = {row[0]: (row[1], row[2]) for row in self.db.execute("""
                    SELECT cache_key,json_extract(data,'$.revision'),json_extract(data,'$.published')
                    FROM anssi_cache WHERE source='CERT-FR hors plage'""")}
                recent = [ref for ref in recent if
                          (not candidates[ref] or candidates[ref] >= threshold) and
                          (ref not in exclusions or exclusions[ref][0] != candidates[ref] or
                           exclusions[ref][1] >= threshold)]
            elif not retention["cve_full_index"]:
                recent = recent[:self.settings.anssi_index_limit]
            existing = {row["reference"]: (row["index_revision"], row["checked_at"]) for row in self.db.execute(
                "SELECT reference,index_revision,checked_at FROM anssi_publications WHERE kind=?", (kind,)
            )}
            cooling = {row[0] for row in self.db.execute(
                "SELECT cache_key FROM anssi_cache WHERE source='CERT-FR' AND next_attempt>?", (time.time(),)
            )}
            pending = [ref for ref in recent if ref not in existing]
            pending = sorted(candidates.keys() & existing.keys(), key=lambda ref: (existing[ref][1], ref)) + pending
            recheck_before = time.time() - 7 * DAY
            due = [ref for ref in pending if ref not in cooling and (
                ref not in existing or candidates[ref] != existing[ref][0] or
                datetime.fromisoformat(existing[ref][1]).timestamp() < recheck_before
            )]
            # Réserver une part du quota aux nouveaux bulletins pendant le rattrapage.
            changed = [ref for ref in due if ref in existing]
            new = [ref for ref in due if ref not in existing]
            selected = changed[:3] + new[:6 - min(len(changed), 3)]
            selected += changed[3:3 + 6 - len(selected)]
            self.sources_ok += 1
        except ERRORS as exc:
            self.error(kind, exc)
            self.penalty("CERT-FR", exc)
            return
        for ref in selected:
            if time.monotonic() >= self.deadline or self.blocked("CERT-FR"):
                break
            try:
                raw = object_or_empty(self.get(f"{CERT}/{kind}/{ref}/json/"))
                if raw.get("reference") != ref:
                    raise SourceError("Référence différente de l’index CERT-FR")
                item = publication(raw, kind)
                item["updated_at"] = max(filter(None, [item["updated_at"], candidates[ref]]), default=None)
                with self.write() as db:
                    self.store(db, item, candidates[ref])
            except ERRORS as exc:
                self.error(ref, exc)
                self.cache("CERT-FR", ref, delay=max(3600, getattr(exc, "delay", 0)))
                self.penalty("CERT-FR", exc)

    def due_cves(self, source, limit):
        return [row[0] for row in self.db.execute("""
            SELECT c.cve FROM anssi_cves c JOIN anssi_publications p USING(reference)
            LEFT JOIN anssi_cache a ON a.source=? AND a.cache_key=c.cve
            WHERE COALESCE(a.next_attempt,0)<=?
            GROUP BY c.cve ORDER BY a.retrieved_at IS NOT NULL, MAX(p.published_at) DESC, c.cve
            LIMIT ?""", (source, time.time(), limit))]

    def kev(self):
        source = "CISA KEV"
        cached = self.db.execute("SELECT next_attempt FROM anssi_cache WHERE source=? AND cache_key='catalogue'",
                                 (source,)).fetchone()
        if (cached and cached[0] > time.time()) or self.blocked(source):
            return
        try:
            raw = object_or_empty(self.get(KEV))
            now = timestamp()
            rows = [(row["cveID"], date(row.get("dateAdded")), now)
                    for row in map(object_or_empty, array(raw.get("vulnerabilities")))
                    if isinstance(row.get("cveID"), str) and CVE.fullmatch(row["cveID"])]
            if not rows or len(rows) != raw.get("count") or len({row[0] for row in rows}) != len(rows):
                raise SourceError("Catalogue incomplet ou invalide")
            with self.write() as db:
                db.execute("DELETE FROM anssi_kev")
                db.executemany("INSERT INTO anssi_kev VALUES(?,?,?)", rows)
                db.execute("INSERT OR REPLACE INTO anssi_cache VALUES(?,?,?,?,?)",
                           (source, "catalogue", encode({"count": len(rows)}), now, time.time() + DAY))
        except ERRORS as exc:
            self.error(source, exc)
            self.cache(source, "catalogue", delay=max(3600, getattr(exc, "delay", 0)))

    def epss(self):
        source = "EPSS"
        if self.blocked(source):
            return
        cves = self.due_cves(source, 50)
        if not cves:
            return
        try:
            result = object_or_empty(self.get(EPSS, {"cve": ",".join(cves), "limit": len(cves)}))
            if result.get("status") != "OK" or not isinstance(result.get("data"), list):
                raise SourceError("Réponse EPSS invalide")
            records = {}
            for record in map(object_or_empty, result["data"]):
                if record.get("cve") in cves:
                    score = float(record.get("epss") or "-1")
                    if 0 <= score <= 1:
                        records[record["cve"]] = dict(score=score, date=date(record.get("date")))
            for cve in cves:
                self.cache(source, cve, records.get(cve, {}))
        except (TypeError, *ERRORS) as exc:
            self.error(source, exc)
            self.cache(source, "*", delay=max(3600, getattr(exc, "delay", 0)))

    def enrich(self):
        if time.monotonic() >= self.deadline:
            return
        self.kev()
        if time.monotonic() >= self.deadline:
            return
        self.epss()
        for source in ("NVD", "CVE"):
            for cve in self.due_cves(source, 5):
                if time.monotonic() >= self.deadline or self.blocked(source):
                    break
                try:
                    if source == "NVD":
                        raw = object_or_empty(self.get(NVD, {"cveId": cve}))
                        if not isinstance(raw.get("vulnerabilities"), list):
                            raise SourceError("Réponse NVD invalide")
                        result = nvd_record(raw, cve)
                    else:
                        result = cve_record(object_or_empty(self.get(CVE_API + cve)), cve)
                    self.cache(source, cve, result, delay=7 * DAY)
                except ERRORS as exc:
                    if isinstance(exc, SourceError) and str(exc) == "HTTP 404":
                        self.cache(source, cve, delay=DAY)
                        continue
                    self.error(source, exc)
                    self.cache(source, cve, delay=max(3600, getattr(exc, "delay", 0)))
                    self.penalty(source, exc)


def collect_anssi(settings, client):
    job = claim(settings)
    if job is None:
        return None
    with connect(settings) as db:
        run = Synchronization(settings, client, db, job)
        try:
            for kind in ("cti", "ioc"):
                run.feed(kind)
            for kind in ("alerte", "avis"):
                run.index(kind)
            run.enrich()
        except Exception as exc:
            LOG.exception("Synchronisation CERT-FR interrompue")
            run.errors.append(f"Collecteur · {type(exc).__name__}")
        status = ("success" if run.sources_ok and not run.errors else
                  "partial" if run.sources_ok else "failed")
        db.execute("""UPDATE anssi_sync SET status=?,finished_at=?,lease_until=NULL,errors=?,saved=?
                      WHERE id=? AND status='running'""",
                   (status, timestamp(), encode(run.errors), run.saved, job))
    return status
