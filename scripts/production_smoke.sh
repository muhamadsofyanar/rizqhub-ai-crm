#!/bin/sh
set -eu

python manage.py check
python manage.py v4_preflight
curl -fsS http://localhost:8000/health/ >/dev/null || true
printf '%s\n' "Smoke check aplikasi selesai. Verifikasi /health/ dari luar container setelah Gunicorn aktif."
