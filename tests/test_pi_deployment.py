import hashlib
import json
from pathlib import Path
import sqlite3
import tarfile

import pytest

from csirt.config import Settings
from csirt.db import connect
from scripts import package_pi, pi_admin
from scripts.verify_export import verify


def test_backup_includes_committed_wal_and_refuses_overwrite(tmp_path):
    source = tmp_path / 'live.sqlite3'
    destination = tmp_path / 'snapshots' / 'copy.sqlite3'
    db = sqlite3.connect(source)
    try:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE example(value)')
        db.execute("INSERT INTO example VALUES ('committed')")
        db.commit()
        pi_admin.backup_database(source, destination)
        with sqlite3.connect(destination) as copy:
            assert copy.execute('SELECT * FROM example').fetchall() == [('committed',)]
            assert copy.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        before = destination.read_bytes()
        with pytest.raises(ValueError, match='existe déjà'):
            pi_admin.backup_database(source, destination)
        assert destination.read_bytes() == before
    finally:db.close()


def test_invalid_database_does_not_leave_backup_or_temporary(tmp_path):
    source = tmp_path / 'broken.sqlite3'
    source.write_bytes(b'not a SQLite database')
    destination = tmp_path / 'snapshots' / 'copy.sqlite3'
    with pytest.raises(sqlite3.DatabaseError):pi_admin.backup_database(source, destination)
    assert list(destination.parent.iterdir()) == []


def test_prepare_imports_seed_once_and_backs_up_existing_data(settings, tmp_path):
    root = tmp_path / 'pi-state'
    target = Settings(db_path=root / 'csirt.sqlite3')
    first = pi_admin.prepare_database(target, settings.db_path, root)
    assert first['seed_imported'] and first['backup'] is None
    with connect(target) as db:
        db.execute("CREATE TABLE local_test(value)")
        db.execute("INSERT INTO local_test VALUES ('preserve me')")
        db.execute('UPDATE sources SET next_poll_at=9876543210')
    second = pi_admin.prepare_database(target, settings.db_path, root)
    assert not second['seed_imported']
    with sqlite3.connect(second['backup']) as copy:
        assert copy.execute('SELECT value FROM local_test').fetchone()[0] == 'preserve me'
        assert copy.execute('SELECT MIN(next_poll_at) FROM sources').fetchone()[0] == 9876543210
    with connect(target) as db:
        assert db.execute('SELECT value FROM local_test').fetchone()[0] == 'preserve me'


def test_prepare_rejects_database_outside_state_root(tmp_path):
    settings = Settings(db_path=tmp_path / 'outside.sqlite3')
    with pytest.raises(ValueError, match='doit rester sous'):
        pi_admin.prepare_database(settings, None, tmp_path / 'state')
    assert not settings.db_path.exists()


def test_prepare_without_seed_creates_empty_valid_database(tmp_path):
    root = tmp_path / 'state'
    settings = Settings(db_path=root / 'csirt.sqlite3')
    result = pi_admin.prepare_database(settings, tmp_path / 'missing.sqlite3', root)
    assert result['incidents'] == result['publications'] == 0
    assert result['seed_imported'] is False


def test_health_requires_live_production_collector(monkeypatch):
    import io
    healthy = {'status': 'ok', 'mode': 'production', 'collector': {'active': True}}
    monkeypatch.setattr(pi_admin.urllib.request, 'urlopen', lambda *a, **k: io.StringIO(json.dumps(healthy)))
    assert pi_admin.wait_for_health('http://localhost/health', 0) == healthy
    healthy['collector']['active'] = False
    with pytest.raises(ValueError, match='indisponible'):
        pi_admin.wait_for_health('http://localhost/health', 0)


def test_export_roundtrip_integrity_permissions_and_database(settings, tmp_path, monkeypatch):
    monkeypatch.setenv('CSIRT_DB_PATH', str(settings.db_path))
    archive_path = tmp_path / 'export.tar.gz'
    release = package_pi.create_package(archive_path, with_database=True)
    assert release['database_included'] and release['database_counts']['incidents'] == 0
    checksum = archive_path.with_name(archive_path.name + '.sha256').read_text().split()[0]
    assert checksum == hashlib.sha256(archive_path.read_bytes()).hexdigest()
    extracted = tmp_path / 'extracted'
    with tarfile.open(archive_path) as archive:
        names = archive.getnames()
        assert 'csirt/data/csirt-backup.sqlite3' in names
        assert not any('/.venv' in n or '/.research/' in n or '/__pycache__/' in n or n.endswith('.pyc') for n in names)
        assert not any(n.endswith('.json') and '/docs/' in n for n in names)
        assert all(member.isfile() and member.name.startswith('csirt/') for member in archive)
        for name in ('csirt/install.sh', 'csirt/deploy/raspberry/install.sh'):
            assert archive.getmember(name).mode == 0o755
            assert b'\r' not in archive.extractfile(name).read()
        for member in archive:
            target = (extracted / member.name).resolve()
            assert target.is_relative_to(extracted.resolve())
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.extractfile(member).read())
    root = extracted / 'csirt'
    assert verify(root) > 25
    with sqlite3.connect(root / 'data/csirt-backup.sqlite3') as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    (root / 'csirt/api.py').write_bytes(b'modified')
    with pytest.raises(ValueError, match='modifié'):verify(root)


def test_source_only_export_and_wrong_wheels(settings, tmp_path, monkeypatch):
    monkeypatch.setenv('CSIRT_DB_PATH', str(tmp_path / 'absent.sqlite3'))
    archive_path = tmp_path / 'empty.tar.gz'
    assert not package_pi.create_package(archive_path)['database_included']
    with tarfile.open(archive_path) as archive:
        assert 'csirt/data/csirt-backup.sqlite3' not in archive.getnames()
    wheelhouse = tmp_path / 'wheels'
    wheelhouse.mkdir()
    (wheelhouse / 'WHEELHOUSE.json').write_text(json.dumps({
        'platform': 'linux_armv7l', 'python_versions': ['3.11'], 'requirements_sha256': 'wrong',
    }))
    before = archive_path.read_bytes()
    with pytest.raises(ValueError, match='verrou'):package_pi.create_package(archive_path, wheelhouse=wheelhouse)
    assert archive_path.read_bytes() == before


def test_wheelhouse_detects_modified_download(tmp_path):
    wheel = tmp_path / 'example-py3-none-any.whl'
    wheel.write_bytes(b'example')
    manifest = {'platform': 'linux_armv7l', 'python_versions': ['3.11'],
                'requirements_sha256': hashlib.sha256((package_pi.ROOT / 'deploy/raspberry/requirements.lock').read_bytes()).hexdigest(),
                'files': {wheel.name: hashlib.sha256(wheel.read_bytes()).hexdigest()}}
    (tmp_path / 'WHEELHOUSE.json').write_text(json.dumps(manifest))
    assert package_pi.validate_wheelhouse(tmp_path)['python_versions'] == ['3.11']
    wheel.write_bytes(b'changed')
    with pytest.raises(ValueError, match='modifiée'):package_pi.validate_wheelhouse(tmp_path)
