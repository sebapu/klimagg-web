#!/usr/bin/env bash
# klimagg-web — local development launcher
# Version: v2.0.1

set -euo pipefail

if [[ ! -x ".venv/bin/python" ]]; then
  echo "ERROR: Missing .venv. Create it first:" >&2
  echo "  python3 -m venv .venv" >&2
  echo "  .venv/bin/python -m pip install -r requirements.txt" >&2
  exit 1
fi

if [[ ! -f ".env" ]]; then
  echo "ERROR: Missing .env. Start from the example:" >&2
  echo "  cp .env.example .env" >&2
  exit 1
fi

source .venv/bin/activate

# Install/update dependencies only when explicitly requested.
if [[ "${1:-}" == "--install" || "${1:-}" == "--deps" ]]; then
  python -m pip install -r requirements.txt
  shift
fi

# Validate the local environment before starting. Warnings do not block startup.
python tools/check_env.py --mode local

exec uvicorn app:app --reload --host 127.0.0.1 --port 8000 "$@"
