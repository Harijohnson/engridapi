# Backend (engridapi): rules for every change

Read the root [../CLAUDE.md](../CLAUDE.md) and [../PLAN.md](../PLAN.md) first. This service **owns all business logic**: ingestion of Elexon data, forecast selection at a decision cutoff, matching to actuals, error metrics and the public API. The Next.js app in [../engrid/](../engrid/) only displays results.

**Ask, don't assume.** If a business rule, library, endpoint shape or naming is not settled in PLAN.md (§3 rules, §4 Decision Log, §5 architecture), ask the owner first. Anything that depends on an open decision (PLAN.md §4.0) must not be built until it is `RESOLVED`. D1 (policy defaults) and D4 (SQLite) were resolved on 2026-10-24.

## Current state (verified 2026-10-24)

- Django **6.1.2** on Python **3.12.10**, managed with **uv** (`.venv/`, `pyproject.toml`, `uv.lock`). The old pip virtualenv `venv/` is unused; ask before deleting it.
- Settings are split into `engridapi/settings/{base,dev,test,prod}.py`, configured from env / `.env` via django-environ. `SECRET_KEY` comes from `DJANGO_SECRET_KEY` (the old hard-coded key is burned). `TIME_ZONE = "UTC"` and `USE_TZ = True` (keep both exactly as they are).
- Phases 0–6 are implemented (PLAN.md §7.1 has the record). The real January 2026 data is in `data/snapshots/`.
- Django 6.x has APIs that differ from older versions (for example the `MAILERS` setting in the generated settings). **Check the 6.1 docs (docs.djangoproject.com/en/6.1/) before using an API from memory.** Verified example: `CheckConstraint` accepts only `condition=` (no `check=`).
- **Deployment target: PythonAnywhere, no containers** (PLAN.md D5, D19). There is no gunicorn and no whitenoise: PythonAnywhere runs the WSGI app and serves static files through its own mapping.

## Approved libraries (PLAN.md D3)

| Purpose | Library |
| --- | --- |
| API + OpenAPI | `djangorestframework`, `drf-spectacular` |
| Analytics | `pandas`, `numpy` |
| Elexon HTTP | `httpx` + `tenacity` |
| Config | `django-environ` |
| Tests (dev) | `pytest`, `pytest-django`, `pytest-cov`, `hypothesis`, `respx` |
| Quality (dev) | `ruff`, `mypy`, `django-stubs`, `djangorestframework-stubs`, `pandas-stubs` |

Anything else needs the owner's OK first. Keep runtime dependencies lean: PythonAnywhere's free disk is **512 MB**.

## Commands (uv, D7)

```bash
uv sync                                   # install from uv.lock (CI: uv sync --locked)
uv run python manage.py runserver
uv run pytest                             # tests + coverage
uv run ruff check . && uv run ruff format --check .
uv run mypy .
uv run python manage.py spectacular --file schema.yml   # OpenAPI contract for the frontend
uv run python manage.py load_snapshot     # seed DB from the committed Elexon snapshot (D10)
uv run python manage.py validate_data     # coverage / duplicates / gaps report
uv run python manage.py ingest_elexon --dataset windfor --from 2025-12-27T00:00Z --to 2026-02-01T00:00Z
uv run python manage.py ingest_elexon --dataset fuelhh --from 2026-01-01 --to 2026-01-31
uv export --locked --no-dev --format requirements.txt -o requirements.txt   # for PythonAnywhere; commit it
```

Flags verified against uv 0.11.7. CI must fail if `requirements.txt` drifts from `uv.lock` (re-export and `git diff --exit-code`).

## Architecture (PLAN.md §5.2–5.3)

```text
engridapi/                 project package: settings/{base,dev,test,prod}.py, urls.py, wsgi.py, asgi.py
apps/core/                 health + readiness, error handler (RFC 9457), pagination, request-id, time utils
apps/market_data/          models, Elexon client, parsers/validators, ingestion service, management commands
apps/analytics/domain/     PURE functions: cutoff.py, selection.py, matching.py, metrics.py
apps/analytics/selectors.py   ORM reads → pandas DataFrames
apps/analytics/services.py    orchestration + caching
apps/analytics/api/        params.py, serializers.py, views.py, urls.py
deploy/                    pythonanywhere_wsgi.py (template), DEPLOY.md (runbook)
data/snapshots/            gzipped raw Elexon JSON + README (source, params, fetch date)
tests/                     mirrors apps/: unit/, integration/, api/, fixtures/
```

### Layering: who may import whom

