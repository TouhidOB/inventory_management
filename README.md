<div align="center">

# 🧾 StockWhisk

### The Operating System for Modern Retail

**Multi-tenant inventory, point-of-sale, purchasing, accounting & analytics — in one platform.**

[![Python](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Django](https://img.shields.io/badge/Django-5.1-092E20?logo=django&logoColor=white)](https://www.djangoproject.com/)
[![DRF](https://img.shields.io/badge/DRF-3.17-A30000?logo=django&logoColor=white)](https://www.django-rest-framework.org/)
[![Celery](https://img.shields.io/badge/Celery-5.6-37814A?logo=celery&logoColor=white)](https://docs.celeryq.dev/)
[![Tests](https://img.shields.io/badge/tests-137%20passing-brightgreen)](#-testing)
[![License](https://img.shields.io/badge/license-Proprietary-blue)](#-license)

</div>

---

## 📖 Overview

**StockWhisk** is a production-grade, multi-tenant SaaS platform that lets small and medium retailers run their entire shop from one screen — selling, restocking, tracking dues, and reading **true profit** (after cost, discounts, and returns).

Each business operates in its own **securely isolated workspace**; one tenant's data can never leak into another's. The platform pairs **accounting-grade correctness** (weighted-average costing, honest COGS, VAT, returns) with a fast, mobile-friendly interface built for a busy counter.

Built for general retail, electronics & mobile shops, pharmacies, restaurants, apparel, hardware, wholesale, and repair/service centers.

**Multi-tenancy model:** shared database, shared schema, scoped by `shop_id` on every tenant-owned row via an abstract `TenantScopedModel` + auto-filtering `TenantManager` + tenant-resolution middleware. Fail-closed — no tenant in context ⇒ empty queryset (never leaks another shop's data).

---

## ✨ Key Features

<table>
<tr><td valign="top" width="33%">

**🛒 Sales & POS**
- Barcode-driven point of sale
- Walk-in customer capture
- Line & invoice discounts
- Partial payments & dues
- Idempotent checkout (double-click safe)
- Printable A4 invoices

</td><td valign="top" width="33%">

**📦 Inventory**
- Live stock, weighted-average cost
- Per-barcode price & warranty table
- Immutable movement ledger (O(1) writes)
- Low-stock & out-of-stock alerts
- Multi-branch stock & transfers
- Nightly reconcile safety net

</td><td valign="top" width="33%">

**🧮 Accounting & Analytics**
- True profit (revenue − COGS ± returns)
- VAT, cash flow, expenses
- Dashboards & 9+ report types
- CSV/Excel export
- Inventory valuation
- Cached, sub-second dashboards

</td></tr>
<tr><td valign="top">

**🚚 Purchasing**
- Purchase orders → goods receipt
- Supplier payables & payments
- Warranty capture at purchase
- Bulk barcode intake

</td><td valign="top">

**👥 Customers & Suppliers**
- Auto-created POS customers
- FIFO due allocation
- Per-customer discounts
- Purchase & invoice history

</td><td valign="top">

**🔧 Service & Warranty**
- Per-unit warranty tracking
- Repair/service ticket workflow
- Return & exchange handling

</td></tr>
</table>

**Platform:** role-based access control · audit logging · bulk import wizard · super-admin video tutorials · manual subscription billing · REST API with JWT + enterprise API keys · PWA (app-shell cache).

---

## 🧱 Tech Stack

| Layer | Technology |
|---|---|
| **Backend** | Python 3.13 · Django 5.1 · Django REST Framework 3.17 |
| **Frontend** | Server-rendered Django templates · Bootstrap 5 · Alpine.js · htmx · Chart.js |
| **Database** | PostgreSQL (production) · SQLite (development) |
| **Async / Scheduling** | Celery 5.6 · Redis · django-celery-beat |
| **Auth** | Session (web) · JWT — `djangorestframework-simplejwt` (API) |
| **API Docs** | drf-spectacular (OpenAPI 3 / Swagger / ReDoc) |
| **Server** | Gunicorn behind nginx |

---

## 🏗️ Architecture

StockWhisk is a **modular monolith** — 25+ focused Django apps sharing one tenant-scoped data model.

```
config/            Project settings, URLs, Celery, WSGI/ASGI
core/              TenantScopedModel, TenantManager, middleware, base API viewset, CSP
tenants/           Shop, Branch (the tenant + its outlets)
accounts/          Users, roles, RBAC catalog & permissions
platform_admin/    Super-admin: shop provisioning, impersonation, tutorials
catalog/           Products, categories, brands, per-unit barcodes
inventory/         Stock movements (ledger), restock, reconcile
crm/               Customers
purchasing/        Suppliers, purchase orders, supplier payments
sales/             Sales, invoices, payments, returns  ← transactional heart
pos/               Point-of-sale API
accounting/        Profit, expenses, cash ledger, financial position
analytics/         Dashboards, rollups, reports (cached)
billing/           Subscriptions & trials (manual billing)
notifications/     Low-stock & event alerts
branches/          Multi-branch stock & transfers
reports/           CSV/Excel exports
service/           Warranties & repair tickets
imports/           Super-admin bulk import wizard
public_api/        Enterprise API-key surface (v1)
web/               Server-rendered frontend (all pages, views, templates)
audit/             Append-only audit log
```

**Data integrity guarantees:** tenant isolation (cross-shop → 404), immutable stock & audit ledgers, DB-level non-negative money constraints, atomic + concurrency-safe sale transactions (row-locked stock checks).

---

## 🚀 Getting Started

### Prerequisites
- Python **3.13+**
- Redis (optional in dev — leave `REDIS_URL` empty for synchronous execution)
- PostgreSQL (production; SQLite works out of the box for dev)

### 1. Clone & install
```bash
git clone <your-repo-url> stockwhisk
cd stockwhisk

python -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

### 2. Configure environment
```bash
cp .env.example .env
# Edit .env — everything has a safe local default.
```

### 3. Migrate & create a super-admin
```bash
python manage.py migrate
python manage.py createsuperuser
```

### 4. Run
```bash
python manage.py runserver
# → http://127.0.0.1:8000
```

Log in at `/login/`. Provision a shop from the platform admin, then sign in as the shop owner.

### 5. (Optional) Background workers
```bash
# Requires REDIS_URL set in .env
celery -A config worker -l info
celery -A config beat  -l info      # scheduled tasks (low-stock, nightly reconcile…)
```

---

## ⚙️ Configuration

All configuration is via environment variables (see [`.env.example`](.env.example)):

| Variable | Default | Description |
|---|---|---|
| `SECRET_KEY` | `change-me…` | **Set a 60+ char random value in production** |
| `DEBUG` | `True` | Set `False` in production |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated hosts |
| `CSRF_TRUSTED_ORIGINS` | *(empty)* | `https://app.yourdomain.com` — **required behind HTTPS/proxy** |
| `DB_ENGINE` | `sqlite` | `sqlite` \| `postgres` \| `mysql` |
| `DB_NAME/USER/PASSWORD/HOST/PORT` | — | Database connection (non-sqlite) |
| `DB_CONN_MAX_AGE` | `60` | Persistent DB connections (seconds) |
| `REDIS_URL` | *(empty)* | Celery/cache; empty → eager local execution |
| `EMAIL_HOST` / `EMAIL_HOST_USER` / `…PASSWORD` | — | SMTP; console backend used in dev |
| `DEFAULT_FROM_EMAIL` | `StockWhisk <no-reply@…>` | Sender address |
| `JWT_ACCESS_MIN` / `JWT_REFRESH_DAYS` | `60` / `7` | Token lifetimes |
| `CORS_ALLOW_ALL_ORIGINS` | `True` (dev) | Lock down in production |
| `AUTH_THROTTLE_RATE` | `10/min` | Login brute-force guard |

---

## 🧪 Testing

```bash
pytest                    # full suite (137 tests)
pytest -q                 # quiet
pytest tests/test_x.py    # single module
```

The suite covers tenant isolation, sales/inventory/accounting correctness, RBAC, API contracts, edge cases, and concurrency (PostgreSQL-only concurrency tests auto-skip on SQLite).

```bash
python manage.py check --deploy              # production security checklist
python manage.py reconcile_stock --dry-run   # verify stock ledger integrity
```

---

## 📡 API

Interactive documentation (when the server is running):

| | URL |
|---|---|
| Swagger UI | `/api/docs/` |
| ReDoc | `/api/redoc/` |
| OpenAPI schema | `/api/schema/` |

Authenticate via JWT:
```bash
curl -X POST http://127.0.0.1:8000/api/auth/token/ \
  -H "Content-Type: application/json" \
  -d '{"email":"owner@shop.com","password":"…"}'
```
Every list endpoint is paginated, orderable (`?ordering=`), and tenant-scoped. Enterprise integrations use scoped, hashed API keys under `/api/v1/` (tenant resolved from the key only).

---

## 🔒 Security

- **Tenant isolation** — every query auto-filtered; cross-tenant access returns 404 (audited & verified).
- **RBAC** — Owner · Manager · Cashier · Inventory Manager · Accountant, least-privilege per view.
- **Auth hardening** — per-IP + per-account login lockout, JWT throttling, secure/HTTP-only cookies, HSTS in production.
- **App hardening** — Content-Security-Policy, CSRF protection, ORM (SQLi-safe), output escaping (XSS), validated & size-capped file uploads.
- **Data integrity** — DB-level non-negative money constraints, immutable ledgers, atomic transactions.

> Found a vulnerability? Please email **security@stockwhisk.app** — do not open a public issue.

---

## 🌐 Production Deployment

```bash
export DEBUG=False
# Set SECRET_KEY, ALLOWED_HOSTS, CSRF_TRUSTED_ORIGINS, DB_*, EMAIL_*, REDIS_URL

pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput

gunicorn config.wsgi:application --workers 4 --bind 0.0.0.0:8000
celery -A config worker -l info
celery -A config beat  -l info
```

Recommended stack: **Ubuntu · nginx · Gunicorn · PostgreSQL · Redis · systemd**. Serve `/static/` and `/media/` from nginx; terminate TLS at nginx. Run `manage.py check --deploy` before going live.

---

## 🗺️ Roadmap

- [ ] Payment-gateway integrations (bKash / Nagad / cards)
- [ ] e-Invoicing / e-receipts
- [ ] Mobile companion app
- [ ] Customer loyalty & promotions
- [ ] WhatsApp / SMS engagement
- [ ] AI demand forecasting
- [ ] Supplier portals & franchise consolidation

---

## 📁 Project Structure

```
stockwhisk/
├── config/              # settings, urls, celery, wsgi/asgi
├── core/                # tenant scoping, base models & viewsets, middleware
├── <domain apps>/       # accounts, catalog, inventory, sales, purchasing, …
├── web/                 # server-rendered frontend (views + templates)
├── templates/web/       # HTML templates
├── tests/               # pytest suite + conftest
├── marketing/           # flyer & prospectus (sources)
├── manage.py
├── requirements.txt
└── .env.example
```

---

## 🤝 Contributing

1. Branch from `main`.
2. Keep changes tenant-scoped and covered by tests.
3. Run `pytest` and `python manage.py check` before opening a PR.
4. Follow the existing code style (PEP 8, descriptive names, docstrings on services).

---

## 📄 License

Proprietary — © StockWhisk. All rights reserved. Contact the maintainers for licensing.

---

<div align="center">

**StockWhisk — বিশ্বাসে চলুক আপনার ব্যবসা**

Built with Django · Made for retailers

</div>
