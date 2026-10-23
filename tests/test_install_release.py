from contextlib import closing
import hashlib
import io
import shutil
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile

import pytest

from scripts.install_release import SERVICES, database_path, install_release, install_local
from scripts.package_pi import ROOT, create_package
from scripts.verify_export import verify


@pytest.fixture
def release(tmp_path):
    output = tmp_path / 'download' / 'csirt-pi2.tar.gz'
    create_package(output)
    source = tmp_path / 'source'
    with tarfile.open(output) as archive:
        for member in archive:
            target = source / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.extractfile(member).read())
            target.chmod(member.mode)
    return source / 'csirt'


def locations(tmp_path):
    root = tmp_path / 'device'
    return {'app': root / 'opt/csirt', 'config': root / 'etc/csirt', 'state': root / 'var/lib/csirt',
            'backups': root / 'var/backups/csirt', 'units': root / 'etc/systemd/system',
            'command_dir': root / 'usr/local/bin'}


class Services:
    def __init__(self, locations, on_install=lambda: None):
        self.locations = locations
        self.active = set()
        self.enabled = set()
        self.on_install = on_install
        self.commands = []

    def run(self, command, **kwargs):
        self.commands.append(command)
        result = 0
        if command[0] == 'bash':
            assert command[1] == str(self.locations['app'] / 'install.sh')
            assert command[2:] in (['--skip-system-packages'], ['--skip-system-packages', '--offline'])
            self.on_install()
        else:
            assert command[0] == 'systemctl'
            operation = command[1]
            service = command[-1]
            if operation == 'is-active':result = 0 if service in self.active else 3
            elif operation == 'is-enabled':result = 0 if service in self.enabled else 1
            elif operation == 'cat':result = 0 if (self.locations['units'] / service).is_file() else 1
            elif operation == 'stop':self.active.discard(service)
            elif operation == 'start':self.active.add(service)
            elif operation == 'enable':self.enabled.add(service)
            elif operation == 'disable':self.enabled.discard(service)
        return subprocess.CompletedProcess(command, result)


def previous_install(paths):
    (paths['app'] / 'csirt').mkdir(parents=True)
    (paths['app'] / 'csirt/api.py').write_text('previous code')
    (paths['app'] / 'marker').write_text('restore this directory')
    paths['config'].mkdir(parents=True)
    paths['state'].mkdir(parents=True)
    database = paths['state'] / 'custom.sqlite3'
    with closing(sqlite3.connect(database)) as db:
        db.execute('CREATE TABLE important(value)')
        db.execute("INSERT INTO important VALUES ('original')")
        db.commit()
    (paths['config'] / 'csirt.env').write_text(f'CSIRT_DB_PATH="{database.as_posix()}"\n')
    (paths['config'] / 'sources.toml').write_text('keep source configuration')
    paths['units'].mkdir(parents=True)
    for service in SERVICES:(paths['units'] / service).write_text('previous unit')
    return database


def test_first_install_places_verified_release_and_update_command(release, tmp_path):
    paths = locations(tmp_path)
    services = Services(paths)
    result = install_release(release, **paths, run=services.run)
    assert result['previous_code'] is None
    assert verify(paths['app']) > 25
    assert (paths['command_dir'] / 'csirt-update').read_bytes() == (release / 'bootstrap.sh').read_bytes()
    assert Path(result['backup']).is_dir()


def test_upgrade_backs_up_before_installing_and_preserves_settings(release, tmp_path):
    paths = locations(tmp_path)
    database = previous_install(paths)
    before_config = (paths['config'] / 'csirt.env').read_bytes()
    services = Services(paths)
    services.active = services.enabled = set(SERVICES)
    result = install_release(release, **paths, run=services.run)
    assert (Path(result['previous_code']) / 'marker').is_file()
    assert (paths['config'] / 'csirt.env').read_bytes() == before_config
    for file in [database, Path(result['backup']) / 'database.sqlite3']:
        with closing(sqlite3.connect(file)) as db:
            assert db.execute('SELECT value FROM important').fetchone()[0] == 'original'
    assert services.commands.index(['systemctl', 'stop', SERVICES[0]]) < next(
        index for index, command in enumerate(services.commands) if command[0] == 'bash')


