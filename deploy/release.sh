#!/usr/bin/env bash
# StockWhisk release/redeploy script. Run as the `stockwhisk` user in /srv/stockwhisk.
#   ./deploy/release.sh
set -euo pipefail

cd "$(dirname "$0")/.."
echo "==> Pulling latest code"
git pull --ff-only

echo "==> Installing dependencies"
.venv/bin/pip install -r requirements.txt

echo "==> Running migrations"
.venv/bin/python manage.py migrate --noinput

echo "==> Collecting static files"
.venv/bin/python manage.py collectstatic --noinput

echo "==> Django deploy checks"
.venv/bin/python manage.py check --deploy

echo "==> Restarting services"
sudo systemctl restart stockwhisk stockwhisk-celery stockwhisk-celerybeat

echo "==> Done. Status:"
systemctl --no-pager status stockwhisk | head -5
