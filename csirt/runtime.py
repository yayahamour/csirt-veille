"""État du collecteur RSS, toujours en production bridée."""
import time
from csirt.db import connect

def start_collector(settings):
    with connect(settings) as db:
        db.execute("INSERT INTO collector_runtime VALUES(1,'production',?) ON CONFLICT(id) DO UPDATE SET mode='production',heartbeat_at=excluded.heartbeat_at",(time.time(),))

def heartbeat(settings):
    with connect(settings) as db:
        db.execute("UPDATE collector_runtime SET mode='production',heartbeat_at=? WHERE id=1",(time.time(),))
    return 'production'

def collector_status(db):
    row=db.execute('SELECT heartbeat_at FROM collector_runtime WHERE id=1').fetchone()
    return {'mode':'production','active':bool(row and row[0]>time.time()-300),'heartbeat_at':row[0] if row else None}

def next_cycle_seconds(settings): return 30

def stop_collector(settings):
    with connect(settings) as db: db.execute('UPDATE collector_runtime SET heartbeat_at=0 WHERE id=1')