def test_failed_upgrade_restores_code_database_configuration_and_services(release, tmp_path):
    paths = locations(tmp_path)
    database = previous_install(paths)
    original_config = (paths['config'] / 'csirt.env').read_bytes()

    def failed_install():
        with closing(sqlite3.connect(database)) as db:
            db.execute("UPDATE important SET value='modified by failed migration'")
            db.commit()
        (paths['config'] / 'csirt.env').write_text('changed')
        for service in SERVICES:(paths['units'] / service).write_text('new unit')
        raise subprocess.CalledProcessError(1, ['bash', 'install.sh'])

    services = Services(paths, failed_install)
    services.active = {SERVICES[0]}
    services.enabled = {SERVICES[1]}
    with pytest.raises(subprocess.CalledProcessError):install_release(release, **paths, run=services.run)
    assert (paths['app'] / 'marker').read_text() == 'restore this directory'
    assert (paths['config'] / 'csirt.env').read_bytes() == original_config
    assert all((paths['units'] / service).read_text() == 'previous unit' for service in SERVICES)
    with closing(sqlite3.connect(database)) as db:
        assert db.execute('SELECT value FROM important').fetchone()[0] == 'original'
    assert services.active == {SERVICES[0]} and services.enabled == {SERVICES[1]}
    assert list(paths['app'].parent.glob('csirt-failed-*'))
    assert not (paths['command_dir'] / 'csirt-update').exists()


def test_rejects_unrelated_target_before_stopping_services(release, tmp_path):
    paths = locations(tmp_path)
    paths['app'].mkdir(parents=True)
    (paths['app'] / 'unrelated').write_text('keep')
    services = Services(paths)
    with pytest.raises(ValueError, match='reconnue'):install_release(release, **paths, run=services.run)
    assert services.commands == []
    assert (paths['app'] / 'unrelated').read_text() == 'keep'


def test_database_path_rejects_external_database(tmp_path):
    config = tmp_path / 'config'
    config.mkdir()
    (config / 'csirt.env').write_text('CSIRT_DB_PATH=/outside/app.sqlite3\n')
    with pytest.raises(ValueError, match='sous /var/lib/csirt'):database_path(config, tmp_path / 'state')


def extract_download(directory):
    # Exécuter exactement le code de vérification/extraction contenu dans le bootstrap.
    script = (ROOT / 'bootstrap.sh').read_text(encoding='utf-8')
    python = script.split("<<'PY'\n", 1)[1].split('\nPY\n', 1)[0]
    return subprocess.run([sys.executable, '-', str(directory)], input=python.encode('utf-8'),
                          capture_output=True)


@pytest.mark.parametrize('kind', ['traversal', 'symlink', 'duplicate', 'checksum'])
def test_download_rejects_invalid_archive_before_extraction(tmp_path, kind):
    archive = tmp_path / 'csirt-pi2.tar.gz'
    with tarfile.open(archive, 'w:gz') as package:
        info = tarfile.TarInfo('csirt/../../escape' if kind == 'traversal' else 'csirt/file')
        if kind == 'symlink':
            info.type = tarfile.SYMTYPE
            info.linkname = '/outside'
            package.addfile(info)
        else:
            info.size = 4
            package.addfile(info, io.BytesIO(b'test'))
            if kind == 'duplicate':package.addfile(info, io.BytesIO(b'test'))
    digest = '0' * 64 if kind == 'checksum' else hashlib.sha256(archive.read_bytes()).hexdigest()
    (tmp_path / 'csirt-pi2.tar.gz.sha256').write_text(f'{digest}  csirt-pi2.tar.gz\n')
    result = extract_download(tmp_path)
    assert result.returncode != 0
    assert ('Checksum' if kind == 'checksum' else 'Chemin').encode() in result.stderr
    assert not (tmp_path / 'csirt').exists()


def test_valid_public_archive_can_be_downloaded_and_extracted(tmp_path):
    archive = tmp_path / 'csirt-pi2.tar.gz'
    create_package(archive)
    result = extract_download(tmp_path)
    assert result.returncode == 0, result.stderr
    assert verify(tmp_path / 'csirt') > 25
    assert not (tmp_path / 'csirt/data/csirt-backup.sqlite3').exists()


