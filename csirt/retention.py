"""Fenêtres de conservation persistantes, appliquées par le collecteur."""
import logging
import sqlite3
from datetime import datetime, timedelta, timezone

from csirt.db import connect

LOG = logging.getLogger(__name__)
SCHEMA = """
CREATE TABLE IF NOT EXISTS retention_options (
    id INTEGER PRIMARY KEY CHECK(id=1),
    incidents_days INTEGER, cve_days INTEGER,
    rss_revision INTEGER NOT NULL DEFAULT 0,
    cve_pending INTEGER NOT NULL DEFAULT 0,
    cve_full_index INTEGER NOT NULL DEFAULT 0,
    incident_id_floor INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO retention_options(id) VALUES(1);
"""


def options(db):
    return db.execute("SELECT * FROM retention_options WHERE id=1").fetchone()


def cutoff(days, now=None):
    if days is None:
        return None
    current_day = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    return (current_day - timedelta(days=days)).isoformat(timespec="seconds")


def update(db, incidents_days, cve_days):
    before = options(db)
    old = before["incidents_days"]
    expanded = old is not None and (incidents_days is None or incidents_days > old)
    if expanded:
        # Le prochain passage RSS doit relire le corps, même s’il est inchangé.
        db.execute("UPDATE sources SET etag=NULL,last_modified=NULL,body_hash=NULL WHERE kind='rss'")
    db.execute("""UPDATE retention_options SET incidents_days=?,cve_days=?,
                  rss_revision=rss_revision+?, cve_pending=MAX(cve_pending,?),
                  cve_full_index=MAX(cve_full_index,?) WHERE id=1""",
               (incidents_days, cve_days, int(expanded), int(cve_days != before["cve_days"]),
                int(before["cve_days"] is not None and cve_days is None)))


def prune_incidents(db, threshold):
    db.execute("""CREATE TEMP TABLE expired_incidents AS
                  SELECT DISTINCT incident_id AS id FROM observations
                  WHERE COALESCE(published_at,first_seen_at)<?""", (threshold,))
    if not db.execute("SELECT 1 FROM expired_incidents LIMIT 1").fetchone():
        return 0
    db.execute("""UPDATE retention_options SET incident_id_floor=MAX(incident_id_floor,
                  (SELECT COALESCE(MAX(id),0) FROM expired_incidents),
                  (SELECT COALESCE(MAX(old_id),0) FROM incident_merges),
                  (SELECT COALESCE(MAX(MAX(original_id,new_id)),0) FROM incident_splits)) WHERE id=1""")
    db.execute("""DELETE FROM incident_merges WHERE old_id IN (SELECT id FROM expired_incidents)
                  OR incident_id IN (SELECT id FROM expired_incidents)""")
    db.execute("""DELETE FROM incident_splits WHERE original_id IN (SELECT id FROM expired_incidents)
                  OR new_id IN (SELECT id FROM expired_incidents)""")
    deleted = db.execute("DELETE FROM observations WHERE COALESCE(published_at,first_seen_at)<?",
                         (threshold,)).rowcount
    db.execute("""DELETE FROM incidents WHERE id IN (SELECT id FROM expired_incidents)
                  AND NOT EXISTS(SELECT 1 FROM observations WHERE incident_id=incidents.id)""")
    return deleted


def prune_cve(db, threshold):
    db.execute("""CREATE TEMP TABLE expired_cve AS SELECT reference FROM anssi_publications
                  WHERE COALESCE(published_at,first_seen)<?""", (threshold,))
    for table in ("anssi_changes", "anssi_targets", "anssi_tracking", "anssi_cves", "anssi_publications"):
        deleted = db.execute(f"DELETE FROM {table} WHERE reference IN (SELECT reference FROM expired_cve)").rowcount
    db.execute("""DELETE FROM anssi_cache WHERE source IN ('NVD','EPSS','CVE') AND cache_key<>'*'
                  AND NOT EXISTS(SELECT 1 FROM anssi_cves WHERE cve=cache_key)""")
    return deleted


def prune(settings):
    with connect(settings) as db:
        db.execute("BEGIN IMMEDIATE")
        current = options(db)
        incidents_at = cutoff(current["incidents_days"])
        cve_at = cutoff(current["cve_days"])
        incidents = prune_incidents(db, incidents_at) if incidents_at else 0
        cves = prune_cve(db, cve_at) if cve_at else 0
        db.commit()
        if incidents or cves:
            LOG.info("Conservation : %s publications d’incidents et %s bulletins CVE purgés", incidents, cves)
        if (incidents or cves) and db.execute("PRAGMA freelist_count").fetchone()[0] >= 64:
            db.execute("PRAGMA busy_timeout=250")
            try:
                db.execute("VACUUM")
                db.execute("PRAGMA wal_checkpoint(PASSIVE)")
            except sqlite3.OperationalError:
                LOG.info("Compactage SQLite reporté", exc_info=True)
