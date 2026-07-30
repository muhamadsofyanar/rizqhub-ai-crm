#!/bin/sh
set -eu

python manage.py wait_for_db

if [ "${SERVICE_MODE:-web}" = "web" ]; then
  python manage.py migrate --noinput
  python manage.py migrate --noinput --run-syncdb
  python manage.py collectstatic --noinput
  python manage.py bootstrap
  exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers "${WEB_CONCURRENCY:-3}" --timeout 120 --access-logfile - --error-logfile -
elif [ "${SERVICE_MODE:-web}" = "worker" ]; then
  exec celery -A config worker -l "${CELERY_LOG_LEVEL:-INFO}" --concurrency="${CELERY_CONCURRENCY:-2}"
elif [ "${SERVICE_MODE:-web}" = "beat" ]; then
  exec celery -A config beat -l "${CELERY_LOG_LEVEL:-INFO}"
else
  exec "$@"
fi
