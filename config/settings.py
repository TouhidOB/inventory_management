"""
Django settings for StockWhisk (multi-tenant retail ERP SaaS).

12-factor / env-driven. Reads a .env file if present. DB engine is switchable
between SQLite (default, zero-setup local dev), PostgreSQL, and MySQL via the
DB_ENGINE env var so the same codebase runs on a laptop, a cPanel/MySQL host,
or a Postgres VPS. Core models avoid Postgres-only field types to stay portable.
"""
import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env(key, default=None):
    return os.environ.get(key, default)


def env_bool(key, default=False):
    val = os.environ.get(key)
    if val is None:
        return default
    return val.lower() in {"1", "true", "yes", "on"}


def env_list(key, default=""):
    raw = os.environ.get(key, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


SECRET_KEY = env("SECRET_KEY", "django-insecure-dev-only-change-me")
DEBUG = env_bool("DEBUG", True)
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party
    "rest_framework",
    "rest_framework_simplejwt",
    "corsheaders",
    "django_celery_beat",
    "drf_spectacular",
    # StockWhisk foundation apps (Phase 0)
    "core",
    "tenants",
    "accounts",
    "audit",
    "platform_admin",
    # Phase 1 (MVP) business apps
    "catalog",
    "inventory",
    "crm",
    "purchasing",
    "sales",
    "pos",
    "accounting",
    "analytics",
    "billing",
    # Phase 2 apps
    "notifications",
    "branches",
    "reports",
    # Phase 3 apps
    "service",
    "public_api",
    # Server-rendered frontend
    "web",
    # Super-admin bulk data import pipeline
    "imports",
]

# Session-auth frontend redirects
LOGIN_URL = "web:login"
LOGIN_REDIRECT_URL = "web:dashboard"
LOGOUT_REDIRECT_URL = "web:login"

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Both run AFTER auth so request.user is available.
    "accounts.middleware.LastSeenMiddleware",
    "core.middleware.TenantMiddleware",
    "core.middleware.ContentSecurityPolicyMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "web.context.shop_context",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# --- Database: env-switchable engine -----------------------------------------
DB_ENGINE = env("DB_ENGINE", "sqlite")  # sqlite | postgres | mysql
# Persistent connections: reuse a DB connection for up to CONN_MAX_AGE seconds
# instead of opening a new one per request — essential under concurrent load.
DB_CONN_MAX_AGE = int(env("DB_CONN_MAX_AGE", "60"))
if DB_ENGINE == "postgres":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("DB_NAME", "stockwhisk"),
            "USER": env("DB_USER", "postgres"),
            "PASSWORD": env("DB_PASSWORD", ""),
            "HOST": env("DB_HOST", "127.0.0.1"),
            "PORT": env("DB_PORT", "5432"),
            "CONN_MAX_AGE": DB_CONN_MAX_AGE,
            "CONN_HEALTH_CHECKS": True,
        }
    }
elif DB_ENGINE == "mysql":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.mysql",
            "NAME": env("DB_NAME", "stockwhisk"),
            "USER": env("DB_USER", "root"),
            "PASSWORD": env("DB_PASSWORD", ""),
            "HOST": env("DB_HOST", "127.0.0.1"),
            "PORT": env("DB_PORT", "3306"),
            "OPTIONS": {"charset": "utf8mb4"},
            "CONN_MAX_AGE": DB_CONN_MAX_AGE,
            "CONN_HEALTH_CHECKS": True,
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = env("TIME_ZONE", "Asia/Dhaka")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- DRF + JWT ----------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    # Baseline abuse guard for all authed/anon API traffic. Views that need a
    # different limit override throttle_classes (JWT token = 'auth' scope,
    # public v1 = APIKeyRateThrottle), so those are unaffected.
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.UserRateThrottle",
        "rest_framework.throttling.AnonRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "user": env("API_USER_THROTTLE", "2000/hour"),
        "anon": env("API_ANON_THROTTLE", "120/hour"),
        "public_api": "600/hour",
        "public_api_enterprise": "5000/hour",
        # Brute-force guard for the JWT token endpoints (per client IP).
        "auth": env("AUTH_THROTTLE_RATE", "10/min"),
    },
}

