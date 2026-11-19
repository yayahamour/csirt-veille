"""Tables et écritures du module CERT-FR, dans la base SQLite de l’Observatoire."""
import json
from contextlib import contextmanager
from datetime import datetime, timezone

from csirt.db import connect
from csirt.feeds import normalize

SCHEMA = """
CREATE TABLE IF NOT EXISTS anssi_publications (
    reference TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL,
    published_at TEXT, updated_at TEXT, data TEXT NOT NULL, search_text TEXT NOT NULL, product_search TEXT NOT NULL,
    first_seen TEXT NOT NULL, changed_at TEXT, checked_at TEXT NOT NULL,
    index_revision TEXT
);
CREATE INDEX IF NOT EXISTS anssi_publications_date ON anssi_publications(published_at);
CREATE TABLE IF NOT EXISTS anssi_cves (
    reference TEXT NOT NULL REFERENCES anssi_publications(reference),
    cve TEXT NOT NULL, PRIMARY KEY(reference, cve)
);
CREATE INDEX IF NOT EXISTS anssi_cves_id ON anssi_cves(cve);
CREATE TABLE IF NOT EXISTS anssi_changes (
    id INTEGER PRIMARY KEY, reference TEXT NOT NULL REFERENCES anssi_publications(reference),
    detected_at TEXT NOT NULL, fields TEXT NOT NULL, previous TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS anssi_changes_reference ON anssi_changes(reference, id DESC);
CREATE TABLE IF NOT EXISTS anssi_tracking (
    reference TEXT PRIMARY KEY REFERENCES anssi_publications(reference),
    read INTEGER NOT NULL DEFAULT 0, important INTEGER NOT NULL DEFAULT 0,
    watch INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'new',
    note TEXT NOT NULL DEFAULT '', updated_at TEXT, severity TEXT, severity_reason TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS anssi_targets (
    reference TEXT NOT NULL REFERENCES anssi_publications(reference),
    kind TEXT NOT NULL CHECK(kind IN ('organization','sector','asset')),
    target_key TEXT NOT NULL, PRIMARY KEY(reference, kind, target_key)
);
CREATE TABLE IF NOT EXISTS anssi_sync (
    id INTEGER PRIMARY KEY, requested_at TEXT NOT NULL, started_at TEXT,
    finished_at TEXT, status TEXT NOT NULL, lease_until REAL,
    errors TEXT NOT NULL DEFAULT '[]', saved INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS anssi_single_job ON anssi_sync((1))
    WHERE status IN ('queued','running');
CREATE TABLE IF NOT EXISTS anssi_cache (
    source TEXT NOT NULL, cache_key TEXT NOT NULL, data TEXT,
    retrieved_at TEXT, next_attempt REAL NOT NULL,
    PRIMARY KEY(source, cache_key)
);
CREATE TABLE IF NOT EXISTS anssi_kev (
    cve TEXT PRIMARY KEY, date_added TEXT, retrieved_at TEXT NOT NULL
);
"""

TRACKING = dict(read=False, important=False, watch=False, status="new", note="", updated_at=None,
                severity=None, severity_reason="")


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


@contextmanager
def transaction(db, run_id=None):
    """Fence les écritures du collecteur si son bail a été repris."""
    with db:
        db.execute("BEGIN IMMEDIATE")
        if run_id is not None and not db.execute(
            "SELECT 1 FROM anssi_sync WHERE id=? AND status='running'", (run_id,)
        ).fetchone():
            raise RuntimeError("Synchronisation CERT-FR reprise par un autre collecteur")
        yield db


@contextmanager
def write(settings):
    with connect(settings) as db, transaction(db):
        yield db


def store_publication(db, item, now, index_revision=None):
    ref = item["reference"]
    row = db.execute("SELECT * FROM anssi_publications WHERE reference=?", (ref,)).fetchone()
    if row:
        previous = json.loads(row["data"])
        for key in ("published_at", "updated_at"):
            item[key] = item[key] or previous[key]
        changed = sorted(key for key in item if item[key] != previous.get(key))
        if changed:
            db.execute(
                "INSERT INTO anssi_changes(reference,detected_at,fields,previous) VALUES(?,?,?,?)",
                (ref, now, encode(changed), row["data"]),
            )
    else:
        changed = []
    product_search = normalize(" ".join([
        item["products_text"],
        *(value for product in item["products"] for value in product.values()),
    ]))
    search = normalize(" ".join([ref, item["title"], item["description"], product_search]))
    db.execute("""
        INSERT INTO anssi_publications VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(reference) DO UPDATE SET title=excluded.title,
          published_at=excluded.published_at, updated_at=excluded.updated_at,
          data=excluded.data, search_text=excluded.search_text, product_search=excluded.product_search,
          changed_at=excluded.changed_at,
          checked_at=excluded.checked_at, index_revision=excluded.index_revision
        """, (ref, item["kind"], item["title"], item["published_at"], item["updated_at"],
              encode(item), search, product_search, row["first_seen"] if row else now,
              now if changed else (row["changed_at"] if row else None), now, index_revision))
    if not row or "cves" in changed:
        db.execute("DELETE FROM anssi_cves WHERE reference=?", (ref,))
        db.executemany("INSERT INTO anssi_cves VALUES(?,?)", [(ref, cve) for cve in item["cves"]])
    return not row or bool(changed)
