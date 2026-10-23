"""Vérification de l'intégrité d'un export, avec la bibliothèque standard."""
import argparse
import hashlib
from pathlib import Path


def verify(root):
    root=root.resolve();manifest=root/'MANIFEST.sha256'
    if not manifest.is_file():raise ValueError('MANIFEST.sha256 absent : utiliser un export produit par package_pi.py')
    entries=manifest.read_text(encoding='utf-8').splitlines()
    if not entries:raise ValueError('Manifeste vide')
    checked=set()
    for line in entries:
        digest,name=line.split('  ',1)
        path=(root/name).resolve()
        if not path.is_relative_to(root) or name in checked:raise ValueError('Chemin de manifeste invalide')
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
            raise ValueError(f'Fichier absent ou modifié : {name}')
        checked.add(name)
    for required in ('csirt/api.py','csirt/collector.py','csirt/sources.toml','install.sh','deploy/raspberry/requirements.lock'):
        if required not in checked:raise ValueError(f'Fichier essentiel absent du manifeste : {required}')
    return len(checked)


def main():
    parser=argparse.ArgumentParser(description='Contrôler les fichiers du paquet CSIRT')
    parser.add_argument('root',nargs='?',type=Path,default=Path(__file__).resolve().parents[1]);args=parser.parse_args()
    try:print(f'Export vérifié : {verify(args.root)} fichiers intacts')
    except (ValueError,OSError) as exc:parser.exit(1,f'Export invalide : {exc}\n')


if __name__=='__main__':main()
