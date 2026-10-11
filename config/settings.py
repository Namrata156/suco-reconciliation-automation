"""
Django settings for the Enterprise Payment Reconciliation Platform.

Phase 1 apps: `core` + `file_processing` + `transactions` (Database
Design + File Processing Framework). `reconciliation` (Transaction
Processing Engine, Phase 2, first slice: 2-way presence matching) added
2026-09-14 -- see claude.md / TRACKER.md for the full phase plan.
`rule_engine` (Business Rule Engine, Phase 2 Step 3: versioned rule
repository, response codes, ageing buckets) added 2026-10-09.

All environment-specific values (DB credentials, Celery/Redis URLs, secret
key) are read from environment variables with local-dev-only fallbacks.
Never hardcode real credentials here.
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# --------------------------------------------------------------------------
# Core / security
# --------------------------------------------------------------------------
# Local-dev-only fallback. Every real deployment must set DJANGO_SECRET_KEY.
SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "dev-only-insecure-secret-key-do-not-use-in-production",
)

DEBUG = os.environ.get("DJANGO_DEBUG", "true").lower() in ("1", "true", "yes")

ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if h.strip()
]

# --------------------------------------------------------------------------
# Applications
# --------------------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "core",
    "file_processing",
    "transactions",
    "reconciliation",
    "rule_engine",
    "exception_management",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# --------------------------------------------------------------------------
# Database -- PostgreSQL per TRACKER.md Tech Stack table. Credentials are
# env-var driven; defaults below are local-dev placeholders only, not real
# credentials.
#
# DJANGO_USE_SQLITE_FOR_MIGRATION_CHECK is NOT a general dev toggle -- it
# exists solely so `manage.py migrate` can be run once against a throwaway
# in-memory SQLite DB to validate migration correctness when no real
# Postgres instance is reachable (as in this environment). Postgres stays
# the actual configured backend; this must never be set in a real
# deployment or left set locally after the one-off check.
# --------------------------------------------------------------------------
if os.environ.get("DJANGO_USE_SQLITE_FOR_MIGRATION_CHECK", "").lower() in ("1", "true", "yes"):
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("POSTGRES_DB", "reconciliation"),
            "USER": os.environ.get("POSTGRES_USER", "reconciliation"),
            "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
            "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
            "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        }
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --------------------------------------------------------------------------
# Password validation / auth
# --------------------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Django's built-in User model is used for UploadedFile.uploaded_by for now.
# Maker-Checker roles (Administrator/Maker/Checker/Auditor) are an explicit
# out-of-scope human decision (Phase 3) -- not modeled here.

# --------------------------------------------------------------------------
# Internationalization
# --------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("DJANGO_TIME_ZONE", "Asia/Kolkata")
USE_I18N = True
USE_TZ = True

# --------------------------------------------------------------------------
# Static files
# --------------------------------------------------------------------------
STATIC_URL = "static/"

# --------------------------------------------------------------------------
# Celery / Redis -- async file-processing pipeline (approved plan Phase 1 §5)
# --------------------------------------------------------------------------
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE

# --------------------------------------------------------------------------
# File Processing Framework settings
# --------------------------------------------------------------------------
# Working directory for persisted uploads / per-stage artifacts. Env-var
# driven so deployments can point this at a real volume; defaults to a
# local `var/` dir for dev.
FILE_PROCESSING_WORK_DIR = os.environ.get(
    "FILE_PROCESSING_WORK_DIR",
    str(BASE_DIR / "var" / "file_processing"),
)

# --------------------------------------------------------------------------
# Email alerts (Phase 3 design section 3; owner-approved plan 2026-10-11)
# --------------------------------------------------------------------------
# Every alert is always recorded in the core.Notification outbox. Mail is
# only sent when ALERTS_EMAIL_ENABLED is true AND a mail server is
# configured (EMAIL_HOST, or a non-SMTP EMAIL_BACKEND such as the console
# backend for checking). Until then alerts are HELD_NO_MAIL_SERVER. Enabling
# mail later is .env only: fill the EMAIL_* keys and set
# ALERTS_EMAIL_ENABLED=true; then run `manage.py send_notifications`
# (scheduled). See .env.example.
def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name, "")
    return default if value.strip() == "" else value.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name, "").strip()
    return int(value) if value else default


EMAIL_BACKEND = os.environ.get("EMAIL_BACKEND", "").strip() or "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = os.environ.get("EMAIL_HOST", "").strip()
EMAIL_PORT = _env_int("EMAIL_PORT", 25)
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = _env_bool("EMAIL_USE_TLS")
EMAIL_USE_SSL = _env_bool("EMAIL_USE_SSL")
EMAIL_TIMEOUT = _env_int("EMAIL_TIMEOUT", 30)
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "").strip() or "reconciliation@localhost"
ALERTS_EMAIL_ENABLED = _env_bool("ALERTS_EMAIL_ENABLED", False)
ALERT_DIGEST_TIME = os.environ.get("ALERT_DIGEST_TIME", "").strip() or "09:30"  # local time (TIME_ZONE, IST)
# Optional: prefix for links in alert emails (e.g. https://recon.bank.local); blank -> path only.
ALERT_LINK_BASE_URL = os.environ.get("ALERT_LINK_BASE_URL", "").strip().rstrip("/")
ALERT_MAX_ATTEMPTS = 3
ALERT_RETRY_BACKOFF_MINUTES = (5, 30)  # wait after the 1st and 2nd failed attempt

# Celery beat schedule, for when a beat + worker are deployed (none runs in
# docker-compose today: schedule the management commands instead -- see
# .env.example). Built from ALERT_DIGEST_TIME.
_digest_hour, _digest_minute = (int(x) for x in ALERT_DIGEST_TIME.split(":"))
try:
    from celery.schedules import crontab

    CELERY_BEAT_SCHEDULE = {
        "deliver-notifications": {"task": "core.tasks.deliver_notifications", "schedule": 300.0},
        "send-alert-digest": {
            "task": "exception_management.tasks.send_alert_digest",
            "schedule": crontab(hour=_digest_hour, minute=_digest_minute),
        },
    }
except ImportError:  # celery is pinned in requirements.txt; defensive only
    CELERY_BEAT_SCHEDULE = {}

# Django REST Framework -- framework included per locked tech stack; no
# endpoints designed yet (API is a Phase 5 stub, see TRACKER.md).
REST_FRAMEWORK = {
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
}
