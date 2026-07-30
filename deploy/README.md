# StockWhisk — Deployment Runbook

Target stack: **Ubuntu 22.04 · nginx · Gunicorn · PostgreSQL · Redis · Celery · systemd**.
App lives at `/srv/stockwhisk`, runs as the `stockwhisk` user.

Files in this folder:
- `gunicorn.conf.py` — Gunicorn config (unix socket, worker recycling, stdout logs)
- `systemd/stockwhisk.service` — web (Gunicorn)
- `systemd/stockwhisk-celery.service` — background worker
- `systemd/stockwhisk-celerybeat.service` — scheduler (low-stock, nightly stock reconcile)
- `nginx/stockwhisk.conf` — TLS reverse proxy + static/media
- `env.production.example` — production env template
- `release.sh` — pull → install → migrate → collectstatic → restart

---

## 1. Server prep (once)

```bash
sudo apt update && sudo apt install -y python3-venv python3-dev build-essential \
    nginx postgresql redis-server libpq-dev git

# App user + directories
sudo useradd --system --home /srv/stockwhisk --shell /bin/bash stockwhisk
sudo mkdir -p /srv/stockwhisk /run/stockwhisk
sudo chown -R stockwhisk:www-data /srv/stockwhisk
```

## 2. Database (once)

```bash
sudo -u postgres psql <<'SQL'
CREATE USER stockwhisk WITH PASSWORD 'REPLACE_ME';
CREATE DATABASE stockwhisk OWNER stockwhisk;
SQL
```

## 3. Code + virtualenv

```bash
sudo -u stockwhisk -H bash
cd /srv/stockwhisk
git clone https://github.com/TouhidOB/inventory_management.git .
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp deploy/env.production.example .env      # then edit: SECRET_KEY, DB_PASSWORD, ALLOWED_HOSTS…
chmod 600 .env
```

Generate a secret key:
```bash
.venv/bin/python -c "import secrets; print(secrets.token_urlsafe(64))"
```

## 4. Migrate, static, first admin

```bash
.venv/bin/python manage.py migrate
.venv/bin/python manage.py collectstatic --noinput
.venv/bin/python manage.py createsuperuser
.venv/bin/python manage.py check --deploy      # expect 0 issues
exit                                            # back to sudo user
```

## 5. systemd services

```bash
sudo cp deploy/systemd/stockwhisk*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now stockwhisk stockwhisk-celery stockwhisk-celerybeat
sudo systemctl status stockwhisk
```

## 6. nginx + TLS

```bash
sudo cp deploy/nginx/stockwhisk.conf /etc/nginx/sites-available/stockwhisk
# edit server_name + paths, then:
sudo ln -s /etc/nginx/sites-available/stockwhisk /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d app.example.com        # issues cert + rewrites config
```

---

## Redeploy (every release)

```bash
sudo -u stockwhisk -H /srv/stockwhisk/deploy/release.sh
```

## Health & logs

```bash
curl -I https://app.example.com/healthz/
journalctl -u stockwhisk -f           # web
journalctl -u stockwhisk-celery -f    # worker
```

## Backups (recommended cron)

```bash
# Nightly DB dump
0 2 * * *  pg_dump -U stockwhisk stockwhisk | gzip > /var/backups/stockwhisk_$(date +\%F).sql.gz
# Media sync (logos/imports)
15 2 * * * rsync -a /srv/stockwhisk/media/ /var/backups/stockwhisk-media/
```

---

## Pre-flight checklist

- [ ] `.env`: `DEBUG=False`, strong `SECRET_KEY`, real `ALLOWED_HOSTS` + `CSRF_TRUSTED_ORIGINS`
- [ ] PostgreSQL reachable; `migrate` clean
- [ ] `collectstatic` run; nginx serves `/static/` and `/media/`
- [ ] `manage.py check --deploy` → 0 warnings
- [ ] TLS active (HTTP redirects to HTTPS)
- [ ] All three systemd units `active (running)`
- [ ] Celery beat scheduling (low-stock + nightly `reconcile_stock`)
- [ ] `REDIS_URL` set (shared cache + login lockout across workers)
- [ ] SMTP configured; a test email sends
- [ ] DB + media backups scheduled
