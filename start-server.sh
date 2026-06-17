#!/usr/bin/env bash
# systemd entrypoint for the deployed server. The web assets are built locally
# and synced to web/dist, so production restarts should not run npm.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

HOST="${MING_SIM_HOST:-127.0.0.1}"
PORT="${MING_SIM_PORT:-8010}"
PY="${MING_PYTHON:-.venv/bin/python}"

exec ./start.sh --host "$HOST" --port "$PORT" --no-build --python "$PY"
