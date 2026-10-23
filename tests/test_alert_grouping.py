from dataclasses import replace
import pytest
from fastapi.testclient import TestClient
from csirt.api import create_app
from csirt.collector import store_entry
from csirt.db import connect
from csirt.feeds import Entry
from csirt.grouping import aliases,queue_grouping,repair_duplicate_incidents

def publication(name,guid='a',date='2026-10-20T10:00:00+00:00',summary='Fuite de données',updated=None,kind='data_breach',title=None):
    return Entry(guid,'https://example.org/'+guid,title or name,summary,date,updated,name,kind,'claimed',[])

def store(settings,entry,source='bonjour-la-fuite',*,separate=False):
    with connect(settings) as db:
        if separate: db.execute('DELETE FROM incident_grouping_aliases')
        store_entry(db,entry,source,'2026-10-22T14:00:00+00:00')
        for row in db.execute('SELECT id,organization FROM incidents').fetchall():queue_grouping(db,row['id'],row['organization'])

@pytest.mark.parametrize('a,b',[
    ('La Compagnie des Sens','Compagnie des Sens'),
    ('Eau Cœur d’Essonne',"Eau coeur d'Essonne"),
    ("Institut national d’études démographiques (Ined)",'INED'),
    ("École d’ingénieurs du numérique (ISEP)",'ISEP'),
    ('PRREL Ambition (Région Hauts-de-France)','PRREL Ambition'),
    ('Société Centrale Canine','Centrale Canine (LOF)'),
    ('Réseau ZOU ! (Région Sud)','Zou ! (Région Sud)'),
])
def test_explicit_name_variants_merge_during_collection(settings,a,b):
    store(settings,publication(a))
    store(settings,publication(b,'b'),'frenchbreaches')
    with connect(settings) as db:
        assert db.execute('SELECT COUNT(*) FROM incidents').fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM observations').fetchone()[0]==2

def test_existing_variants_are_reprocessed_and_old_links_survive(settings):
    store(settings,publication('INED'))
    store(settings,publication("Institut national d’études démographiques (Ined)",'b'),'frenchbreaches',separate=True)
    assert repair_duplicate_incidents(settings)==[{'old_id':2,'incident_id':1}]
    assert repair_duplicate_incidents(settings)==[]
    with TestClient(create_app(settings)) as api:
        assert api.get('/incidents/2').json()['id']==1

def test_delayed_source_cannot_bypass_48h_even_with_update_and_shared_context(settings):
    store(settings,publication('Fédération Française de Tir (FFTIR)',date='2026-10-17T20:25:00+00:00',
          summary='La fédération confirme le vol visant EDEN et Cibles Couleurs.',updated='2026-10-22T12:03:00+00:00',kind='cyberattack'))
    store(settings,publication('Fédération Française de Tir','b','2026-10-22T00:00:00+00:00',summary='Club Grade Cibles Couleurs Pratique handisport'),'frenchbreaches')
    with connect(settings) as db:assert db.execute('SELECT COUNT(*) FROM incidents').fetchone()[0]==2

@pytest.mark.parametrize('second',[
    publication('Atelier Azur','b','2026-10-24T10:00:00+00:00'),
    publication('Atelier Azur','b','2026-11-10T10:00:00+00:00',updated='2026-11-10T12:00:00+00:00'),
    publication('Atelier Azur (Lyon)','b'),
    publication('AZ','b'),
    publication('Autre Atelier','b'),
])
def test_dates_qualifiers_and_unsupported_acronyms_do_not_merge(settings,second):
    store(settings,publication('Atelier Azur (Paris)'))
    store(settings,second,'frenchbreaches')
    assert repair_duplicate_incidents(settings)==[]
    with connect(settings) as db: assert db.execute('SELECT COUNT(*) FROM incidents').fetchone()[0]==2

def test_recent_update_timestamp_alone_does_not_join_old_incidents(settings):
    store(settings,publication('Atelier Azur',date='2026-10-12T00:00:00+00:00',updated='2026-10-21T12:00:00+00:00'))
    store(settings,publication('Atelier Azur','b','2026-10-21T00:00:00+00:00'),'frenchbreaches')
    with connect(settings) as db:assert db.execute('SELECT COUNT(*) FROM incidents').fetchone()[0]==2

def test_vulnerability_fixed_after_actual_data_exposure_remains_same_incident(settings):
    store(settings,publication('PortaleCo',summary='PortaleCo : les données de 103 000 clients revendiquées par un pirate. Une faille IDOR corrigée.',kind='vulnerability'))
    store(settings,publication('Portaleco','b',summary='Fuite de données du site.'),'frenchbreaches')
    repair_duplicate_incidents(settings)
    with connect(settings) as db:
        assert db.execute('SELECT COUNT(*) FROM incidents').fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM incident_splits').fetchone()[0]==0

