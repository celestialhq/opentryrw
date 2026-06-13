#!/bin/sh
set -eu

if [ "${STORAGE_BACKEND:-tinydb}" = "postgres" ] && [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
  python -m alembic upgrade head
fi

exec "$@"
