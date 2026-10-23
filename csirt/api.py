"""API de consultation locale ; aucun accès aux sources depuis une requête web."""
import json
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal
from pathlib import Path
import anyio.to_thread
from fastapi import FastAPI,HTTPException,Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from csirt.config import Settings
from csirt.db import connect,initialize
from csirt.runtime import collector_status
from csirt.grouping import canonical_incident_id,grouping_key
from csirt.alert_status import ALERT_STATUS_SQL,alert_status_sql,incident_alerts,publication_assessment,LABELS
from csirt.reporting import reporting_period

Kind=Literal['data_breach','cyberattack','ransomware','vulnerability','other']
Status=Literal['confirmed','claimed','unreliable_claim','unknown']
AlertStatus=Literal['confirmed','claimed','unreliable_claim','unknown','mixed']

def observation(row):
    item=dict(row)
    item['categories']=json.loads(item['categories'])
    item.pop('content_hash',None)
    item['alert_assessment']=publication_assessment(item)
    return item

def snapshot(db,since=0,period=None):
    state=dict(db.execute('SELECT generation,revision,sequence FROM alert_state WHERE id=1').fetchone())
    # Les erreurs et succès RSS actualisent aussi les cartes, même sans nouvel article.
    sources=db.execute('SELECT COALESCE(SUM(last_attempt_at),0),SUM(failures) FROM sources').fetchone()
    period=period or reporting_period()
    # La bascule du bilan à 20 h actualise aussi la page sans nouvelle collecte.
    state['revision']=f"{state['revision']}:{sources[0]}:{sources[1]}:{period['starts_at']}"
    state['new_count']=db.execute('SELECT COUNT(*) FROM incidents WHERE activity_seq>?',(since,)).fetchone()[0]
    return state

