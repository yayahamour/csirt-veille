import pytest
from fastapi.testclient import TestClient
from csirt.api import create_app
from csirt.collector import store_entry
from csirt.db import connect
from csirt.feeds import Entry


def seed(settings):
    records=[
        ('Zulu','z','frenchbreaches','2026-10-22T12:00:00+00:00','claimed'),
        ('École Azur','e','cyberattaque','2026-10-21T12:00:00+00:00','confirmed'),
        ('alpha','a','fuites-infos','2026-10-20T12:00:00+00:00','unknown'),
        (None,'b','bonjour-la-fuite',None,'unreliable_claim'),
    ]
    with connect(settings) as db:
        for name,guid,source,date,status in records:
            entry=Entry(guid,'https://example.org/'+guid,name or 'Bravo sans organisme',
                        '',date,None,name,'data_breach',status,[])
            store_entry(db,entry,source,'2026-10-22T12:30:00+00:00')


def names(response):
    assert response.status_code==200,response.text
    return [r['organization'] or r['title'] for r in response.json()['items']]


def test_french_alphabetical_order_applies_before_pagination(settings):
    seed(settings)
    with TestClient(create_app(settings)) as api:
        ascending=names(api.get('/incidents?sort=organization&order=asc'))
        assert ascending==['alpha','Bravo sans organisme','École Azur','Zulu']
        assert names(api.get('/incidents?sort=organization&order=desc'))==ascending[::-1]
        assert names(api.get('/incidents?sort=organization&order=asc&limit=2&offset=2'))==ascending[2:]
        assert names(api.get('/incidents?sort=organization&order=asc&source=cyberattaque'))==['École Azur']
        assert names(api.get('/incidents?sort=organization&order=asc&since_sequence=3'))==['Bravo sans organisme']


def test_date_order_is_chronological_and_missing_dates_stay_last(settings):
    seed(settings)
    with TestClient(create_app(settings)) as api:
        assert names(api.get('/incidents?sort=reference_at&order=asc'))==['alpha','École Azur','Zulu','Bravo sans organisme']
        assert names(api.get('/incidents?sort=reference_at&order=desc'))==['Zulu','École Azur','alpha','Bravo sans organisme']


def test_status_sorts_by_visible_french_label_and_sources_by_name(settings):
    seed(settings)
    with TestClient(create_app(settings)) as api:
        # Confirmé, Non renseigné, Revendication peu fiable, Revendiqué.
        assert names(api.get('/incidents?sort=alert_status&order=asc'))==['École Azur','alpha','Bravo sans organisme','Zulu']
        assert names(api.get('/incidents?sort=source_name&order=asc'))==['Bravo sans organisme','École Azur','Zulu','alpha']
        with connect(settings) as db:
            # Ajouter une source au même incident : le tri utilise la première alphabétique.
            row=db.execute("SELECT * FROM observations WHERE organization='Zulu'").fetchone()
            entry=Entry('other','https://example.org/other','Zulu','',row['published_at'],None,'Zulu','data_breach','claimed',[])
            store_entry(db,entry,'bonjour-la-fuite','2026-10-22T12:31:00+00:00')
        result=api.get('/incidents?sort=source_name&order=asc').json()['items']
        assert result[0]['organization']=='Zulu'
        assert result[0]['sources'][0]['id']=='bonjour-la-fuite'
        assert names(api.get('/incidents?sort=source_name&order=desc'))==['alpha','École Azur','Bravo sans organisme','Zulu']


@pytest.mark.parametrize('query',['sort=title','sort=organization%3BDROP%20TABLE%20incidents','order=invalid'])
def test_sort_keys_and_direction_are_validated(settings,query):
    with TestClient(create_app(settings)) as api:
        assert api.get('/incidents?'+query).status_code==422
