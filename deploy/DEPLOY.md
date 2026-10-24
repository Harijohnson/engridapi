# Deploying the API to PythonAnywhere (no containers; PLAN.md D5, Phase 7)

Replace `<username>` with your PythonAnywhere username everywhere below. Paths assume the repo
is cloned to `/home/<username>/engrid` with the backend in `engridapi/`.

> **Not yet verified on a real PythonAnywhere account.** These steps follow PythonAnywhere's docs
> (PLAN.md §9.4). Record anything that differs here during Phase 7.

## First deploy

1. **Bash console:** clone the public repo.
   ```bash
   git clone https://github.com/<owner>/<repo>.git ~/engrid
   cd ~/engrid/engridapi
   ```
2. **Virtualenv (Python 3.12)** and runtime dependencies only. `--no-cache-dir` matters: the
   pip cache counts against the free plan's 512 MB.
   ```bash
   python3.12 -m venv ~/.virtualenvs/engrid
   source ~/.virtualenvs/engrid/bin/activate
   pip install --no-cache-dir -r requirements.txt
   du -sh ~/.virtualenvs/engrid ~   # stay well under 512 MB
   ```
3. **`.env`** next to `manage.py` (never committed). Start from `.env.example`:
   ```bash
   cp .env.example .env
   python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"
   nano .env
   ```
   Set at least:
   ```ini
   DJANGO_SETTINGS_MODULE=engridapi.settings.prod
   DJANGO_SECRET_KEY=<the generated key>
   DJANGO_DEBUG=false
   DJANGO_ALLOWED_HOSTS=<username>.pythonanywhere.com
   DJANGO_CSRF_TRUSTED_ORIGINS=https://<username>.pythonanywhere.com
   DATABASE_URL=sqlite:////home/<username>/engrid/engridapi/db.sqlite3
   ```
4. **Database, static files, data** (offline; uses console CPU seconds, not web time):
   ```bash
   export DJANGO_SETTINGS_MODULE=engridapi.settings.prod
   python manage.py migrate
   python manage.py collectstatic --noinput
   python manage.py load_snapshot
   python manage.py validate_data --strict
   ```
5. **Web tab:**
   - Add a new web app → **Manual configuration** → **Python 3.12**.
   - *Virtualenv:* `/home/<username>/.virtualenvs/engrid`.
   - *Source code / working directory:* `/home/<username>/engrid/engridapi`.
   - *WSGI configuration file:* replace its contents with
     [`pythonanywhere_wsgi.py`](pythonanywhere_wsgi.py) (with `<username>` filled in).
   - *Static files:* URL `/static/` → directory `/home/<username>/engrid/engridapi/staticfiles`.
   - *Security:* enable **Force HTTPS**.
   - **Reload**.
6. **Check:**
   ```bash
   curl -s https://<username>.pythonanywhere.com/health    # {"status":"ok"}
   curl -s https://<username>.pythonanywhere.com/ready     # forecast_rows 21024, actual_rows 1488
   curl -s https://<username>.pythonanywhere.com/api/v1/meta | head -c 300
   ```

## Proxy headers and throttling (D17; do this once Vercel is live)

Browser requests reach Django through Vercel's rewrite **and** PythonAnywhere's front end.
DRF's throttle must key on the real client IP:

1. Temporarily log `X-Forwarded-For`, `X-Forwarded-Proto` and `Host` for a request made via
   the Vercel URL (for example with a one-off log line in a console-run test, or the server log).
2. If `X-Forwarded-For` looks like `client, vercel-edge`, set `API_NUM_PROXIES=2` in `.env`.
3. Add any `Host` value Vercel sends to `DJANGO_ALLOWED_HOSTS`.
4. Only after confirming `X-Forwarded-Proto: https` arrives, consider enabling
   `SECURE_PROXY_SSL_HEADER` and HSTS in `settings/prod.py`.
5. Reload and record the observed headers in PLAN.md (Phase 7).

## Updating

```bash
cd ~/engrid && git pull
source ~/.virtualenvs/engrid/bin/activate
cd engridapi
pip install --no-cache-dir -r requirements.txt
export DJANGO_SETTINGS_MODULE=engridapi.settings.prod
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py load_snapshot        # only if data/snapshots changed (idempotent anyway)
```
Then **Reload** on the Web tab.

## Keep it alive

Free web apps **expire after one month** unless renewed: Web tab → "Run until 3 months from
today". Do this right before submission and before any review window.

## Logs

Web tab → *Log files*: the error log holds Django's JSON log lines (each with a `request_id`;
the same id is returned in the `X-Request-ID` response header).
