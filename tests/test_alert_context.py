import pytest
from csirt.db import connect
from csirt.grouping import repair_duplicate_incidents
from test_alert_grouping import publication,store


@pytest.mark.parametrize('misspelled,correct',[
    ('Icantec (retraites)','Ircantec'),
    ('Novatel','Novatell'),
    ('Laboratoire Eliza','Laboratoire Elisa'),
])
def test_variant_is_named_in_leading_summary_and_is_one_typo_away(settings,misspelled,correct):
    store(settings,publication(misspelled,summary=correct+' : une fuite de données revendiquée.'))
    store(settings,publication(correct,'b'),'frenchbreaches')
    with connect(settings) as db:
        assert db.execute('SELECT COUNT(*) FROM incidents').fetchone()[0]==1
        assert {r[0] for r in db.execute('SELECT organization FROM observations')}=={misspelled,correct}


@pytest.mark.parametrize('summary,name',[
    ('Une fuite de données revendiquée.','Ircantec'),
    ('Une fuite de données évoque Ircantec en fin de texte.','Ircantec'),
    ('Ircantec : une fuite de données revendiquée.','Autre Caisse'),
])
def test_similarity_or_body_mention_alone_does_not_merge(settings,summary,name):
    store(settings,publication('Icantec (retraites)',summary=summary))
    store(settings,publication(name,'b'),'frenchbreaches')
    repair_duplicate_incidents(settings)
    with connect(settings) as db:assert db.execute('SELECT COUNT(*) FROM incidents').fetchone()[0]==2


def test_short_near_names_are_not_treated_as_typographical_aliases(settings):
    store(settings,publication('ABC',summary='ABD : fuite de données.'))
    store(settings,publication('ABD','b'),'frenchbreaches')
    with connect(settings) as db:assert db.execute('SELECT COUNT(*) FROM incidents').fetchone()[0]==2


def test_service_explicitly_targeted_under_its_parent_can_merge(settings):
    store(settings,publication('Région Sud',summary='Une base attribuée à ZOU!, le réseau de transport de la Région Sud, est mise en vente.',
          title='Région Sud : 89 083 dossiers d’usagers de ZOU! mis en vente'))
    store(settings,publication('Réseau ZOU ! (Région Sud)','b'),'frenchbreaches',separate=True)
    store(settings,publication('Zou ! (Région Sud)','c'),'fuites-infos',separate=True)
    for _ in range(2):repair_duplicate_incidents(settings)
    with connect(settings) as db:
        assert db.execute('SELECT COUNT(*) FROM incidents').fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM observations').fetchone()[0]==3
        assert 'ZOU' in db.execute('SELECT organization FROM incidents').fetchone()[0]


@pytest.mark.parametrize('title,summary',[
    ('Région Sud : une fuite de données','Le réseau ZOU! est également cité dans les informations régionales.'),
    ('Région Sud : fuite chez un fournisseur','Les données de la région sont hébergées par ZOU!.'),
])
def test_same_parent_and_incidental_service_mention_are_not_enough(settings,title,summary):
    store(settings,publication('Région Sud',title=title,summary=summary))
    store(settings,publication('Réseau ZOU ! (Région Sud)','b'),'frenchbreaches')
    repair_duplicate_incidents(settings)
    with connect(settings) as db:assert db.execute('SELECT COUNT(*) FROM incidents').fetchone()[0]==2


def test_context_cannot_merge_services_with_different_names_or_locations(settings):
    store(settings,publication('Service Azur (Région Sud)'))
    store(settings,publication('Service Vert (Région Sud)','b'),'frenchbreaches')
    repair_duplicate_incidents(settings)
    with connect(settings) as db:assert db.execute('SELECT COUNT(*) FROM incidents').fetchone()[0]==2


def test_attested_variant_and_service_context_still_require_less_than_48h(settings):
    store(settings,publication('Icantec (retraites)',summary='Ircantec : une fuite de données.'))
    store(settings,publication('Ircantec','b','2026-10-22T10:00:00+00:00'),'frenchbreaches')
    repair_duplicate_incidents(settings)
    with connect(settings) as db:assert db.execute('SELECT COUNT(*) FROM incidents').fetchone()[0]==2
