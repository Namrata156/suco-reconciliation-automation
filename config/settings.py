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

# Django REST Framework -- framework included per locked tech stack; no
# endpoints designed yet (API is a Phase 5 stub, see TRACKER.md).
REST_FRAMEWORK = {
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
}
