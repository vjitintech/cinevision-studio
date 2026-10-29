#!/usr/bin/env bash
# Run without Docker on your own machine (single process, embedded worker, http on localhost only).
# Requirements: Python 3.12+, Node 20+, ffmpeg on PATH.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f deploy/secrets/master_key ] || python3 scripts/gen_keys.py deploy/secrets
if [ ! -d .venv ]; then python3 -m venv .venv; .venv/bin/pip install -q -r backend/requirements.txt; fi
if [ ! -d frontend/dist ]; then (cd frontend && npm install --no-audit --no-fund && npm run build); fi
export CINEVISION_MASTER_KEY_FILE="$PWD/deploy/secrets/master_key"
export CINEVISION_DATA_DIR="${CINEVISION_DATA_DIR:-$PWD/data}"
export CINEVISION_PUBLIC_ORIGIN="http://127.0.0.1:8000"
export CINEVISION_COOKIE_SECURE=false          # plain http is acceptable only on 127.0.0.1
export CINEVISION_EMBEDDED_WORKER=true
export CINEVISION_FRONTEND_DIST="$PWD/frontend/dist"
cd backend
echo "CineVision Studio → http://127.0.0.1:8000"
exec ../.venv/bin/python -m cinevision.app
