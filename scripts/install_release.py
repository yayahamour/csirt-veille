"""Installation d'une version vérifiée, avec sauvegarde et retour arrière sur échec."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import shutil
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.pi_admin import backup_database
from scripts.verify_export import verify

SERVICES = ('csirt-collector.service', 'csirt-web.service')


def database_path(config, state):
    """Lire la seule valeur nécessaire, sans exécuter le fichier EnvironmentFile."""
    database = state / 'csirt.sqlite3'
    env = config / 'csirt.env'
    if env.is_file():
        for line in env.read_text(encoding='utf-8').splitlines():
            key, separator, value = line.strip().partition('=')
            if separator and key.strip() == 'CSIRT_DB_PATH':
                tokens = shlex.split(value, comments=False)
                if len(tokens) != 1:
                    raise ValueError('CSIRT_DB_PATH invalide ; entourer un chemin avec espaces de guillemets')
                database = Path(tokens[0])
    if not database.is_absolute() or not database.resolve().is_relative_to(state.resolve()) or database.resolve() == state.resolve():
        raise ValueError('CSIRT_DB_PATH doit être un fichier sous /var/lib/csirt')
    return database


def restore_database(snapshot, destination, metadata):
    with closing(sqlite3.connect(snapshot)) as original, closing(sqlite3.connect(destination)) as target:
        original.backup(target)
    destination.chmod(metadata.st_mode & 0o777)
    if hasattr(os, 'chown'):os.chown(destination, metadata.st_uid, metadata.st_gid)


def install_release(source, app=Path('/opt/csirt'), config=Path('/etc/csirt'),
                    state=Path('/var/lib/csirt'), backups=Path('/var/backups/csirt'),
                    units=Path('/etc/systemd/system'), command_dir=Path('/usr/local/bin'),
                    run=subprocess.run):
    verify(source)
    release = json.loads((source / 'EXPORT.json').read_text(encoding='utf-8'))
    if release['database_included']:
        raise ValueError('Le téléchargement public doit être sans base de données')
    if app.is_symlink() or (app.exists() and not (app / 'csirt/api.py').is_file()):
        raise ValueError(f'{app} ne correspond pas à une installation CSIRT reconnue')
    database = database_path(config, state)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    previous = app.with_name(f'{app.name}-previous-{stamp}')
    failed = app.with_name(f'{app.name}-failed-{stamp}')
    staging = app.with_name(f'.{app.name}-incoming-{stamp}')
    backup = backups / stamp
    backup.mkdir(parents=True, mode=0o700)
    backup.chmod(0o700)
    if config.exists():shutil.copytree(config, backup / 'config')
    for service in SERVICES:
        unit = units / service
        if unit.exists():
            (backup / 'units').mkdir(exist_ok=True)
            shutil.copy2(unit, backup / 'units' / service)
    app.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, staging)
    for directory in [staging, *[p for p in staging.rglob('*') if p.is_dir()]]:directory.chmod(0o755)
    active = [service for service in SERVICES if run(
        ['systemctl', 'is-active', '--quiet', service], check=False).returncode == 0]
    enabled = [service for service in SERVICES if run(
        ['systemctl', 'is-enabled', '--quiet', service], check=False).returncode == 0]
    existing = [service for service in SERVICES if run(
        ['systemctl', 'cat', service], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0]
    database_metadata = None
    replaced = False
    had_app = app.exists()
    try:
        for service in existing:run(['systemctl', 'stop', service], check=True)
        if database.exists():
            database_metadata = database.stat()
            backup_database(database, backup / 'database.sqlite3')
        (backup / 'installation.json').write_text(json.dumps({
            'database': str(database), 'previous_code': str(previous) if had_app else None,
            'active_services': active, 'enabled_services': enabled, 'version': release['version'],
        }, indent=2) + '\n', encoding='utf-8')
        if had_app:app.rename(previous)
        try:staging.rename(app)
        except OSError:
            if had_app:previous.rename(app)
            raise
        replaced = True
        inherited_lock = sys.platform == 'linux' and Path('/proc/self/fd/9').exists() and os.readlink('/proc/self/fd/9') == '/run/lock/csirt-install.lock'
        run(['bash', str(app / 'install.sh'), '--skip-system-packages'], check=True,
            pass_fds=(9,) if inherited_lock else ())
    except (OSError, ValueError, sqlite3.Error, subprocess.CalledProcessError):
        if replaced:
            for service in SERVICES:run(['systemctl', 'stop', service], check=False)
            app.rename(failed)
            if had_app:previous.rename(app)
            if database_metadata:
                restore_database(backup / 'database.sqlite3', database, database_metadata)
            if (backup / 'config').exists():shutil.copytree(backup / 'config', config, dirs_exist_ok=True)
            for service in SERVICES:
                saved = backup / 'units' / service
                if saved.exists():shutil.copy2(saved, units / service)
                else:
                    run(['systemctl', 'disable', service], check=False)
                    (units / service).unlink(missing_ok=True)
            run(['systemctl', 'daemon-reload'], check=True)
            for service in existing:
                run(['systemctl', 'enable' if service in enabled else 'disable', service], check=True)
        for service in active:run(['systemctl', 'start', service], check=True)
        print(f'Échec : sauvegardes conservées dans {backup}. Ancien service restauré si présent.', file=sys.stderr)
        raise
    finally:
        # Seul le staging créé par cet appel est supprimé ; sauvegardes jamais effacées.
        if staging.exists():shutil.rmtree(staging)
    command_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(app / 'bootstrap.sh', command_dir / 'csirt-update')
    (command_dir / 'csirt-update').chmod(0o755)
    print(f'CSIRT {release["version"]} installé. Mise à jour : sudo csirt-update')
    print(f'Sauvegardes : {backup}')
    if had_app:print(f'Ancien code et environnement : {previous}')
    return {'version': release['version'], 'backup': str(backup),
            'previous_code': str(previous) if had_app else None}


def main():
    parser = argparse.ArgumentParser(description='Installer une version CSIRT vérifiée')
    parser.add_argument('--source', required=True, type=Path)
    args = parser.parse_args()
    if not hasattr(os, 'geteuid') or os.geteuid() != 0:parser.exit(1, 'Lancer le bootstrap avec sudo.\n')
    try:install_release(args.source.resolve())
    except (OSError, ValueError, sqlite3.Error, subprocess.CalledProcessError) as exc:
        parser.exit(1, f'Installation interrompue : {exc}\n')


if __name__ == '__main__':main()
