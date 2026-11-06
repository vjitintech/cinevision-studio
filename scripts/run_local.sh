#!/usr/bin/env bash
# Run without Docker on your own machine (single process, embedded worker, http on localhost only).
# Requirements: Python 3.12+, Node 20+, ffmpeg on PATH.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f deploy/secrets/master_key ] || python3 scripts/gen_keys.py deploy/secrets
if [ ! -d .venv ]; then python3 -m venv .venv; fi
# (re)install packages when backend/requirements.txt changed, e.g. after git pull
REQ_HASH=$(sha256sum backend/requirements.txt 2>/dev/null || shasum -a 256 backend/requirements.txt); REQ_HASH=${REQ_HASH%% *}
if [ "$(cat .venv/requirements.sha256 2>/dev/null)" != "$REQ_HASH" ]; then
  .venv/bin/pip uninstall -y -q opencv-python-headless opencv-python >/dev/null 2>&1 || true   # contrib build provides cv2
  .venv/bin/pip install -q -r backend/requirements.txt && echo "$REQ_HASH" > .venv/requirements.sha256
fi
# build the UI on first run, and again whenever the source changed (e.g. after git pull)
if [ ! -f frontend/dist/index.html ] || [ -n "$(find frontend/src frontend/build.mjs frontend/package.json -newer frontend/dist/index.html -print -quit)" ]; then
  (cd frontend && npm install --no-audit --no-fund && npm run build)
fi
export CINEVISION_MASTER_KEY_FILE="$PWD/deploy/secrets/master_key"
export CINEVISION_DATA_DIR="${CINEVISION_DATA_DIR:-$PWD/data}"
PORT="${CINEVISION_PORT:-8000}"
export CINEVISION_PORT="$PORT"
export CINEVISION_PUBLIC_ORIGIN="http://127.0.0.1:$PORT"
export CINEVISION_EXTRA_ORIGINS="http://localhost:$PORT"
export CINEVISION_COOKIE_SECURE=false          # plain http is acceptable only on 127.0.0.1
export CINEVISION_EMBEDDED_WORKER=true
export CINEVISION_FRONTEND_DIST="$PWD/frontend/dist"
cd backend
FACE="$PWD/cinevision/models/face_detection_yunet_2023mar.onnx"
[ -n "${CINEVISION_FACE_MODEL:-}" ] || [ ! -f "$FACE" ] || export CINEVISION_FACE_MODEL="$FACE"
# listens on 127.0.0.1 only, unless you set CINEVISION_HOST yourself (the server then prints a warning)
echo "CineVision Studio → http://127.0.0.1:$PORT"
exec ../.venv/bin/python -m cinevision.app
