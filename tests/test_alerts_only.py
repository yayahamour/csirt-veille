import json
import sqlite3
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from csirt.api import create_app
from csirt.collector import store_entry
from csirt.config import Settings,load_sources
from csirt.db import connect,initialize
from csirt.feeds import Entry
from csirt.grouping import queue_grouping,repair_duplicate_incidents

def entry(name='Billets Nantes',guid='a',date='2026-10-20T10:00:00+00:00'):
    return Entry(guid,'https://example.org/'+guid,name,'Fuite de données',date,None,name,'data_breach','claimed',[])

def add(settings,name='Billets Nantes',guid='a',source='bonjour-la-fuite'):
    with connect(settings) as db: store_entry(db,entry(name,guid),source,'2026-10-22T12:00:00+00:00')

def test_only_production_and_four_rss_sources(settings,monkeypatch):
    monkeypatch.setenv('CSIRT_COLLECTION_MODE','debride')
    assert Settings.from_env().collection_mode=='production'
    with pytest.raises(ValueError): replace(settings,collection_mode='debride')
    with pytest.raises(ValueError): replace(settings,poll_seconds=900)
    sources=load_sources(settings)
    assert len(sources)==4 and all(s['kind']=='rss' and s['poll_seconds']>=1800 for s in sources)
    with TestClient(create_app(settings)) as api:
        assert api.post('/collector/mode',json={'mode':'debride'}).status_code==404
        for url in ('/matching/coverage','/matching/methods','/regions','/companies/825380157','/company-searches'):
            assert api.get(url).status_code==404
        assert len(api.get('/sources').json())==4

def test_new_publications_updates_and_ack_cursor_are_persistent(settings):
    add(settings)
    with TestClient(create_app(settings)) as api:
        baseline=api.get('/updates').json()
        assert api.get('/updates',params={'since':baseline['sequence']}).json()['new_count']==0
        # Autre source, orthographe compacte, même incident : une nouveauté et deux publications.
        add(settings,'BilletsNantes','b','frenchbreaches')
        update=api.get('/updates',params={'since':baseline['sequence']}).json()
        assert update['revision']!=baseline['revision'] and update['new_count']==1
        listed=api.get('/incidents',params={'since_sequence':baseline['sequence']}).json()
        assert listed['total']==1 and listed['items'][0]['observation_count']==2
        assert 'company_match' not in listed['items'][0]
        assert api.get('/incidents?q=BilletsNantes').json()['total']==1
        assert api.get('/incidents?q=Billets%20Nantes').json()['total']==1
        with connect(settings) as db:
            store_entry(db,entry('Billets Nantes'), 'bonjour-la-fuite','2026-10-22T12:01:00+00:00')
        # Relecture du même contenu : aucun changement de curseur ni faux nouveau.
        assert api.get('/updates').json()['sequence']==update['sequence']
        acknowledged=update['sequence']
    with TestClient(create_app(settings)) as api:
        restarted=api.get('/updates',params={'since':acknowledged}).json()
        assert restarted['generation']==baseline['generation'] and restarted['new_count']==0
        with connect(settings) as db:
            modified=replace(entry('Billets Nantes'),summary='Nouvelle information')
            store_entry(db,modified,'bonjour-la-fuite','2026-10-22T12:02:00+00:00')
        assert api.get('/updates',params={'since':acknowledged}).json()['new_count']==1

def test_polling_is_read_only_and_does_not_change_feed_deadlines(settings):
    add(settings)
    with connect(settings) as db: before=[dict(r) for r in db.execute('SELECT * FROM sources')]
    with TestClient(create_app(settings)) as api:
        for _ in range(3):
            for url in ('/updates','/dashboard','/sources','/incidents','/incidents/1'):
                assert api.get(url).status_code==200
    with connect(settings) as db: assert before==[dict(r) for r in db.execute('SELECT * FROM sources')]

def test_legacy_migration_removes_matching_preserves_alerts_and_http_state(settings):
    add(settings)
    with connect(settings) as db:
        db.execute('DELETE FROM schema_version WHERE version=5')
        db.execute('INSERT OR IGNORE INTO schema_version VALUES(4)')
        db.execute('CREATE TABLE companies(siren TEXT)')
        db.execute('CREATE TABLE identity_jobs(id INTEGER)')
        db.execute('CREATE VIEW incident_company_resolutions AS SELECT * FROM companies')
        db.execute("INSERT INTO sources(id,name,feed_url,kind) VALUES('recherche-entreprises','API','https://example.org/api','company_search')")
        db.execute("UPDATE sources SET next_poll_at=2000000000,retry_not_before=1900000000,etag='keep' WHERE id='bonjour-la-fuite'")
    initialize(settings)
    initialize(settings)
    with connect(settings) as db:
        names={r[0] for r in db.execute('SELECT name FROM sqlite_master')}
        assert not names & {'companies','identity_jobs','incident_company_resolutions'}
        assert db.execute('SELECT COUNT(*) FROM observations').fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM sources').fetchone()[0]==4
        assert tuple(db.execute("SELECT next_poll_at,retry_not_before,etag FROM sources WHERE id='bonjour-la-fuite'").fetchone())==(2000000000,1900000000,'keep')
        assert not db.execute('PRAGMA foreign_key_check').fetchall()

def test_regrouping_preserves_publications_aliases_and_activity(settings):
    add(settings)
    with connect(settings) as db: db.execute('DELETE FROM incident_grouping_aliases')
    add(settings,'BilletsNantes','b','frenchbreaches')
    with connect(settings) as db:
        for row in db.execute('SELECT id,organization FROM incidents').fetchall(): queue_grouping(db,row['id'],row['organization'])
    assert repair_duplicate_incidents(settings)==[{'old_id':2,'incident_id':1}]
    with TestClient(create_app(settings)) as api:
        assert api.get('/incidents/2').json()['id']==1
        assert api.get('/incidents').json()['items'][0]['observation_count']==2
    with connect(settings) as db:
        archived=json.loads(db.execute('SELECT snapshot FROM incident_merges').fetchone()[0])
        assert set(archived)=={'incidents','observations'}
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
