#!/usr/bin/env bash
# Idempotent, non-interactive bootstrap for Cursor Cloud Agents.
# Never fails because a Google credential is absent, and never writes secrets.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

export PIP_DISABLE_PIP_VERSION_CHECK=1
export PIP_NO_INPUT=1
export DEBIAN_FRONTEND=noninteractive

if ! python3 -c 'import ensurepip' >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y python3-venv
fi

if [[ -d .venv && ! -x .venv/bin/python ]]; then
  rm -rf .venv
fi

python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt

if [[ -z "${SERVICE_ACCOUNT_FILE:-}" ]]; then
  echo "WARNING: SERVICE_ACCOUNT_FILE is unset. The crawler expects this variable to be a path to a Google service account JSON file. Install continues without it. Do not commit that file."
elif [[ ! -f "${SERVICE_ACCOUNT_FILE}" ]]; then
  echo "WARNING: SERVICE_ACCOUNT_FILE does not point at an existing file. Install continues without it. Do not commit the service account JSON."
fi
