"""Export ARM : sources, dépendances et photographie SQLite cohérente facultative."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import tarfile
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def validate_wheelhouse(directory):
    manifest = json.loads((directory / 'WHEELHOUSE.json').read_text(encoding='utf-8'))
    lock_hash = hashlib.sha256((ROOT / 'deploy/raspberry/requirements.lock').read_bytes()).hexdigest()
    if manifest.get('platform') != 'linux_armv7l' or manifest.get('requirements_sha256') != lock_hash:
        raise ValueError('Roues incompatibles avec la plateforme ou le verrou de dépendances')
    versions = manifest.get('python_versions')
    if not versions or not set(versions) <= {'3.11', '3.13'}:
        raise ValueError('Versions Python du wheelhouse invalides')
    files = manifest.get('files', {})
    if not files or set(files) != {p.name for p in directory.glob('*.whl')}:
        raise ValueError('Liste des roues incomplète')
    for name, digest in files.items():
        path = directory / name
        if Path(name).name != name or '/' in name or '\\' in name or path.is_symlink():
            raise ValueError('Nom de roue invalide')
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f'Roue modifiée : {name}')
    return manifest


def create_package(output, with_database=False, wheelhouse=None):
    from scripts.pi_admin import backup_database

    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {}
    for name in ('pyproject.toml', 'requirements-pi.txt', 'README.md', 'README_RASPBERRY_PI.md',
                 'install.sh', 'bootstrap.sh', '.env.example', 'test.ipynb'):
        payload[name] = (ROOT / name).read_bytes()
    for folder in ('csirt', 'deploy', 'docs', 'scripts', 'tests'):
        for path in sorted((ROOT / folder).rglob('*')):
            relative = path.relative_to(ROOT)
            if not path.is_file() or path.is_symlink() or '__pycache__' in relative.parts or path.suffix == '.pyc':
                continue
            if 'wheels' in relative.parts or (folder == 'docs' and path.suffix not in {'.md', '.docx'}):
                continue
            data = path.read_bytes()
            if path.suffix == '.sh':data = data.decode('utf-8-sig').replace('\r\n', '\n').encode('utf-8')
            payload[relative.as_posix()] = data
    payload['install.sh'] = payload['install.sh'].decode('utf-8-sig').replace('\r\n', '\n').encode('utf-8')
    payload['bootstrap.sh'] = payload['bootstrap.sh'].decode('utf-8-sig').replace('\r\n', '\n').encode('utf-8')
    wheels = validate_wheelhouse(wheelhouse) if wheelhouse else None
    if wheels:
        for name in ['WHEELHOUSE.json', *wheels['files']]:
            payload['deploy/raspberry/wheels/' + name] = (wheelhouse / name).read_bytes()
    counts = None
    if with_database:
        from csirt.config import Settings

        with tempfile.TemporaryDirectory(dir=output.parent) as temporary:
            snapshot = backup_database(Settings.from_env().db_path, Path(temporary) / 'snapshot.sqlite3')
            with closing(sqlite3.connect(snapshot)) as db:
                counts = {'incidents': db.execute('SELECT COUNT(*) FROM incidents').fetchone()[0],
                          'publications': db.execute('SELECT COUNT(*) FROM observations').fetchone()[0]}
            payload['data/csirt-backup.sqlite3'] = snapshot.read_bytes()
    version = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8-sig'))['project']['version']
    release = {'created_at': datetime.now(timezone.utc).isoformat(), 'version': version,
               'target': 'Raspberry Pi 2 Model B v1.1 / Raspberry Pi OS Lite 32 bits',
               'database_included': with_database, 'database_counts': counts,
               'bundled_python_versions': wheels['python_versions'] if wheels else []}
    payload['EXPORT.json'] = (json.dumps(release, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    payload['MANIFEST.sha256'] = ''.join(
        f'{hashlib.sha256(data).hexdigest()}  {name}\n' for name, data in sorted(payload.items())
    ).encode('utf-8')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent, suffix='.tar.gz', delete=False) as stream:
        staging = Path(stream.name)
    try:
        with tarfile.open(staging, 'w:gz') as archive:
            for name, data in sorted(payload.items()):
                info = tarfile.TarInfo('csirt/' + name)
                info.size = len(data)
                info.mode = 0o755 if name.endswith('.sh') else 0o644
                info.uid = info.gid = 0
                info.uname = info.gname = 'root'
                archive.addfile(info, io.BytesIO(data))
        staging.replace(output)
    finally:staging.unlink(missing_ok=True)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_name(output.name + '.sha256').write_text(f'{digest}  {output.name}\n', encoding='utf-8', newline='\n')
    (output.parent / 'README_RASPBERRY_PI.md').write_bytes(payload['README_RASPBERRY_PI.md'])
    return release


def main():
    parser = argparse.ArgumentParser(description='Créer le paquet de déploiement CSIRT pour Raspberry Pi')
    parser.add_argument('--with-database', action='store_true', help='Inclure un snapshot SQLite des alertes actuelles')
    parser.add_argument('--wheelhouse', type=Path, help='Roues préparées par scripts/prepare_pi_wheels.py')
    parser.add_argument('--output', type=Path, default=ROOT / 'dist/csirt-pi2.tar.gz')
    args = parser.parse_args()
    try:release = create_package(args.output, args.with_database, args.wheelhouse)
    except (OSError, ValueError, sqlite3.Error) as exc:parser.exit(1, f'Export impossible : {exc}\n')
    print(f'Paquet : {args.output} ({args.output.stat().st_size} octets)')
    print(json.dumps(release, ensure_ascii=False, indent=2))


if __name__ == '__main__':main()
