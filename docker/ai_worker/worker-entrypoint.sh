#!/usr/bin/env bash
set -euo pipefail

echo "Starting AI worker entrypoint"

# Wait for database (simple polling). Adjust DATABASE_URL parsing as needed.
if [ -n "${DJANGO_DB_HOST-}" ]; then
  echo "Waiting for DB host ${DJANGO_DB_HOST} to be reachable..."
fi

# Apply migrations if requested
if [ "${APPLY_MIGRATIONS-true}" = "true" ]; then
  echo "Applying Django migrations..."
  python manage.py migrate --noinput || true
fi

if [ "${WORKER_LOOP-true}" = "true" ]; then
  echo "Running AI worker in loop mode"
  exec python manage.py run_ai_worker --loop --sleep ${WORKER_SLEEP:-10}
else
  echo "Running one-shot AI worker"
  exec python manage.py run_ai_worker
fi
