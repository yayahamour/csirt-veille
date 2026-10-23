"""SQLite locale : alertes, publications et état de collecte uniquement."""
import sqlite3
import time
import json
import uuid
from contextlib import contextmanager
from csirt.config import Settings, load_sources, source_fingerprint
from csirt.feeds import normalize


def alphabetical_key(value):
    return normalize(value.replace('œ','oe').replace('Œ','OE').replace('æ','ae').replace('Æ','AE'))


def alphabetical_compare(left,right):
    a,b=alphabetical_key(left),alphabetical_key(right)
    return (a>b)-(a<b)

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY);
INSERT OR IGNORE INTO schema_version VALUES (1);
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, feed_url TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    next_poll_at REAL NOT NULL DEFAULT 0, last_attempt_at REAL,
    last_success_at REAL, failures INTEGER NOT NULL DEFAULT 0,
    etag TEXT, last_modified TEXT, body_hash TEXT, feed_ttl_seconds INTEGER NOT NULL DEFAULT 0,
    last_http_status INTEGER, last_error TEXT,
    lease_token TEXT, lease_until REAL
);
CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY, organization TEXT, organization_key TEXT,
    title TEXT NOT NULL, reference_at TEXT, first_seen_at TEXT NOT NULL,
    last_changed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS incidents_org_date ON incidents(organization_key, reference_at);
