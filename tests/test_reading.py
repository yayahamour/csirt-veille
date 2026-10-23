from dataclasses import replace
import pytest
from fastapi.testclient import TestClient

from csirt.api import create_app
from csirt.collector import store_entry
from csirt.db import connect
from csirt.feeds import Entry


def add(settings, name, guid=None, summary='Information initiale'):
    entry = Entry(guid or name, 'https://example.org/' + (guid or name), name, summary,
                  '2026-10-23T10:00:00+00:00', None, name, 'data_breach', 'claimed', [])
    with connect(settings) as db:
        store_entry(db, entry, 'bonjour-la-fuite', '2026-10-23T10:10:00+00:00')
    return entry


def profile(api, sequence=0):
    return {'generation': api.get('/updates').json()['generation'], 'sequence': sequence, 'seen': {}}


def test_each_browser_has_independent_progress_and_updates_reopen_alert(settings):
    first = add(settings, 'Alpha')
    add(settings, 'Bravo')
    with TestClient(create_app(settings)) as api:
        alice = profile(api)
        bob = profile(api)
        items = api.get('/incidents?sort=organization&order=asc').json()['items']
        alpha = items[0]
        alice['seen'][str(alpha['id'])] = alpha['activity_seq']
        assert api.post('/reading/updates', json=alice).json()['new_count'] == 1
        assert api.post('/reading/updates', json=bob).json()['new_count'] == 2
        assert api.post('/reading/incidents', json=alice).json()['items'][0]['organization'] == 'Bravo'
        with connect(settings) as db:
            store_entry(db, replace(first, summary='Nouvelle information'), 'bonjour-la-fuite', '2026-10-23T10:20:00+00:00')
        assert api.post('/reading/updates', json=alice).json()['new_count'] == 2
        # Le chargement du détail par un autre navigateur ne modifie aucun profil.
        assert api.get(f'/incidents/{alpha["id"]}').status_code == 200
        assert api.post('/reading/updates', json=bob).json()['new_count'] == 2


def test_personal_unread_filter_precedes_sorting_and_pagination(settings):
    for name in ['Alpha', 'Bravo', 'Charlie', 'Delta']:add(settings, name)
    with TestClient(create_app(settings)) as api:
        state = profile(api)
        all_items = api.get('/incidents?sort=organization&order=asc').json()['items']
        state['seen'] = {str(item['id']): item['activity_seq'] for item in all_items[:2]}
        page = api.post('/reading/incidents?sort=organization&order=asc&limit=1&offset=1', json=state).json()
        assert page['total'] == page['snapshot']['new_count'] == 2
        assert [item['organization'] for item in page['items']] == ['Delta']
        assert api.post('/reading/incidents?q=Charlie', json=state).json()['total'] == 1
        assert api.post('/reading/incidents?source=frenchbreaches', json=state).json()['total'] == 0
        assert api.get('/incidents').json()['total'] == 4


def test_mark_all_only_acknowledges_snapshot_not_later_arrivals(settings):
    add(settings, 'Alpha')
    with TestClient(create_app(settings)) as api:
        snapshot = api.get('/updates').json()
        state = profile(api, snapshot['sequence'])
        add(settings, 'Later')
        assert api.post('/reading/updates', json=state).json()['new_count'] == 1
        assert api.post('/reading/incidents', json=state).json()['items'][0]['organization'] == 'Later'


def test_reading_requests_are_not_persisted_and_do_not_change_source_schedule(settings):
    add(settings, 'Alpha')
    with TestClient(create_app(settings)) as api:
        state = profile(api)
        with connect(settings) as db:
            before = {table: [tuple(row) for row in db.execute('SELECT * FROM ' + table)]
                      for table in ['sources', 'incidents', 'alert_state']}
        for _ in range(3):
            assert api.post('/reading/updates', json=state).status_code == 200
            assert api.post('/reading/incidents', json=state).status_code == 200
        with connect(settings) as db:
            after = {table: [tuple(row) for row in db.execute('SELECT * FROM ' + table)] for table in before}
            assert not db.execute("SELECT name FROM sqlite_master WHERE name IN ('users','reading','reader_profiles')").fetchall()
        assert before == after


def test_wrong_database_generation_cannot_hide_new_database_alerts(settings):
    add(settings, 'Alpha')
    with TestClient(create_app(settings)) as api:
        state = {'generation': 'old-database', 'sequence': 999, 'seen': {'1': 999}}
        result = api.post('/reading/updates', json=state).json()
        assert result['generation'] != state['generation']
        assert result['sequence'] < state['sequence']
        assert result['new_count'] == 0  # Le client réinitialise son repère à cette photographie.


@pytest.mark.parametrize('change', [
    {'sequence': -1}, {'sequence': True}, {'sequence': 9007199254740992},
    {'seen': {'oops': 2}}, {'seen': {'1': '2'}}, {'seen': {'1': -1}},
    {'user_id': 'someone-else'}, {'seen': {str(i): 1 for i in range(1, 10002)}},
])
def test_local_reading_input_is_bounded_and_validated(settings, change):
    with TestClient(create_app(settings)) as api:
        state = {**profile(api), **change}
        assert api.post('/reading/updates', json=state).status_code == 422


def test_large_read_state_uses_one_bound_json_parameter(settings):
    add(settings, 'Alpha')
    with TestClient(create_app(settings)) as api:
        state = profile(api)
        state['seen'] = {str(i): 100 for i in range(1, 2001)}
        result = api.post('/reading/updates', json=state)
        assert result.status_code == 200
        assert result.json()['new_count'] == 0
