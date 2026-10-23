"""Bilan de veille de 17 h à 17 h, basculé à 20 h en heure de Paris."""
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

PARIS = ZoneInfo('Europe/Paris')


def reporting_period(now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('Un instant avec fuseau horaire est requis')
    local = now.astimezone(PARIS)
    day = local.date() + timedelta(days=local.time() >= time(20))
    start = datetime.combine(day - timedelta(days=1), time(17), PARIS)
    end = datetime.combine(day, time(17), PARIS)
    return {
        'starts_at': start.astimezone(timezone.utc).isoformat(timespec='seconds'),
        'ends_at': end.astimezone(timezone.utc).isoformat(timespec='seconds'),
        'start_local': start.isoformat(timespec='seconds'),
        'end_local': end.isoformat(timespec='seconds'),
        'switches_at': datetime.combine(day, time(20), PARIS).astimezone(timezone.utc).isoformat(timespec='seconds'),
        'timezone': 'Europe/Paris', 'date_basis': 'published_at', 'end_exclusive': True,
    }
