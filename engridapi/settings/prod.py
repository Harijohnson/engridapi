"""Production settings (PythonAnywhere, D5).

`SECURE_PROXY_SSL_HEADER` and HSTS stay off until PythonAnywhere's forwarded headers are
checked in Phase 7 (PLAN.md §5.9). PythonAnywhere's "Force HTTPS" handles redirects meanwhile.
"""

from .base import *  # noqa: F403
from .base import env

DEBUG = False
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS")

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"request_id": {"()": "apps.core.logging.RequestIdFilter"}},
    "formatters": {"json": {"()": "apps.core.logging.JsonFormatter"}},
    "handlers": {
        "stderr": {
            "class": "logging.StreamHandler",
            "filters": ["request_id"],
            "formatter": "json",
        }
    },
    "root": {"handlers": ["stderr"], "level": env.str("DJANGO_LOG_LEVEL", default="INFO")},
}
