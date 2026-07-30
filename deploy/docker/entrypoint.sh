#!/usr/bin/env sh
# Container entrypoint. Waits for the database, then (for the web process only)
# applies migrations + collects static before handing off to the CMD.
set -e

echo "==> Waiting for database…"
python - <<'PY'
import os, time, sys
import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()
from django.db import connections
from django.db.utils import OperationalError
for i in range(60):
    try:
        connections["default"].cursor()
        print("    database is up")
        sys.exit(0)
    except OperationalError:
        time.sleep(1)
print("    database not reachable after 60s", file=sys.stderr)
sys.exit(1)
PY

# Only the web container runs migrations + collectstatic (avoids races between
# worker/beat starting at the same time).
case "$1" in
  gunicorn)
    echo "==> Applying migrations"
    python manage.py migrate --noinput
    echo "==> Collecting static files"
    python manage.py collectstatic --noinput
    ;;
esac

echo "==> Starting: $*"
exec "$@"
