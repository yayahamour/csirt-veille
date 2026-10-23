"""Télécharge des roues ARM sans les installer ni les exécuter sur le PC."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description='Préparer les dépendances binaires Pi OS 32 bits')
    parser.add_argument('--output',type=Path,default=ROOT/'dist/pi-wheels')
    parser.add_argument('--python',dest='versions',action='append',choices=['3.11','3.13'])
    parser.add_argument('--offline',action='store_true',help='Vérifier uniquement les roues déjà téléchargées')
    args=parser.parse_args()
    versions=args.versions or ['3.11']
    args.output.mkdir(parents=True,exist_ok=True)
    lock=ROOT/'deploy/raspberry/requirements.lock'
    for version in versions:
        abi='cp'+version.replace('.','')
        indexes=['--no-index','--find-links',str(args.output)] if args.offline else ['--extra-index-url','https://www.piwheels.org/simple']
        subprocess.run([sys.executable,'-m','pip','download','--disable-pip-version-check',
                        '--only-binary=:all:','--platform','linux_armv7l','--implementation','cp',
                        '--python-version',version,'--abi',abi,'--dest',str(args.output),
                        *indexes,'-r',str(lock)],check=True)
    files={path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(args.output.glob('*.whl'))}
    if not files:raise SystemExit('Aucune roue téléchargée')
    manifest={'platform':'linux_armv7l','python_versions':versions,
              'requirements_sha256':hashlib.sha256(lock.read_bytes()).hexdigest(),'files':files}
    (args.output/'WHEELHOUSE.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    print(f'{len(files)} roues préparées : {args.output}')


if __name__=='__main__':main()
