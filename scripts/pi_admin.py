"""Contrôles, préparation et sauvegarde du déploiement natif CSIRT."""
import argparse
from datetime import datetime,timezone
import importlib.metadata
import json
from pathlib import Path
import platform
import sqlite3
import struct
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def backup_database(source:Path,destination:Path):
    """Photographie cohérente, y compris quand SQLite utilise son journal WAL."""
    if not source.is_file():raise ValueError(f'Base absente : {source}')
    if destination.exists():raise ValueError(f'La sauvegarde existe déjà : {destination}')
    destination.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent,suffix='.sqlite3',delete=False) as stream:
        temporary=Path(stream.name)
    try:
        original=sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True)
        try:
            copy=sqlite3.connect(temporary)
            try:
                original.backup(copy)
                if copy.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Sauvegarde SQLite invalide')
                copy.commit()
            finally:copy.close()
        finally:original.close()
        if destination.exists():raise ValueError(f'La sauvegarde existe déjà : {destination}')
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def check_runtime(require_pi=False):
    if sys.version_info<(3,11):raise ValueError('Python 3.11 minimum requis')
    if require_pi and (sys.platform!='linux' or platform.machine()!='armv7l' or struct.calcsize('P')!=4):
        raise ValueError('Installer Raspberry Pi OS Lite 32 bits sur le Pi 2 v1.1 (ARMv7)')
    if require_pi and sys.version_info[:2] not in {(3,11),(3,13)}:
        raise ValueError('Déploiement prévu pour Python 3.11 (Bookworm) ou 3.13 (Trixie)')
    with sqlite3.connect(':memory:') as db:db.execute('CREATE VIRTUAL TABLE test USING fts5(content)')
    from csirt.config import Settings,load_sources
    settings=Settings.from_env();sources=load_sources(settings)
    from csirt.api import create_app
    create_app(settings)
    versions={}
    for line in (ROOT/'deploy/raspberry/requirements.lock').read_text(encoding='utf-8-sig').splitlines():
        line=line.strip()
        if not line or line.startswith('#'):continue
        name,expected=line.split('==',1)
        actual=importlib.metadata.version(name)
        if actual!=expected:raise ValueError(f'Dépendance {name} : {actual}, version attendue {expected}')
        versions[name]=actual
    return {'python':platform.python_version(),'architecture':platform.machine(),
            'bits':struct.calcsize('P')*8,'sqlite_fts5':True,'mode':'production',
            'sources':[s['id'] for s in sources],'dependencies':versions}


def prepare_database(settings,seed:Path|None,state_root:Path):
    root=state_root.resolve();database=settings.db_path.resolve()
    if not database.is_relative_to(root):raise ValueError(f'CSIRT_DB_PATH doit rester sous {root}')
    database.parent.mkdir(parents=True,exist_ok=True)
    backup=None;imported=False
    if database.exists():
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        backup=backup_database(database,root/'backups'/f'before-install-{stamp}.sqlite3')
    elif seed is not None and seed.is_file():
        backup_database(seed,database);imported=True
    from csirt.db import initialize,connect
    initialize(settings)
    with connect(settings) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Base invalide après préparation')
        if db.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('Références SQLite invalides')
        counts={'incidents':db.execute('SELECT COUNT(*) FROM incidents').fetchone()[0],
                'publications':db.execute('SELECT COUNT(*) FROM observations').fetchone()[0]}
    return {'database':str(database),'backup':str(backup) if backup else None,'seed_imported':imported,**counts}


def wait_for_health(url,wait_seconds):
    deadline=time.monotonic()+wait_seconds
    while True:
        try:
            with urllib.request.urlopen(url,timeout=3) as response:health=json.load(response)
            if health.get('status')=='ok' and health.get('mode')=='production' and health.get('collector',{}).get('active'):
                return health
        except (OSError,urllib.error.URLError,ValueError):pass
        if time.monotonic()>=deadline:raise ValueError('API ou collecteur indisponible ; consulter journalctl -u csirt-web -u csirt-collector')
        time.sleep(min(1,max(0,deadline-time.monotonic())))


def main():
    parser=argparse.ArgumentParser(description='Administration locale CSIRT Raspberry Pi')
    sub=parser.add_subparsers(dest='command',required=True)
    check=sub.add_parser('check');check.add_argument('--require-pi',action='store_true')
    prepare=sub.add_parser('prepare');prepare.add_argument('--seed',type=Path);prepare.add_argument('--state-root',type=Path,default=Path('/var/lib/csirt'))
    backup=sub.add_parser('backup');backup.add_argument('output',type=Path)
    status=sub.add_parser('status');status.add_argument('--url',default='http://127.0.0.1:8000/health');status.add_argument('--wait',type=int,default=30,choices=range(0,61))
    args=parser.parse_args()
    try:
        if args.command=='check':result=check_runtime(args.require_pi)
        elif args.command=='status':result=wait_for_health(args.url,args.wait)
        else:
            from csirt.config import Settings
            settings=Settings.from_env()
            result=prepare_database(settings,args.seed,args.state_root) if args.command=='prepare' else {'backup':str(backup_database(settings.db_path,args.output))}
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except (ValueError,OSError,sqlite3.Error,importlib.metadata.PackageNotFoundError) as exc:
        parser.exit(1,f'CSIRT : {exc}\n')


if __name__=='__main__':main()