@pytest.mark.parametrize('directory', ['csirt', 'csirt-veille-main', 'csirt dossier extrait'])
def test_local_nested_extraction_is_installed_at_managed_root(release, tmp_path, directory):
    paths = locations(tmp_path)
    extracted = paths['app'] / directory
    extracted.parent.mkdir(parents=True)
    shutil.copytree(release, extracted)
    services = Services(paths)
    result = install_local(extracted, **paths, run=services.run)
    assert verify(paths['app']) > 25
    assert (Path(result['previous_code']) / directory / 'install.sh').is_file()
    assert not (paths['app'] / directory / 'install.sh').exists()


def test_local_git_checkout_is_packaged_without_git_or_secrets(release, tmp_path, monkeypatch):
    from scripts import package_pi
    checkout = tmp_path / 'checkout source'
    shutil.copytree(release, checkout)
    (checkout / 'MANIFEST.sha256').unlink()
    (checkout / '.env').write_text('PRIVATE=must-not-be-copied')
    (checkout / '.git').mkdir()
    (checkout / '.git/config').write_text('local Git configuration')
    monkeypatch.setattr(package_pi, 'ROOT', checkout)
    paths = locations(tmp_path)
    services = Services(paths)
    install_local(checkout, **paths, run=services.run, offline=True)
    assert verify(paths['app']) > 25
    assert not (paths['app'] / '.env').exists() and not (paths['app'] / '.git').exists()
    assert (checkout / '.env').is_file()
    assert next(command for command in services.commands if command[0] == 'bash')[-1] == '--offline'


def test_local_repackaged_nested_checkout_keeps_original_directory(release, tmp_path, monkeypatch):
    from scripts import package_pi
    paths = locations(tmp_path)
    checkout = paths['app'] / 'csirt-veille-main'
    checkout.parent.mkdir(parents=True)
    shutil.copytree(release, checkout)
    (checkout / 'MANIFEST.sha256').unlink()
    monkeypatch.setattr(package_pi, 'ROOT', checkout)
    services = Services(paths)
    result = install_local(checkout, **paths, run=services.run)
    assert verify(paths['app']) > 25
    assert (Path(result['previous_code']) / 'csirt-veille-main/install.sh').is_file()


def test_public_bootstrap_repairs_existing_nested_extraction(release, tmp_path):
    paths = locations(tmp_path)
    previous_source = paths['app'] / 'csirt'
    previous_source.parent.mkdir(parents=True)
    shutil.copytree(release, previous_source)
    services = Services(paths)
    result = install_release(release, **paths, run=services.run)
    assert verify(paths['app']) > 25
    assert (Path(result['previous_code']) / 'csirt/install.sh').is_file()


def test_local_snapshot_is_preserved_but_rejected_for_public_download(settings, tmp_path, monkeypatch):
    monkeypatch.setenv('CSIRT_DB_PATH', str(settings.db_path))
    output = tmp_path / 'local-export.tar.gz'
    create_package(output, with_database=True)
    unpacked = tmp_path / 'unpacked'
    with tarfile.open(output) as archive:
        for member in archive:
            target = unpacked / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.extractfile(member).read())
    source = unpacked / 'csirt'
    paths = locations(tmp_path)
    services = Services(paths)
    with pytest.raises(ValueError, match='sans base de données'):
        install_release(source, **paths, run=services.run)
    assert not services.commands
    install_local(source, **paths, run=services.run)
    with closing(sqlite3.connect(paths['app'] / 'data/csirt-backup.sqlite3')) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert db.execute('SELECT COUNT(*) FROM sources').fetchone()[0] == 4


@pytest.mark.skipif(sys.platform != 'linux' or not shutil.which('bash'), reason='Bash Linux requis')
def test_launcher_resolves_file_symlink_and_spaces_from_other_directory(tmp_path):
    project = tmp_path / 'projet avec espaces'
    installer = project / 'deploy/raspberry/install.sh'
    installer.parent.mkdir(parents=True)
    installer.write_text('printf "LAUNCHER_OK:%s" "$1"\n')
    launcher = project / 'install.sh'
    launcher.write_bytes((ROOT / 'install.sh').read_bytes())
    alias = tmp_path / 'alias.sh'
    alias.symlink_to(launcher)
    result = subprocess.run(['bash', str(alias), '--offline'], cwd=tmp_path,
                            capture_output=True, text=True, check=True)
    assert result.stdout == 'LAUNCHER_OK:--offline'