SPECTACULAR_SETTINGS = {
    "TITLE": "StockWhisk API",
    "DESCRIPTION": "Multi-tenant retail ERP + POS API. Public v1 surface is gated to Enterprise.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=int(env("JWT_ACCESS_MIN", "60"))),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=int(env("JWT_REFRESH_DAYS", "7"))),
}

# --- Celery -------------------------------------------------------------------
# Redis is the broker in production. If REDIS_URL is unset we fall back to eager
# (synchronous) execution so local dev works without a running Redis.
REDIS_URL = env("REDIS_URL", "")
CELERY_BROKER_URL = REDIS_URL or "memory://"
CELERY_RESULT_BACKEND = REDIS_URL or "cache+memory://"
CELERY_TASK_ALWAYS_EAGER = not bool(REDIS_URL)
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"

# --- Cache: Redis when available, in-memory otherwise --------------------------
# Analytics aggregations are cached with a short TTL (dashboards hit often).
if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "stockwhisk-local",
        }
    }
ANALYTICS_CACHE_TTL = int(env("ANALYTICS_CACHE_TTL", "60"))  # seconds

# Platform billing receiving details (shown to owners for manual payment).
# Configurable here / via Super Admin platform settings — NOT a live gateway.
PLATFORM_BILLING = {
    "bkash": env("PLATFORM_BKASH", ""),
    "nagad": env("PLATFORM_NAGAD", ""),
    "bank": env("PLATFORM_BANK", ""),
}

# --- CORS ---------------------------------------------------------------------
CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS", "")
CORS_ALLOW_ALL_ORIGINS = env_bool("CORS_ALLOW_ALL_ORIGINS", DEBUG)

# --- Transport / cookie hardening (production) --------------------------------
# Sent as defaults; overridable via env. Applied only when DEBUG is off so local
# HTTP dev is unaffected. Clickjacking is already blocked by XFrameOptionsMiddleware.
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
# Behind HTTPS/a proxy, Django requires the site's own https origin(s) here or
# every POST 403s ("Origin checking failed"). Supply prod domains via env, e.g.
# CSRF_TRUSTED_ORIGINS="https://app.stockwhisk.com,https://www.stockwhisk.com".
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS", "")
if not DEBUG:
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_HSTS_SECONDS = int(env("SECURE_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

# --- Email --------------------------------------------------------------------
# Console backend in dev (prints to stdout); SMTP in prod when a host is set.
# Notifications degrade gracefully — a missing host just means no email is sent.
if DEBUG or not env("EMAIL_HOST"):
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
else:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = env("EMAIL_HOST")
    EMAIL_PORT = int(env("EMAIL_PORT", "587"))
    EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
    EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
    EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
    EMAIL_TIMEOUT = int(env("EMAIL_TIMEOUT", "10"))  # never block a request forever
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "StockWhisk <no-reply@stockwhisk.app>")
SERVER_EMAIL = env("SERVER_EMAIL", DEFAULT_FROM_EMAIL)

# --- Logging ------------------------------------------------------------------
# Structured console logging (captured by systemd/journald or Docker in prod).
# App loggers at INFO; noisy request 5xx go through django.request at ERROR.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {"format": "{asctime} {levelname} {name}: {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "verbose"},
    },
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "ERROR", "propagate": False},
        "django.security": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}

# --- Anthropic (AI insights, Phase 3) ----------------------------------------
ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = env("ANTHROPIC_MODEL", "claude-opus-4-8")
AI_INSIGHTS_DAILY_CAP = int(env("AI_INSIGHTS_DAILY_CAP", "1"))  # Claude calls/shop/day

# --- WhatsApp (Meta Business API, Phase 3) -----------------------------------
WHATSAPP_TOKEN = env("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_ID = env("WHATSAPP_PHONE_ID", "")
WHATSAPP_VERIFY_TOKEN = env("WHATSAPP_VERIFY_TOKEN", "stockwhisk-verify")
WHATSAPP_API_URL = env("WHATSAPP_API_URL", "https://graph.facebook.com/v20.0")
