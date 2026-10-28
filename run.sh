#!/usr/bin/env bash
# Start the spritegen backend (and, if built, serve the frontend from the same port).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8760}"

# Python: honor $PY, else the project-local venv (env/ venv/ .venv/), else the
# conda "webui" env which already has fastapi/uvicorn/PIL/numpy/scipy.
if [[ -z "${PY:-}" ]]; then
  for v in env venv .venv; do
    if [[ -x "$ROOT/$v/bin/python" ]]; then PY="$ROOT/$v/bin/python"; break; fi
  done
fi
PY="${PY:-/home/newworld/.conda/envs/webui/bin/python3}"
echo "python: $PY" >&2

# Load credentials from .env (real env vars win over the file).
if [[ -f "$ROOT/.env" ]]; then
  set -a; . "$ROOT/.env"; set +a
fi

[[ -n "${QWEN_MAAS_KEY:-}" ]] || \
  echo "warn: QWEN_MAAS_KEY is empty — put it in $ROOT/.env or export it" >&2

if [[ ! -d "$ROOT/frontend/dist" ]]; then
  echo "note: frontend/dist missing — serving a placeholder. Build it with:" >&2
  echo "      cd '$ROOT/frontend' && bun install && bun run build" >&2
fi

echo "spritegen -> http://$HOST:$PORT"
exec "$PY" -m uvicorn server.app:app --host "$HOST" --port "$PORT" --app-dir "$ROOT/backend"