def create_app(settings=None):
    settings=settings or Settings.from_env()
    @asynccontextmanager
    async def lifespan(app):
        initialize(settings)
        anyio.to_thread.current_default_thread_limiter().total_tokens=settings.web_threads
        yield
    app=FastAPI(title='CSIRT — rassemblement des alertes',version='0.2.1',lifespan=lifespan)
    web=Path(__file__).with_name('web')
    app.mount('/assets',StaticFiles(directory=web),name='assets')

    @app.middleware("http")
    async def web_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path == "/" or request.url.path.startswith("/assets/"):
            response.headers["Content-Security-Policy"] = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                                                           "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
                                                           "base-uri 'none'; form-action 'self'")
            response.headers["Cache-Control"] = "no-cache"
        else:
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/", include_in_schema=False)
    def home():
        return FileResponse(web / "index.html")


    @app.get('/health')
    def health():
        with connect(settings) as db:
            db.execute('SELECT 1').fetchone()
            return {'status':'ok','mode':'production','collector':collector_status(db)}

    @app.get('/updates')
    def updates(since:int=Query(0,ge=0)):
        with connect(settings) as db:
            db.execute('BEGIN')
            return {**snapshot(db,since),'collector':collector_status(db)}

    @app.get('/sources')
    def sources():
        with connect(settings) as db:
            return [dict(r) for r in db.execute('SELECT id,name,feed_url,enabled,poll_seconds,last_attempt_at,last_success_at,next_poll_at,last_http_status,last_error,failures FROM sources WHERE kind=\'rss\' ORDER BY name')]

    @app.get('/dashboard')
    def dashboard():
        period=reporting_period()
        params=(period['starts_at'],period['ends_at'])
        selection='WITH period_observations AS (SELECT * FROM observations WHERE published_at>=? AND published_at<?) '
        with connect(settings) as db:
            db.execute('BEGIN')
            statuses=[{'status':r[0],'label':LABELS[r[0]],'count':r[1]} for r in db.execute(
                selection+'SELECT ('+alert_status_sql('period_observations')+') status,COUNT(*) FROM '
                '(SELECT DISTINCT incident_id AS id FROM period_observations) i GROUP BY status',params)]
            return {'period':period,
                    'incidents':db.execute(selection+'SELECT COUNT(DISTINCT incident_id) FROM period_observations',params).fetchone()[0],
                    'observations':db.execute(selection+'SELECT COUNT(*) FROM period_observations',params).fetchone()[0],
                    'total_incidents':db.execute('SELECT COUNT(*) FROM incidents').fetchone()[0],
                    'sources':db.execute('SELECT COUNT(*) FROM sources WHERE enabled=1').fetchone()[0],
                    'multiple_sources':db.execute(selection+'SELECT COUNT(*) FROM (SELECT incident_id FROM period_observations GROUP BY incident_id HAVING COUNT(DISTINCT source_id)>1)',params).fetchone()[0],
                    'statuses':statuses,'collector':collector_status(db),'snapshot':snapshot(db,period=period),
                    'by_source':[dict(r) for r in db.execute(selection+'SELECT s.name,COUNT(o.id) count FROM sources s LEFT JOIN period_observations o ON o.source_id=s.id GROUP BY s.id ORDER BY count DESC,s.name COLLATE ALPHA',params)]}

    @app.get("/observations")
    def observations(
        source: str | None = Query(None, max_length=80),
        kind: Kind | None = None,
        status: Status | None = None,
        q: str | None = Query(None, min_length=1, max_length=128),
        since: datetime | None = None,
        until: datetime | None = None,
        sort: Literal["published_at", "last_changed_at"] = "published_at",
        order: Literal["asc", "desc"] = "desc",
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0, le=10000),
    ):
        from datetime import timezone

        clauses, params = [], []
        for column, value in (("source_id", source), ("kind", kind), ("status", status)):
            if value is not None:
                clauses.append(f"o.{column}=?")
                params.append(value)
        if since and until and since.replace(tzinfo=since.tzinfo or timezone.utc) > until.replace(tzinfo=until.tzinfo or timezone.utc):
            raise HTTPException(422, "since doit preceder until")
        for operator, date in ((">=", since), ("<=", until)):
            if date:
                date = date.replace(tzinfo=date.tzinfo or timezone.utc).astimezone(timezone.utc)
                clauses.append(f"o.published_at {operator} ?")
                params.append(date.isoformat(timespec="seconds"))
        if q:
            terms = re.findall(r"\w+", q, re.UNICODE)[:20]
            if not terms:
                raise HTTPException(422, "La recherche doit contenir un mot")
            clauses.append("o.id IN (SELECT rowid FROM observations_fts WHERE observations_fts MATCH ?)")
            params.append(" AND ".join('"' + t + '"' for t in terms))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        # sort/order sont valides par les enums avant interpolation.
        with connect(settings) as db:
            # Une seule photographie coherente du compteur et de la page, meme
            # si le collecteur ecrit entre les deux SELECT.
            db.execute("BEGIN")
            total = db.execute("SELECT COUNT(*) FROM observations o" + where, params).fetchone()[0]
            rows = db.execute("SELECT o.* FROM observations o" + where +
                              f" ORDER BY o.{sort} {order} NULLS LAST, o.id {order} LIMIT ? OFFSET ?",
                              [*params, limit, offset]).fetchall()
        return {"total": total, "limit": limit, "offset": offset, "items": [observation(row) for row in rows]}


    @app.get('/incidents')
    def incidents(limit:int=Query(25,ge=1,le=100),offset:int=Query(0,ge=0,le=10000),
                  alert_status:AlertStatus|None=None, source:str|None=Query(None,max_length=80),
                  q:str|None=Query(None,min_length=1,max_length=128),
                  since_sequence:int|None=Query(None,ge=0),
                  sort:Literal['last_changed_at','reference_at','activity_seq','organization','alert_status','source_name']='reference_at',
                  order:Literal['asc','desc']='desc'):
        clauses,params=[],[]
        if alert_status:
            clauses.append('('+ALERT_STATUS_SQL+')=?');params.append(alert_status)
        if since_sequence is not None:
            clauses.append('i.activity_seq>?');params.append(since_sequence)
        if source:
            clauses.append('EXISTS(SELECT 1 FROM observations o WHERE o.incident_id=i.id AND o.source_id=?)');params.append(source)
        if q:
            terms=re.findall(r'\w+',q,re.UNICODE)[:20]
            if not terms: raise HTTPException(422,'La recherche doit contenir un mot')
            clauses.append('(i.grouping_key=? OR EXISTS(SELECT 1 FROM observations o WHERE o.incident_id=i.id AND o.id IN (SELECT rowid FROM observations_fts WHERE observations_fts MATCH ?)))')
            params.extend([grouping_key(q),' AND '.join('"'+t+'"' for t in terms)])
        where=' WHERE '+' AND '.join(clauses) if clauses else ''
        # Expressions fixes uniquement ; le tri précède la pagination.
        status_labels='CASE ('+ALERT_STATUS_SQL+') '+' '.join(
            "WHEN '"+key+"' THEN '"+label.replace("'","''")+"'" for key,label in LABELS.items())+' END'
        sort_expression={
            'reference_at':'i.reference_at',
            'last_changed_at':'i.last_changed_at',
            'activity_seq':'i.activity_seq',
            'organization':"COALESCE(NULLIF(i.organization,''),i.title) COLLATE ALPHA",
            'alert_status':status_labels+' COLLATE ALPHA',
            'source_name':'(SELECT MIN(s.name COLLATE ALPHA) FROM observations o JOIN sources s ON s.id=o.source_id WHERE o.incident_id=i.id) COLLATE ALPHA',
        }[sort]
        with connect(settings) as db:
            db.execute('BEGIN')
            total=db.execute('SELECT COUNT(*) FROM incidents i'+where,params).fetchone()[0]
            rows=db.execute('SELECT i.*,(SELECT COUNT(*) FROM observations o WHERE o.incident_id=i.id) observation_count FROM incidents i'+where+f' ORDER BY {sort_expression} {order} NULLS LAST,i.id {order} LIMIT ? OFFSET ?',[*params,limit,offset]).fetchall()
            alerts=incident_alerts(db,[r['id'] for r in rows])
            origins={}
            if rows:
                marks=','.join('?' for _ in rows)
                for origin in db.execute(f'SELECT o.incident_id,s.id,s.name FROM observations o JOIN sources s ON s.id=o.source_id WHERE o.incident_id IN ({marks}) GROUP BY o.incident_id,s.id ORDER BY s.name COLLATE ALPHA',[r['id'] for r in rows]):
                    origins.setdefault(origin['incident_id'],[]).append({'id':origin['id'],'name':origin['name']})
            items=[{**dict(r),'sources':origins.get(r['id'],[]),'alert_assessment':alerts[r['id']],
                    'grouping':'possible_match' if r['observation_count']>1 else 'single_source'} for r in rows]
            return {'total':total,'limit':limit,'offset':offset,'items':items,'snapshot':snapshot(db)}

    @app.get('/incidents/{incident_id}')
    def incident(incident_id:int):
        with connect(settings) as db:
            db.execute('BEGIN')
            incident_id=canonical_incident_id(db,incident_id)
            row=db.execute('SELECT * FROM incidents WHERE id=?',(incident_id,)).fetchone()
            if row is None: raise HTTPException(404,'Incident introuvable')
            publications=db.execute('SELECT o.*,s.name source_name FROM observations o JOIN sources s ON s.id=o.source_id WHERE incident_id=? ORDER BY published_at DESC,o.id DESC',(incident_id,)).fetchall()
            aliases=[r[0] for r in db.execute('SELECT DISTINCT organization FROM observations WHERE incident_id=? AND organization IS NOT NULL AND organization<>?',(incident_id,row['organization'] or ''))]
            return {**dict(row),'organization_aliases':aliases,
                    'grouping':'possible_match' if len(publications)>1 else 'single_source',
                    'observations':[observation(r) for r in publications],
                    'alert_assessment':incident_alerts(db,[incident_id])[incident_id]}
    return app

app=create_app()
