"""Test settings: SQLite (the production engine, D4), deterministic and offline."""

import os

os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-not-a-secret")

from .base import *  # noqa: F403
from .base import REST_FRAMEWORK

DEBUG = False
ALLOWED_HOSTS = ["testserver", "localhost"]
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "engridapi-test",
    }
}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    "DEFAULT_THROTTLE_RATES": {"anon": "10000/min"},
    "NUM_PROXIES": None,
}
ELEXON_BASE_URL = "https://elexon.test/bmrs/api/v1"
ELEXON_MAX_ATTEMPTS = 3
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"null": {"class": "logging.NullHandler"}},
    "root": {"handlers": ["null"], "level": "WARNING"},
}