CREATE INDEX IF NOT EXISTS incidents_changed ON incidents(last_changed_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS incidents_reference ON incidents(reference_at DESC, id DESC);
CREATE TABLE IF NOT EXISTS incident_grouping_queue(
    incident_id INTEGER PRIMARY KEY REFERENCES incidents(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS incident_merges(
    old_id INTEGER PRIMARY KEY,incident_id INTEGER NOT NULL REFERENCES incidents(id),
    merged_at REAL NOT NULL,reason TEXT NOT NULL,snapshot TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY, incident_id INTEGER NOT NULL REFERENCES incidents(id),
    source_id TEXT NOT NULL REFERENCES sources(id), external_id TEXT NOT NULL,
    url TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL,
    published_at TEXT, updated_at TEXT, organization TEXT,
    kind TEXT NOT NULL, status TEXT NOT NULL, categories TEXT NOT NULL,
    content_hash TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_changed_at TEXT NOT NULL,
    UNIQUE(source_id, external_id), UNIQUE(source_id, url)
);
CREATE INDEX IF NOT EXISTS observations_incident ON observations(incident_id);
CREATE INDEX IF NOT EXISTS observations_incident_date ON observations(incident_id, published_at DESC);
CREATE INDEX IF NOT EXISTS observations_incident_status ON observations(incident_id,status);
CREATE INDEX IF NOT EXISTS observations_published ON observations(published_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS observations_source_date ON observations(source_id, published_at DESC);
CREATE INDEX IF NOT EXISTS observations_kind_date ON observations(kind, published_at DESC);
CREATE INDEX IF NOT EXISTS observations_status_date ON observations(status, published_at DESC);
CREATE TRIGGER IF NOT EXISTS observations_latest_insert AFTER INSERT ON observations BEGIN
    UPDATE incidents SET reference_at=(SELECT MAX(published_at) FROM observations WHERE incident_id=new.incident_id) WHERE id=new.incident_id;
END;
CREATE TRIGGER IF NOT EXISTS observations_latest_update AFTER UPDATE OF published_at,incident_id ON observations BEGIN
    UPDATE incidents SET reference_at=(SELECT MAX(published_at) FROM observations WHERE incident_id=new.incident_id) WHERE id=new.incident_id;
    UPDATE incidents SET reference_at=(SELECT MAX(published_at) FROM observations WHERE incident_id=old.incident_id) WHERE id=old.incident_id AND old.incident_id<>new.incident_id;
END;
CREATE TRIGGER IF NOT EXISTS observations_latest_delete AFTER DELETE ON observations BEGIN
    UPDATE incidents SET reference_at=(SELECT MAX(published_at) FROM observations WHERE incident_id=old.incident_id) WHERE id=old.incident_id;
END;
CREATE VIRTUAL TABLE IF NOT EXISTS observations_fts USING fts5(
    title, summary, organization, content='observations', content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS observations_ai AFTER INSERT ON observations BEGIN
    INSERT INTO observations_fts(rowid, title, summary, organization)
    VALUES (new.id, new.title, new.summary, new.organization);
END;
CREATE TRIGGER IF NOT EXISTS observations_ad AFTER DELETE ON observations BEGIN
    INSERT INTO observations_fts(observations_fts, rowid, title, summary, organization)
    VALUES ('delete', old.id, old.title, old.summary, old.organization);
END;
CREATE TRIGGER IF NOT EXISTS observations_au AFTER UPDATE ON observations BEGIN
    INSERT INTO observations_fts(observations_fts, rowid, title, summary, organization)
    VALUES ('delete', old.id, old.title, old.summary, old.organization);
    INSERT INTO observations_fts(rowid, title, summary, organization)
    VALUES (new.id, new.title, new.summary, new.organization);
END;

CREATE TABLE IF NOT EXISTS collector_runtime(
    id INTEGER PRIMARY KEY CHECK(id=1),
    mode TEXT NOT NULL CHECK(mode='production'), heartbeat_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS alert_state(
    id INTEGER PRIMARY KEY CHECK(id=1), generation TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 0, sequence INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS incident_grouping_aliases(
    incident_id INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    alias_key TEXT NOT NULL, PRIMARY KEY(incident_id,alias_key)
);
CREATE INDEX IF NOT EXISTS grouping_alias_lookup ON incident_grouping_aliases(alias_key,incident_id);
CREATE TABLE IF NOT EXISTS incident_splits(
    id INTEGER PRIMARY KEY, original_id INTEGER NOT NULL, new_id INTEGER NOT NULL,
    split_at REAL NOT NULL, reason TEXT NOT NULL, snapshot TEXT NOT NULL
);
"""

@contextmanager
def connect(settings: Settings):
    db = sqlite3.connect(settings.db_path, timeout=10)
    db.row_factory = sqlite3.Row
    db.create_collation('ALPHA',alphabetical_compare)
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=10000")
    db.execute("PRAGMA cache_size=-2048")
    db.execute("PRAGMA temp_store=FILE")
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()



CORE={'schema_version','sources','incidents','observations','incident_grouping_queue',
      'incident_merges','collector_runtime','alert_state','observations_fts',
      'incident_grouping_aliases','incident_splits'}
CORE_TRIGGERS={'observations_latest_insert','observations_latest_update','observations_latest_delete',
               'observations_ai','observations_ad','observations_au'}

def initialize(settings: Settings):
    configs=load_sources(settings)
    settings.db_path.parent.mkdir(parents=True,exist_ok=True)
    with connect(settings) as db:
        db.execute('PRAGMA journal_mode=WAL')
        exists=db.execute("SELECT 1 FROM sqlite_master WHERE name='schema_version'").fetchone()
        version=db.execute('SELECT MAX(version) FROM schema_version').fetchone()[0] if exists else 0
        if version and version>5: raise ValueError('Version de base plus récente que ce programme')
        if version and version<5:
            # Migration transactionnelle ; les publications, identifiants et délais HTTP restent intacts.
            db.execute('PRAGMA foreign_keys=OFF')
            db.execute('BEGIN IMMEDIATE')
            for row in db.execute("SELECT type,name FROM sqlite_master WHERE type IN ('view','trigger')").fetchall():
                if row['type']=='view' or row['name'] not in CORE_TRIGGERS:
                    db.execute(f'DROP {row["type"]} "'+row['name'].replace('"','""')+'"')
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
                name=row['name']
                if name not in CORE and not name.startswith(('sqlite_','observations_fts_')):
                    db.execute('DROP TABLE "'+name.replace('"','""')+'"')
            db.execute('DROP TABLE IF EXISTS collector_runtime')
            db.commit()
            db.execute('PRAGMA foreign_keys=ON')
        db.executescript(SCHEMA)
        for table, additions in {
            'incidents': [('grouping_key','TEXT'),('activity_seq','INTEGER NOT NULL DEFAULT 0'),('grouping_version','TEXT')],
            'sources': [('kind',"TEXT NOT NULL DEFAULT 'rss'"),('poll_seconds','INTEGER NOT NULL DEFAULT 1800'),
                        ('config_json','TEXT'),('config_hash','TEXT'),('retry_not_before','REAL NOT NULL DEFAULT 0')]
        }.items():
            columns={row['name'] for row in db.execute(f'PRAGMA table_info({table})')}
            for name,definition in additions:
                if name not in columns: db.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
        db.execute('CREATE INDEX IF NOT EXISTS incidents_grouping_date ON incidents(grouping_key,reference_at)')
        db.execute('CREATE INDEX IF NOT EXISTS incidents_activity ON incidents(activity_seq)')
        db.execute('INSERT OR IGNORE INTO alert_state(id,generation) VALUES(1,?)',(uuid.uuid4().hex,))
        if not version or version<5:
            db.execute('UPDATE incidents SET activity_seq=id')
            high=db.execute('SELECT MAX(value) FROM (SELECT COALESCE(MAX(id),0) value FROM incidents UNION ALL SELECT COALESCE(MAX(old_id),0) FROM incident_merges)').fetchone()[0]
            db.execute('UPDATE alert_state SET sequence=?,revision=revision+1 WHERE id=1',(high,))
            # Les anciennes archives de fusion ne gardent que les informations de regroupement.
            for row in db.execute('SELECT old_id,snapshot FROM incident_merges').fetchall():
                snapshot=json.loads(row['snapshot'])
                clean={k:v for k,v in snapshot.items() if k in ('incidents','observations')}
                db.execute('UPDATE incident_merges SET snapshot=? WHERE old_id=?',(json.dumps(clean),row['old_id']))
            db.execute("DELETE FROM sources WHERE (kind<>'rss' OR id='lemagit-cyberhebdo') AND NOT EXISTS(SELECT 1 FROM observations WHERE source_id=sources.id)")
            db.execute('INSERT OR IGNORE INTO schema_version VALUES(5)')
        for event in ('INSERT','UPDATE','DELETE'):
            db.execute(f'CREATE TRIGGER IF NOT EXISTS alerts_revision_{event.lower()} AFTER {event} ON observations BEGIN '
                       'UPDATE alert_state SET revision=revision+1 WHERE id=1; END')
        db.execute('CREATE TRIGGER IF NOT EXISTS alerts_revision_merge AFTER INSERT ON incident_merges BEGIN '
                   'UPDATE alert_state SET revision=revision+1 WHERE id=1; END')
        if db.execute('PRAGMA foreign_key_check').fetchone(): raise ValueError('Références invalides après migration')
    sync_sources(settings,configs)
    from csirt.grouping import backfill_grouping_keys
    backfill_grouping_keys(settings)

def touch_incident(db, incident_id):
    db.execute('UPDATE alert_state SET sequence=sequence+1 WHERE id=1')
    db.execute('UPDATE incidents SET activity_seq=(SELECT sequence FROM alert_state WHERE id=1) WHERE id=?',(incident_id,))

def sync_sources(settings: Settings, configs: list[dict] | None = None) -> list[dict]:
    configs = load_sources(settings) if configs is None else configs
    now = time.time()
    with connect(settings) as db:
        db.execute("BEGIN IMMEDIATE")
        ids = {config["id"] for config in configs}
        for old in db.execute("SELECT id FROM sources").fetchall():
            if old["id"] not in ids:
                db.execute("UPDATE sources SET enabled=0 WHERE id=?", (old["id"],))
        for config in configs:
            source_id = config["id"]
            fingerprint = source_fingerprint(config)
            serialized = json.dumps(config, sort_keys=True, ensure_ascii=False)
            old = db.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
            if old and old["kind"] != config["kind"]:
                raise ValueError(f"{source_id} : utiliser un nouvel identifiant pour changer kind")
            if old and old["lease_until"] and old["lease_until"] > now:
                continue
            if old is None:
                db.execute("INSERT INTO sources(id, name, feed_url, enabled, kind, poll_seconds, config_json, config_hash) "
                           "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                           (source_id, config["name"], config["request"]["url"], config["enabled"], config["kind"],
                            config["poll_seconds"], serialized, fingerprint))
            elif old["config_json"] != serialized:
                # La premiere adoption de la configuration par une base v1 ne
                # reinitialise pas son cache si son URL n'a pas change.
                changed = ((old["config_hash"] is not None and old["config_hash"] != fingerprint)
                           or old["feed_url"] != config["request"]["url"])
                db.execute("UPDATE sources SET name=?, feed_url=?, enabled=?, poll_seconds=?, config_json=?, config_hash=? WHERE id=?",
                           (config["name"], config["request"]["url"], config["enabled"], config["poll_seconds"],
                            serialized, fingerprint, source_id))
                if old["config_json"] is not None and config["kind"]=="rss" and config["fixed_poll_interval"] and not old["failures"] and (old["poll_seconds"]!=config["poll_seconds"] or not json.loads(old["config_json"]).get("fixed_poll_interval")):
                    db.execute("UPDATE sources SET next_poll_at=MAX(retry_not_before,MIN(next_poll_at,COALESCE(last_attempt_at,0)+?)) WHERE id=?",
                               (config["poll_seconds"],source_id))
                if changed:
                    # Conserver l'echeance, les baux et les delais Retry-After.
                    db.execute("UPDATE sources SET etag=NULL, last_modified=NULL, body_hash=NULL, feed_ttl_seconds=0, "
                               "last_success_at=NULL WHERE id=?", (source_id,))
    return configs


def claim_source(settings: Settings, source_id: str, token: str, now: float | None = None):
    now = time.time() if now is None else now
    with connect(settings) as db:
        db.execute("BEGIN IMMEDIATE")
        source = db.execute("SELECT * FROM sources WHERE id=? AND kind='rss' AND enabled=1 AND next_poll_at<=? "
                            "AND (lease_until IS NULL OR lease_until<=?)", (source_id, now, now)).fetchone()
        if source is None:
            return None
        db.execute("UPDATE sources SET lease_token=?, lease_until=?, last_attempt_at=?, next_poll_at=? WHERE id=?",
                   (token, now + settings.lease_seconds, now, now + source["poll_seconds"], source_id))
        return dict(source)
