"""Template for the PythonAnywhere WSGI file (Web tab -> "WSGI configuration file").

Copy the body of this file into /var/www/<username>_pythonanywhere_com_wsgi.py and replace
<username>. It contains no secrets: configuration comes from engridapi/.env on the server.
"""

import os
import sys

PROJECT_DIR = "/home/<username>/engrid/engridapi"

if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

os.environ["DJANGO_SETTINGS_MODULE"] = "engridapi.settings.prod"

from django.core.wsgi import get_wsgi_application  # noqa: E402

application = get_wsgi_application()