| Layer | May use | Must NOT |
| --- | --- | --- |
| `api/` | services, serializers | ORM queries, pandas maths, Elexon calls |
| `services.py` | domain, selectors, cache | `request`/`Response` objects, formatting |
| `domain/` | stdlib, pandas, numpy | **anything from Django**, the database, network, settings, logging config |
| `selectors.py` | models | business rules |
| ingestion | Elexon client, validators, models | API layer |

If you need to break a rule, stop and ask. Do not add "just one" ORM call to a view.

## Domain rules (PLAN.md §3, enforced in code and tests)

- **No look-ahead (R1):** eligible ⇔ `publish_time <= cutoff`, and `cutoff <= start_time`. After selection, **assert** this for every selected row and fail loudly if it is violated.
- **Latest eligible wins (R2):** max `publish_time` among eligible versions.
- **Missing is explicit (R3/R4):** status `missing_forecast` / `missing_actual`; excluded from metrics; counted in coverage. Never interpolate, forward-fill, back-fill or default to 0.
- **Errors (R5/R6):** `error_mw = forecast_mw - actual_mw`. Headline MAE, RMSE and bias, plus n, coverage, median, P90 and max absolute error, average forecast and average actual (D15). Matched rows only. `n == 0` gives `None` for every metric. Never divide by zero or return NaN in JSON.
- **Cutoff policies (D1, resolved):** `daily_gate` (`C = (UK delivery date - k days) at HH:MM Europe/London`, default D-1 10:00, `k` 1–3) and `lead_time` (`C = T - H`, `H` in 0–48, default 24). Defaults live in settings (`ENGRID_*`) and are served via `/api/v1/meta`; the dashboard opens on the daily gate.
- **Matching (D2, resolved):** join the selected hourly forecast to the actual half-hour with the **same `start_time`** (the `HH:00` half-hour). Keep it behind a `MatchingMethod` interface so a later change is local.
- **Auditability (R8):** every analytics result carries policy, parameters, per-row `cutoff_time`, `publish_time` and `lead_time_h`, the evaluation window and `data_version`.

## Time handling (most common source of bugs here)

- Every datetime is **timezone-aware UTC**. Use `django.utils.timezone.now()` or `datetime.now(UTC)`, never naive `datetime.now()` / `utcnow()`.
- Parse Elexon timestamps (`"2026-01-15T18:00:00Z"`) into aware UTC and reject naive values.
- UK-local concepts (delivery date, gate times) use `zoneinfo.ZoneInfo("Europe/London")` and convert back to UTC immediately. January is GMT (= UTC), but the code must be correct across DST, so test March and October transitions.
- Settlement periods run **1–50** (clock-change days have 46 or 50). Never hard-code 48.
- **Never hard-code the forecast publish schedule.** Use real `publishTime` values: Elexon's documented times are off by one hour versus January data, and irregular publishes exist (12:25 on 2026-01-15).
- API output: ISO-8601 with `Z`.

## Naming and code style

- **Unit suffixes in names:** `generation_mw`, `error_mw`, `lead_time_h`, `cutoff_time`, `publish_time`. No bare `value` or `gen`.
- Type hints on every function signature; `mypy` must pass. Use `Enum`/`StrEnum` for closed sets (`CutoffPolicyKind`, `ForecastStatus`); frozen `dataclass` for value objects (`CutoffPolicy`, `MetricSummary`).
- pandas DataFrames crossing a function boundary have their **columns documented in the docstring** (name, dtype, unit, tz). Validate required columns at the domain entry point.
- No magic numbers: constants (evaluation window, max lead time, chunk size, timeouts) live in settings or a `constants.py`.
- Small, single-purpose functions. Prefer vectorised pandas over Python loops over rows.
- `ruff` (lint and format) is the style authority. No `print()`; use `logging.getLogger(__name__)`.

## Database and migrations

- **Engine: SQLite (D4, resolved 2026-10-24)**, because PythonAnywhere's free plan has no PostgreSQL and no MySQL for new accounts. Keep the ORM code **engine-agnostic** so a later move is config only: configure through `DATABASE_URL`, no raw SQL, no PostgreSQL-only features (`DISTINCT ON`, array fields) in the core path. Tests run on the production engine.
- Integrity lives in the **database**: `UniqueConstraint(start_time, publish_time)` on forecasts, `UniqueConstraint(fuel_type, start_time)` on actuals, `CheckConstraint(condition=...)` for ranges. Add composite indexes for the real query patterns (`(start_time, publish_time)`).
- Every migration is committed and reviewed. **Never edit a migration that has been applied or pushed**; add a new one. Data migrations must be reversible or say explicitly why not.
- Forecast rows are **immutable**: insert-ignore on conflict. A conflicting value for an existing key is a data-quality error to log, not to overwrite.
- Actual rows may be republished: upsert, keeping the latest `publish_time`.
- Use `bulk_create(..., ignore_conflicts=True / update_conflicts=True, unique_fields=..., update_fields=...)` in batches. No row-by-row saves in loops. Check that the chosen engine supports each option.

