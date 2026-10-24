"""Settings shared by every environment.

All environment-specific values come from env vars (or a `.env` file next to `manage.py`,
which is how PythonAnywhere gets its config). See `.env.example` for the full list.
"""

from datetime import date
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env.str("DJANGO_SECRET_KEY")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS: list[str] = env.list("DJANGO_ALLOWED_HOSTS", default=[])
CSRF_TRUSTED_ORIGINS: list[str] = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "drf_spectacular",
    "apps.core",
    "apps.market_data",
    "apps.analytics",
]

MIDDLEWARE = [
    "apps.core.middleware.RequestIdMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "engridapi.urls"

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

WSGI_APPLICATION = "engridapi.wsgi.application"

# D4: SQLite locally and in tests, PostgreSQL in production. The engine is a DATABASE_URL change.
DATABASES = {"default": env.db("DATABASE_URL", default="sqlite:///db.sqlite3")}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "en-gb"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# D16: local-memory cache. Fine with PythonAnywhere's single worker; Redis later is config only.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "engridapi",
        "TIMEOUT": env.int("ENGRID_CACHE_TTL_S", default=3600),
    }
}

MAILERS = {
    "default": {
        "BACKEND": "django.core.mail.backends.console.EmailBackend",
    },
}

# --- Django REST Framework -------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "UNAUTHENTICATED_USER": None,
    "EXCEPTION_HANDLER": "apps.core.exceptions.problem_exception_handler",
    # D17: anonymous throttling. NUM_PROXIES must match the proxies seen in Phase 7
    # (Vercel rewrite + PythonAnywhere front end) so the throttle keys on the real client IP.
    "DEFAULT_THROTTLE_CLASSES": ["rest_framework.throttling.AnonRateThrottle"],
    "DEFAULT_THROTTLE_RATES": {"anon": env.str("API_THROTTLE_ANON", default="240/min")},
    "NUM_PROXIES": env.int("API_NUM_PROXIES", default=None),
}

SPECTACULAR_SETTINGS = {
    "TITLE": "EnGrid Wind Forecast Monitor API",
    "DESCRIPTION": (
        "Reconstructs the Elexon WINDFOR wind forecast available at a purchasing-decision cutoff "
        "(no look-ahead) and measures its accuracy against FUELHH actual wind generation. "
        "All datetimes are UTC (ISO-8601 with Z)."
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "ENUM_NAME_OVERRIDES": {
        "PolicyEnum": "apps.analytics.domain.cutoff.CutoffPolicyKind",
        "IntervalStatusEnum": "apps.analytics.domain.matching.IntervalStatus",
        "VersionStatusEnum": "apps.analytics.domain.history.VersionStatus",
        "GroupByEnum": "apps.analytics.domain.metrics.GroupBy",
    },
}

# --- Logging (JSON lines to stderr, each with the request id) --------------------------------
LOG_LEVEL = env.str("DJANGO_LOG_LEVEL", default="INFO")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"request_id": {"()": "apps.core.logging.RequestIdFilter"}},
    "formatters": {
        "json": {"()": "apps.core.logging.JsonFormatter"},
        "plain": {"format": "%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s"},
    },
    "handlers": {
        "stderr": {
            "class": "logging.StreamHandler",
            "filters": ["request_id"],
            "formatter": env.str("DJANGO_LOG_FORMAT", default="plain"),
        }
    },
    "root": {"handlers": ["stderr"], "level": LOG_LEVEL},
}

# --- EnGrid domain configuration -------------------------------------------------------------
# Evaluation window (UK delivery dates, inclusive). The code is period-agnostic; this is config.
ENGRID_EVALUATION_DATE_FROM = date.fromisoformat(
    env.str("ENGRID_EVALUATION_DATE_FROM", default="2026-01-01")
)
ENGRID_EVALUATION_DATE_TO = date.fromisoformat(
    env.str("ENGRID_EVALUATION_DATE_TO", default="2026-01-31")
)
ENGRID_FUEL_TYPE = "WIND"

# D1 (resolved 2026-10-24): daily gate D-1 at 10:00 UK time is the default policy;
# the gate is user-adjustable; the lead-time slider runs 0-48 h with a default of 24 h.
ENGRID_DEFAULT_POLICY = "daily_gate"
ENGRID_DEFAULT_GATE_TIME = "10:00"
ENGRID_DEFAULT_GATE_DAYS_BEFORE = 1
# WINDFOR horizons reach about 67 h, so a gate three days ahead is already almost all missing.
ENGRID_GATE_DAYS_BEFORE_MAX = 3
ENGRID_DEFAULT_LEAD_TIME_H = 24
ENGRID_LEAD_TIME_H_MAX = 48

# HTTP caching (D16). The data is historical; the ETag changes with data_version.
ENGRID_HTTP_MAX_AGE_S = env.int("ENGRID_HTTP_MAX_AGE_S", default=300)
ENGRID_HTTP_S_MAXAGE_S = env.int("ENGRID_HTTP_S_MAXAGE_S", default=3600)

# --- Elexon Insights API (offline ingestion only, R11) ---------------------------------------
ELEXON_BASE_URL = env.str("ELEXON_BASE_URL", default="https://data.elexon.co.uk/bmrs/api/v1")
ELEXON_TIMEOUT_S = env.float("ELEXON_TIMEOUT_S", default=60.0)
ELEXON_CHUNK_DAYS = env.int("ELEXON_CHUNK_DAYS", default=7)
ELEXON_MAX_ATTEMPTS = env.int("ELEXON_MAX_ATTEMPTS", default=5)
ELEXON_USER_AGENT = "engrid-wind-forecast-monitor/0.1 (+offline ingestion)"
ENGRID_SNAPSHOT_DIR = BASE_DIR / "data" / "snapshots"
