#!/bin/sh
set -eu

cd /app

if [ ! -f /app/data/stories.db ]; then
    echo "[entrypoint] No existing SQLite file found; running init-db (this only happens on a truly fresh container)."
    python -m app.cli init-db || true
else
    echo "[entrypoint] Existing SQLite file detected at /app/data/stories.db — preserving it (no init-db)."
fi

if [ "${1:-serve}" = "serve" ]; then
    shift
    exec python -m app.cli serve --host 0.0.0.0 --port "${MAIN_PORT:-8800}" "$@"
fi

exec python -m app.cli "$@"