## Ingestion (offline only, never on the request path, R11)

- **Default data path is the committed snapshot** (`load_snapshot`, D10). It is deterministic, needs no network, and is what production uses.
- Live `ingest_elexon` also works on PythonAnywhere (`data.elexon.co.uk` is on its allowlist), but console runs consume the free plan's **100 CPU-seconds/day**, so keep it efficient.
- One client method per dataset in `market_data/clients/elexon.py`: `httpx` with **explicit timeouts**, `tenacity` retry with exponential backoff on 429/5xx and network errors, and a descriptive `User-Agent`.
- FUELHH: filter by **settlement date** (publish-date filters spill over month edges and cannot be combined with settlement filters). Always pass `fuelType=WIND`.
- WINDFOR: filter by **publish time**; the window must start ≥ 72 h before the first delivery hour (horizons reach 66.5 h).
- Request in **chunks** (for example 7-day windows) so a failure is resumable and small.
- Validate every row (types, tz, ranges, duplicates) **before** writing. Rejected rows are counted and logged with reasons. The snapshot loader and the API path share the same parser and validator.
- Each run writes an `IngestionRun` row (source, params, counts, checksum, status, error). Runs are **idempotent**: re-running the same window creates 0 new rows.
- One transaction per chunk; never delete existing data because a run failed.

## API (DRF + drf-spectacular)

- URL-versioned under `/api/v1/`. Breaking changes go to `/api/v2/`, never silently.
- **Thin views:** parse and validate params → call one service → serialise. No logic in views or serializers.
- Query params validated by a params class: dates within the configured evaluation window (2026-01-01..2026-01-31), `date_from <= date_to`, policy parameters consistent. Invalid input returns **422** with **RFC 9457** `application/problem+json`. Never return a 500 for bad input.
- Every endpoint is annotated (`@extend_schema`) with request params and response schema. **The OpenAPI schema is the contract**: the frontend generates its types from it, so CI fails if schema generation fails.
- Responses include `query` (normalised params echoed back) and `data_version`.
- List endpoints: pagination, plus `ordering` from an explicit **whitelist**.
- **Caching (D16):** service-level cache (local-memory backend) keyed by `(endpoint, normalised params, data_version)`; responses send `Cache-Control: public, max-age=…, s-maxage=…` and an `ETag` from `data_version`.
- **Throttling (D17):** DRF anonymous throttle. Requests arrive via **Vercel's rewrite proxy and PythonAnywhere's front end**, so configure `NUM_PROXIES` from the headers actually observed in Phase 7. Never trust a client-supplied IP header blindly.
- **No CORS.** The browser only talks to Vercel same-origin, and Vercel proxies to us (D8). Do not add `django-cors-headers`.
- `/health` = liveness (no DB). `/ready` = DB reachable **and** data present.

## Performance and scalability

- The API is **stateless**. On a bigger host it scales by adding WSGI workers or instances with no code changes. No module-level mutable state that matters (the local-memory cache is a cache, never a source of truth).
- **PythonAnywhere free has one web worker:** keep every endpoint fast, avoid heavy per-request recomputation (cache it), and never block on network I/O in a request.
- Read only the columns you need (`.values_list()` / `.values()` into DataFrames). No N+1 queries; check with `assertNumQueries` in tests.
- **Measure before optimising** (Phase 4 records p95 locally, Phase 7 on PythonAnywhere). The growth path (PostgreSQL, SQL-side selection, materialised selections) changes services and config only, never the API contract.

## Security and configuration

