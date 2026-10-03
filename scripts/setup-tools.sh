#!/usr/bin/env bash
# Prepare controller prerequisites, pinned Python dependencies, Helm and kubectl.
set -euo pipefail
project_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$project_root"

if [[ $(uname -s) != Linux || $(uname -m) != x86_64 ]]; then
    echo 'The supplied controller tools require Linux amd64.' >&2
    exit 1
fi

missing=()
for program in make curl openssl python3 ssh; do
    command -v "$program" >/dev/null 2>&1 || missing+=("$program")
done
if ! python3 -c 'import ensurepip, venv' >/dev/null 2>&1; then
    missing+=(python3-venv)
fi
if (( ${#missing[@]} )); then
    # Installing controller packages is supported on the documented Ubuntu OS.
    source /etc/os-release
    if [[ ${ID:-} != ubuntu || ${VERSION_ID:-} != 24.04 ]]; then
        echo "Missing controller prerequisites: ${missing[*]}. Use Ubuntu 24.04 or install them first." >&2
        exit 1
    fi
    privilege=()
    if (( EUID != 0 )); then
        if ! command -v sudo >/dev/null 2>&1 || ! sudo -n true; then
            echo 'Installing missing packages requires passwordless sudo on this controller. Install the prerequisites as an administrator, then rerun as your regular user.' >&2
            exit 1
        fi
        privilege=(sudo -n)
    fi
    "${privilege[@]}" apt-get update
    "${privilege[@]}" apt-get install -y ca-certificates make curl openssl python3 python3-venv openssh-client
fi

if [[ ! -x .venv/bin/python ]]; then
    python3 -m venv .venv
fi
.venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt
.venv/bin/python scripts/tools.py
