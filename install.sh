#!/usr/bin/env bash
set -euo pipefail
script_file="$(readlink -f -- "${BASH_SOURCE[0]}")"
script_dir="$(cd -P -- "$(dirname -- "$script_file")" && pwd -P)"
exec bash "$script_dir/deploy/raspberry/install.sh" "$@"