def test_ddos_and_data_breach_are_separate_even_with_same_name_and_date(settings):
    store(settings,publication('La Poste Mobile',kind='cyberattack',title='La Poste Mobile : attaque DDoS'))
    store(settings,publication('Poste Mobile','b'),'frenchbreaches')
    assert repair_duplicate_incidents(settings)==[]
    with connect(settings) as db:assert db.execute('SELECT COUNT(*) FROM incidents').fetchone()[0]==2

def test_old_mixed_event_is_split_then_leak_sources_merge(settings):
    store(settings,publication('Poste Mobile',kind='cyberattack',title='La Poste Mobile : attaque DDoS'))
    store(settings,publication('Poste Mobile','b'),'fuites-infos')
    store(settings,publication('La Poste Mobile','c'),'frenchbreaches',separate=True)
    with connect(settings) as db:
        # Reproduire une ancienne fusion du DDoS et d'une fuite, avec sa publication intacte.
        leak=db.execute("SELECT id,incident_id FROM observations WHERE source_id='fuites-infos'").fetchone()
        old=leak['incident_id']
        db.execute('UPDATE observations SET incident_id=1 WHERE id=?',(leak['id'],))
        db.execute('DELETE FROM incidents WHERE id=?',(old,))
        queue_grouping(db,1,'Poste Mobile')
    for _ in range(3):repair_duplicate_incidents(settings)
    with connect(settings) as db:
        assert db.execute('SELECT COUNT(*) FROM incidents').fetchone()[0]==2
        assert db.execute('SELECT COUNT(*) FROM observations').fetchone()[0]==3
        assert db.execute('SELECT COUNT(*) FROM incident_splits').fetchone()[0]==1
        assert db.execute("SELECT COUNT(DISTINCT incident_id) FROM observations WHERE source_id IN ('fuites-infos','frenchbreaches')").fetchone()[0]==1
        assert not db.execute('PRAGMA foreign_key_check').fetchall()

def test_parenthetical_parent_is_not_an_alias_of_the_victim():
    assert not aliases('TrainMe (Decathlon)')&aliases('Decathlon')
    assert not aliases('Service Azur (Région Sud)')&aliases('Région Sud')

def test_ambiguous_acronym_does_not_bridge_two_entities(settings):
    store(settings,publication('Agence Bleue Centrale (ABC)'))
    store(settings,publication('Agence Blanche Culturelle (ABC)','b'),'frenchbreaches',separate=True)
    store(settings,publication('ABC','c'),'fuites-infos')
    repair_duplicate_incidents(settings)
    with connect(settings) as db: assert db.execute('SELECT COUNT(*) FROM incidents').fetchone()[0]==3

@pytest.mark.parametrize('date,expected',[
    ('2026-10-22T09:59:59+00:00',1),
    ('2026-10-22T10:00:00+00:00',2),
    ('2026-10-22T10:00:01+00:00',2),
    ('2026-10-22T11:59:59+02:00',1),
    ('2026-10-22T12:00:00+02:00',2),
])
def test_strict_48h_boundary_uses_real_elapsed_time(settings,date,expected):
    store(settings,publication('Atelier Azur'))
    store(settings,publication('Atelier Azur','b',date),'frenchbreaches')
    repair_duplicate_incidents(settings)
    with connect(settings) as db:assert db.execute('SELECT COUNT(*) FROM incidents').fetchone()[0]==expected

def test_chain_of_publications_cannot_extend_total_48h_span(settings):
    store(settings,publication('Atelier Azur',date='2026-10-20T00:00:00+00:00'))
    store(settings,publication('Atelier Azur','b','2026-10-21T23:00:00+00:00'),'frenchbreaches')
    store(settings,publication('Atelier Azur','c','2026-10-23T22:00:00+00:00'),'fuites-infos')
    repair_duplicate_incidents(settings)
    with connect(settings) as db:
        assert db.execute('SELECT COUNT(*) FROM incidents').fetchone()[0]==2
        assert db.execute('SELECT COUNT(*) FROM observations').fetchone()[0]==3

def test_legacy_late_merge_is_split_and_old_links_follow_their_publication(settings):
    from csirt.grouping import merge_incidents,canonical_incident_id
    store(settings,publication('Atelier Azur'))
    store(settings,publication('Atelier Azur','b','2026-10-25T10:00:00+00:00'),'frenchbreaches')
    with connect(settings) as db:merge_incidents(db,1,2)
    repair_duplicate_incidents(settings)
    with connect(settings) as db:
        assert db.execute('SELECT COUNT(*) FROM incidents').fetchone()[0]==2
        assert db.execute('SELECT COUNT(*) FROM observations').fetchone()[0]==2
        target=canonical_incident_id(db,2)
        assert target!=1
        assert db.execute("SELECT incident_id FROM observations WHERE external_id='b'").fetchone()[0]==target
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