- All config via env vars through **django-environ**, which also reads a `.env` file (that is how PythonAnywhere gets its config). Split settings `base/dev/test/prod`. Commit only `.env.example`, with every variable documented.
- **Generate a new `SECRET_KEY` and move it to env before the first public push.** The current one is in the file and must be treated as burned.
- Production: `DEBUG=False`; `ALLOWED_HOSTS` includes `<username>.pythonanywhere.com` (plus whatever `Host` Vercel's rewrite sends, verified in Phase 7); `CSRF_TRUSTED_ORIGINS`; secure cookies. Set `SECURE_PROXY_SSL_HEADER` and HSTS **only after verifying** PythonAnywhere's forwarded headers.
- **Django admin is disabled in production** (D17): don't mount `admin/` when `DEBUG=False`.
- Never log secrets or full request headers. Never `eval`/`exec` input. Raw SQL only with parameters.

## Errors and logging

- Domain code raises specific exceptions (`NoEligibleForecastError`, `InvalidCutoffPolicyError`, …). The API layer maps them to 4xx problem responses; unexpected exceptions become 500 with a request id and are logged with a stack trace.
- Never swallow exceptions (`except Exception: pass` is banned).
- Structured logs (JSON in prod) to stderr with a request id. On PythonAnywhere they appear in the web app's error/server logs. Ingestion logs counts per chunk.

## Testing (pytest; PLAN.md §6.1)

- Layout mirrors `apps/`. Markers: `unit` (no DB), `integration` (DB), `api`. Domain tests must not touch the DB.
- **No real network in tests.** Mock Elexon with `respx` and recorded fixtures in `tests/fixtures/`.
- **Required tests, never delete:**
  - the brief's example: versions at Jan 14 08:00 (5,000), Jan 14 12:00 (4,800), Jan 15 08:00 (4,600) with cutoff Jan 14 10:00 selects **5,000**
  - `publish_time == cutoff` is eligible (inclusive)
  - no eligible version gives `missing_forecast`; past-dated rows (`start < publish`) are never selected
  - metrics: errors `[+500, -300, +100]` give MAE 300, RMSE ≈ 341.57, bias +100; `n == 0` gives `None`
  - **property test (Hypothesis):** every selected `publish_time <= cutoff <= start_time`, and it is the max eligible
  - **golden real-data test** for 2026-01-15 18:00Z (PLAN.md §2.4)
  - ingestion idempotency (second run creates 0 rows); retry on 429/5xx; malformed rows rejected; snapshot path equals API path
  - API: 422 for out-of-window dates and inconsistent policy params; schema generation succeeds; throttle keys on the forwarded client IP
- Tests are deterministic: fixed data, no `now()` without freezing, no ordering assumptions without `ORDER BY`.
- Coverage: ≥ 90 % for `analytics/domain` and `services`, ≥ 80 % overall. Coverage is a floor, not the goal.

## Deployment: PythonAnywhere (no containers; PLAN.md D5, D19, Phase 7)

- Code arrives by `git clone` / `git pull` of the public repo in a PythonAnywhere Bash console (GitHub is on the allowlist).
- Python **3.12** virtualenv on the server; install with `pip install --no-cache-dir -r requirements.txt` (the exported file, never dev dependencies). Watch disk usage against the 512 MB quota.
- `.env` lives on the server next to the project and is **never committed**. `deploy/pythonanywhere_wsgi.py` is the committed template for the WSGI file. It sets `DJANGO_SETTINGS_MODULE=engridapi.settings.prod` and contains no secrets.
- Deploy/update sequence (keep `deploy/DEPLOY.md` current): `git pull` → `pip install --no-cache-dir -r requirements.txt` → `migrate` → `collectstatic` → (`load_snapshot` if data changed) → reload the web app.
- Static files: PythonAnywhere static mapping `/static/` → `STATIC_ROOT`.
- **Free web apps expire monthly:** extend before submission and before any review window.
- Never run long or CPU-heavy work inside a web request; use management commands from a console.

## Definition of Done (every backend change)

- [ ] `ruff check`, `ruff format --check`, `mypy` and `pytest` are green; coverage floors hold
- [ ] Layering rules respected; domain code has no Django imports
- [ ] New or changed endpoints annotated; OpenAPI schema regenerates (tell the frontend to regenerate its types)
- [ ] Migrations created, committed, reviewed
- [ ] Time handling tz-aware; units in names; no hard-coded schedule or period count
- [ ] `requirements.txt` re-exported if dependencies changed
- [ ] PLAN.md checkboxes or decision log updated; Conventional Commit message

## Never

- Never call Elexon during an API request.
- Never select a forecast published after the cutoff, and never "fix" missing data by inventing values.
- Never commit secrets, `venv/`, `.venv/`, `db.sqlite3` or `.env`.
- Never edit applied migrations or disable a failing test to get green; fix the cause or ask.
- Never add gunicorn, whitenoise, Docker files or CORS packages unless the owner changes D3, D8 or D19.
