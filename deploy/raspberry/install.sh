#!/usr/bin/env bash
set -euo pipefail
umask 022

usage() { printf '%s\n' 'Usage : sudo ./install.sh [--offline] [--skip-system-packages]' 'Installe automatiquement le projet dans /opt/csirt et prépare les prérequis système.'; }
offline=false
skip_system=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --offline) offline=true ;;
    --skip-system-packages) skip_system=true ;;
    --help|-h) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
  shift
done
[[ ${EUID} -eq 0 ]] || { printf '%s\n' 'Lancer avec sudo.' >&2; exit 1; }
script_file="$(readlink -f -- "${BASH_SOURCE[0]}")"
app_dir="$(cd -P -- "$(dirname -- "$script_file")/../.." && pwd -P)"
[[ -d /run/systemd/system ]] || { printf '%s\n' 'Raspberry Pi OS avec systemd est requis.' >&2; exit 1; }
# Une installation à la fois. flock est fourni par util-linux sur Raspberry Pi OS.
lock_file=/run/lock/csirt-install.lock
# Réutiliser le même descripteur quand le bootstrap tient déjà le verrou.
if [[ "$(readlink /proc/$$/fd/9 2>/dev/null || true)" != "$lock_file" ]]; then exec 9>"$lock_file"; fi
flock -n 9 || { printf '%s\n' 'Une installation CSIRT est déjà en cours.' >&2; exit 1; }
cd "$app_dir"
if ! "$offline" && ! "$skip_system"; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv ca-certificates tzdata
fi
python3 -c 'import sys,platform,struct; assert sys.version_info >= (3,11), "Python 3.11 minimum"; assert platform.machine()=="armv7l" and struct.calcsize("P")==4, "Pi 2 v1.1 : utiliser Raspberry Pi OS Lite 32 bits ARMv7"'
if [[ "$app_dir" != '/opt/csirt' ]]; then
  printf 'Dossier source : %s\nInstallation automatique vers /opt/csirt ; le dossier source sera conservé ou sauvegardé.\n' "$app_dir"
  local_args=(--source "$app_dir" --local --skip-system-packages)
  if "$offline"; then local_args+=(--offline); fi
  exec python3 scripts/install_release.py "${local_args[@]}"
fi
if [[ -f MANIFEST.sha256 ]]; then
  python3 scripts/verify_export.py "$app_dir"
fi
if ! id csirt >/dev/null 2>&1; then
  useradd --system --user-group --home-dir /var/lib/csirt --shell /usr/sbin/nologin csirt
fi
install -d -m 0750 -o csirt -g csirt /var/lib/csirt
install -d -m 0700 -o csirt -g csirt /var/lib/csirt/backups
install -d -m 0750 -o root -g csirt /etc/csirt
if [[ ! -f /etc/csirt/csirt.env ]]; then
  install -m 0640 -o root -g csirt deploy/raspberry/csirt.env.example /etc/csirt/csirt.env
fi
if [[ ! -f /etc/csirt/sources.toml ]]; then
  install -m 0640 -o root -g csirt csirt/sources.toml /etc/csirt/sources.toml
fi
# Préparer un nouveau venv sans modifier celui d'un service déjà actif.
release_id="$(date -u +%Y%m%dT%H%M%S)-$$"
venv_dir="$app_dir/.venvs/$release_id"
python3 -m venv "$venv_dir"
bundled=false
if [[ -f "$app_dir/deploy/raspberry/wheels/WHEELHOUSE.json" ]]; then
  if python3 -c 'import json,sys; from pathlib import Path; manifest=json.loads(Path("deploy/raspberry/wheels/WHEELHOUSE.json").read_text()); sys.exit(0 if f"{sys.version_info.major}.{sys.version_info.minor}" in manifest["python_versions"] else 1)'; then
    bundled=true
  fi
fi
if "$bundled"; then
  "$venv_dir/bin/python" -m pip install --disable-pip-version-check --no-cache-dir --no-index \
    --find-links="$app_dir/deploy/raspberry/wheels" --only-binary=:all: -r deploy/raspberry/requirements.lock
elif "$offline"; then
  printf '%s\n' 'Roues absentes pour ce Python : exporter avec --wheelhouse et la bonne version Python pour utiliser --offline.' >&2
  exit 1
else
  "$venv_dir/bin/python" -m pip install --disable-pip-version-check --no-cache-dir -r requirements-pi.txt
fi
"$venv_dir/bin/python" -m pip check
# EnvironmentFile est interprété par systemd, pas exécuté comme un script shell.
run_as_csirt() {
  systemd-run --quiet --wait --pipe --collect --unit="csirt-install-$release_id-$1" \
    --uid=csirt --gid=csirt --working-directory="$app_dir" \
    --property=EnvironmentFile=/etc/csirt/csirt.env --property=UMask=0027 \
    --property=MemoryMax=256M "$venv_dir/bin/python" scripts/pi_admin.py "${@:2}"
}
run_as_csirt check check --require-pi
# La configuration invalide est refusée avant arrêt et avant toute migration.
for service in csirt-collector.service csirt-web.service; do
  if systemctl cat "$service" >/dev/null 2>&1; then systemctl stop "$service"; fi
done
run_as_csirt prepare prepare --seed "$app_dir/data/csirt-backup.sqlite3"
# Préserver l'ancien venv ; le nouveau n'est jamais déplacé, ses chemins restent valides.
if [[ -d "$app_dir/.venv" && ! -L "$app_dir/.venv" ]]; then
  mv -- "$app_dir/.venv" "$app_dir/.venv-before-$release_id"
fi
ln -sfn -- "$venv_dir" "$app_dir/.venv"
install -m 0644 deploy/raspberry/csirt-web.service /etc/systemd/system/csirt-web.service
install -m 0644 deploy/raspberry/csirt-collector.service /etc/systemd/system/csirt-collector.service
systemctl daemon-reload
systemctl enable --now csirt-web.service csirt-collector.service
"$venv_dir/bin/python" scripts/pi_admin.py status --wait 45
printf '%s\n' 'Installation vérifiée.' 'Interface : http://ADRESSE_DU_PI:8000' \
  'Configuration conservée dans /etc/csirt ; données dans /var/lib/csirt.' \
  'Pour toute mise à jour, suivre README_RASPBERRY_PI.md avant extraction.'
for address in $(hostname -I); do
  if [[ "$address" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then printf 'Ouvrir : http://%s:8000/\n' "$address"; fi
done
