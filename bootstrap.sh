#!/usr/bin/env bash
# Point d'entrée autonome : aucun clone Git ou fichier à copier manuellement.
set -euo pipefail
umask 022

version=latest
case "${1:-}" in
  '') ;;
  --version) [[ $# -eq 2 && "$2" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo 'Usage : bootstrap.sh [--version vX.Y.Z]' >&2; exit 2; }; version="$2" ;;
  --help|-h) echo 'Usage : sudo bash bootstrap.sh [--version vX.Y.Z]'; exit 0 ;;
  *) echo 'Usage : bootstrap.sh [--version vX.Y.Z]' >&2; exit 2 ;;
esac
[[ $EUID -eq 0 ]] || { echo 'Lancer avec sudo.' >&2; exit 1; }
[[ "$(uname -s)" == Linux && "$(uname -m)" == armv7l ]] || { echo 'Pi 2 v1.1 : Raspberry Pi OS Lite 32 bits ARMv7 requis.' >&2; exit 1; }
[[ -d /run/systemd/system ]] || { echo 'Raspberry Pi OS avec systemd est requis.' >&2; exit 1; }
exec 9>/run/lock/csirt-install.lock
flock -n 9 || { echo 'Une installation CSIRT est déjà en cours.' >&2; exit 1; }
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y curl ca-certificates python3 python3-venv tzdata
python3 -c 'import sys,struct; assert sys.version_info[:2] in {(3,11),(3,13)}, "Utiliser Bookworm / Python 3.11 ou Trixie / Python 3.13"; assert struct.calcsize("P")==4, "Python 32 bits requis"'

work="$(mktemp -d /var/tmp/csirt-download.XXXXXXXX)"
cleanup() { case "$work" in /var/tmp/csirt-download.*) rm -rf -- "$work" ;; esac; }
trap cleanup EXIT
base=https://github.com/yayahamour/csirt-veille/releases
if [[ "$version" == latest ]]; then base="$base/latest/download"; else base="$base/download/$version"; fi
for asset in csirt-pi2.tar.gz csirt-pi2.tar.gz.sha256; do
  curl --fail --show-error --location --proto '=https' --proto-redir '=https' \
    --connect-timeout 15 --max-time 240 --retry 3 --retry-delay 2 --retry-max-time 300 \
    "$base/$asset" --output "$work/$asset"
done

# Vérifier le checksum avant extraction ; refuser liens, chemins sortants et gros fichiers.
python3 - "$work" <<'PY'
import hashlib, pathlib, re, shutil, sys, tarfile
root=pathlib.Path(sys.argv[1])
archive=root/'csirt-pi2.tar.gz'
checksum=(root/'csirt-pi2.tar.gz.sha256').read_text().strip()
match=re.fullmatch(r'([0-9a-f]{64})  csirt-pi2\.tar\.gz',checksum)
if not match or hashlib.sha256(archive.read_bytes()).hexdigest()!=match[1]:
    raise SystemExit('Checksum de la version invalide ; aucun code installé.')
with tarfile.open(archive) as package:
    members=package.getmembers()
    if len(members)>1000 or sum(m.size for m in members)>128*1024*1024:
        raise SystemExit('Archive trop volumineuse')
    names=set()
    for member in members:
        path=pathlib.PurePosixPath(member.name)
        if (not member.isfile() or path.is_absolute() or len(path.parts)<2 or path.parts[0]!='csirt'
            or '..' in path.parts or '\\' in member.name or path.as_posix()!=member.name or member.name in names):
            raise SystemExit('Chemin ou type de fichier interdit dans la version')
        names.add(member.name)
    for member in members:
        target=root.joinpath(*pathlib.PurePosixPath(member.name).parts)
        target.parent.mkdir(parents=True,exist_ok=True)
        with package.extractfile(member) as source, target.open('wb') as destination:
            shutil.copyfileobj(source,destination)
        target.chmod(0o755 if member.name.endswith('.sh') else 0o644)
PY
python3 "$work/csirt/scripts/verify_export.py" "$work/csirt"
python3 "$work/csirt/scripts/install_release.py" --source "$work/csirt"
