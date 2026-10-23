from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from csirt import api as api_module
from csirt.collector import store_entry
from csirt.db import connect
from csirt.feeds import Entry
from csirt.reporting import reporting_period


@pytest.mark.parametrize('now,start,end', [
    ('2026-10-23T00:00:00+02:00', '2026-10-22T15:00:00+00:00', '2026-10-23T15:00:00+00:00'),
    ('2026-10-23T16:59:59+02:00', '2026-10-22T15:00:00+00:00', '2026-10-23T15:00:00+00:00'),
    ('2026-10-23T17:00:00+02:00', '2026-10-22T15:00:00+00:00', '2026-10-23T15:00:00+00:00'),
    ('2026-10-23T19:59:59+02:00', '2026-10-22T15:00:00+00:00', '2026-10-23T15:00:00+00:00'),
    ('2026-10-23T20:00:00+02:00', '2026-10-23T15:00:00+00:00', '2026-10-24T15:00:00+00:00'),
    ('2026-10-24T00:00:00+02:00', '2026-10-23T15:00:00+00:00', '2026-10-24T15:00:00+00:00'),
    ('2026-01-01T08:00:00+00:00', '2025-12-31T16:00:00+00:00', '2026-01-01T16:00:00+00:00'),
    ('2026-10-23T18:00:00+00:00', '2026-10-23T15:00:00+00:00', '2026-10-24T15:00:00+00:00'),
])
def test_reporting_period_keeps_daily_report_until_20_paris(now, start, end):
    period = reporting_period(datetime.fromisoformat(now))
    assert (period['starts_at'], period['ends_at']) == (start, end)
    assert period['timezone'] == 'Europe/Paris'
    assert period['end_exclusive']


@pytest.mark.parametrize('now,hours', [
    ('2026-03-29T12:00:00+02:00', 23),
    ('2026-10-25T12:00:00+01:00', 25),
])
def test_reporting_day_tracks_clock_changes_instead_of_fixed_24_hours(now, hours):
    period = reporting_period(datetime.fromisoformat(now))
    assert datetime.fromisoformat(period['start_local']).hour == 17
    assert datetime.fromisoformat(period['end_local']).hour == 17
    delta = datetime.fromisoformat(period['ends_at']) - datetime.fromisoformat(period['starts_at'])
    assert delta.total_seconds() == hours * 3600


def freeze(monkeypatch, now):
    period = reporting_period(datetime.fromisoformat(now))
    monkeypatch.setattr(api_module, 'reporting_period', lambda: period)
    return period


def add(settings, name, guid, date, source='bonjour-la-fuite', status='claimed'):
    entry = Entry(guid, 'https://example.org/' + guid, name, '', date,
                  '2026-10-23T12:00:00+00:00', name, 'data_breach', status, [])
    with connect(settings) as db:
        store_entry(db, entry, source, '2026-10-23T12:00:00+00:00')


def test_dashboard_uses_publication_period_and_deduplicates_incidents(settings, monkeypatch):
    period = freeze(monkeypatch, '2026-10-23T19:00:00+02:00')
    add(settings, 'Before', 'before', '2026-10-22T14:59:59+00:00')
    add(settings, 'Boundary', 'start', period['starts_at'], status='confirmed')
    add(settings, 'Boundary', 'second', '2026-10-22T16:00:00+00:00', 'frenchbreaches')
    add(settings, 'Crossing', 'old', '2026-10-22T14:00:00+00:00', status='confirmed')
    add(settings, 'Crossing', 'new', '2026-10-22T16:00:00+00:00', 'frenchbreaches')
    add(settings, 'Last second', 'last', '2026-10-23T14:59:59+00:00', status='unreliable_claim')
    add(settings, 'At end', 'end', period['ends_at'])
    add(settings, 'Undated', 'no-date', None)
    add(settings, 'After end', 'after', '2026-10-23T16:00:00+00:00')
    with TestClient(api_module.create_app(settings)) as client:
        result = client.get('/dashboard').json()
        assert result['period'] == period
        assert result['incidents'] == 3 and result['observations'] == 4
        assert result['multiple_sources'] == 1
        assert {s['status']: s['count'] for s in result['statuses']} == {
            'confirmed': 1, 'claimed': 1, 'unreliable_claim': 1,
        }
        assert sorted(s['count'] for s in result['by_source']) == [0, 0, 2, 2]
        assert sum(s['count'] for s in result['statuses']) == result['incidents']
        assert sum(s['count'] for s in result['by_source']) == result['observations']
        # L'historique et le suivi des nouveautés gardent leur propre périmètre.
        assert result['sources'] == 4
        assert result['total_incidents'] == client.get('/incidents').json()['total'] == 7
        assert client.get('/updates?since=0').json()['new_count'] == 7


def test_counter_switch_is_published_without_database_changes(settings, monkeypatch):
    add(settings, 'Prior report', 'old', '2026-10-22T15:00:00+00:00')
    add(settings, 'Next report', 'new', '2026-10-23T15:00:00+00:00')
    with TestClient(api_module.create_app(settings)) as client:
        freeze(monkeypatch, '2026-10-23T16:59:59+02:00')
        before_17 = client.get('/updates').json()
        freeze(monkeypatch, '2026-10-23T19:59:59+02:00')
        held = client.get('/dashboard').json()
        assert held['snapshot']['revision'] == before_17['revision']
        freeze(monkeypatch, '2026-10-23T20:00:00+02:00')
        next_report = client.get('/dashboard').json()
        assert next_report['snapshot']['revision'] != held['snapshot']['revision']
        assert next_report['snapshot']['sequence'] == before_17['sequence']
        assert next_report['period']['starts_at'] == held['period']['ends_at']
        assert next_report['incidents'] == held['incidents'] == 1
        assert next_report['observations'] == held['observations'] == 1
        freeze(monkeypatch, '2026-10-24T00:00:00+02:00')
        assert client.get('/updates').json()['revision'] == next_report['snapshot']['revision']


def test_empty_period_keeps_global_history(settings, monkeypatch):
    freeze(monkeypatch, '2026-10-23T12:00:00+02:00')
    add(settings, 'Historical alert', 'old', '2026-01-01T10:00:00+00:00')
    with TestClient(api_module.create_app(settings)) as client:
        result = client.get('/dashboard').json()
        assert result['incidents'] == result['observations'] == result['multiple_sources'] == 0
        assert result['statuses'] == [] and all(s['count'] == 0 for s in result['by_source'])
        assert result['total_incidents'] == 1
