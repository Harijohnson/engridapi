# EnGrid API (backend)

The backend of **EnGrid: Wind Forecast Monitor**. A UK electricity supplier has to buy power *before* it knows how much wind there will be. For **January 2026**, this API answers two questions:

1. **Which wind forecast was available when the purchasing decision had to be made?**
2. **How accurate was that forecast compared with the wind power actually generated?**

It stores real data from Elexon BMRS, picks the right forecast for every delivery hour (never one published too late), matches it to the actual generation, and serves error metrics as a JSON API. The dashboard that displays it lives in a separate repo: [Harijohnson/engridweb](https://github.com/Harijohnson/engridweb).

> **New here?** Read the [wiki](https://github.com/Harijohnson/engridapi/wiki). It walks you through forking, running everything locally and the code, step by step.

## Tech stack

| What | Tool |
| --- | --- |
| Language | Python **3.12** |
| Web framework | Django 6.1 + Django REST Framework |
| API docs / contract | drf-spectacular (OpenAPI 3), served at `/api/docs` |
| Number crunching | pandas + numpy |
| Talking to Elexon | httpx + tenacity (timeouts and retries) |
| Config | django-environ (reads env vars and a `.env` file) |
| Database | **SQLite** locally and in tests, **PostgreSQL** in production (both set through `DATABASE_URL`) |
| Package manager | [uv](https://docs.astral.sh/uv/) |
| Production server | gunicorn on **Render.com** |
| Tests and quality | pytest, Hypothesis, respx, ruff, mypy (strict) |

## Architecture

```mermaid
flowchart LR
    subgraph offline["Offline (management commands, never during a web request)"]
        E[Elexon BMRS API] -->|ingest_elexon| P[parsers.py<br/>validate every row]
        S[data/snapshots/*.json.gz<br/>committed real data] -->|load_snapshot| P
        P --> I[ingestion.py<br/>idempotent writes] --> DB[(Database)]
    end
    subgraph request["On every API request"]
        V[api/views.py<br/>thin] --> PA[api/params.py<br/>validate query] --> SV[services.py<br/>orchestrate + cache]
        SV --> SE[selectors.py<br/>ORM to DataFrame] --> DB
        SV --> D[domain/<br/>pure pandas logic]
        SV --> SZ[api/serializers.py] --> J[JSON response]
    end
    FE[Next.js frontend] -->|/api/v1/...| V
```

The code is split into **layers**. Each layer may only use the layers below it. That keeps the business logic easy to test and hard to break.

| Layer | Folder / file | Job | Must NOT |
| --- | --- | --- | --- |
| API | `apps/analytics/api/` | Read query params, call **one** service, return JSON | Query the DB, do maths |
| Services | `apps/analytics/services.py` | Glue: load data, run the domain logic, cache results | Touch `request`/`Response` |
| Domain | `apps/analytics/domain/` | **The business rules**: cutoffs, selection, matching, metrics | Import anything from Django |
| Selectors | `apps/analytics/selectors.py` | Read rows from the DB into pandas DataFrames | Contain business rules |
| Market data | `apps/market_data/` | Models, Elexon client, parsers, ingestion, commands | Be called by a web request |
| Core | `apps/core/` | `/health`, `/ready`, error format, request ids, time helpers | Hold business logic |

```text
engridapi/                  ← this repo
├── engridapi/              Django project: settings/{base,dev,test,prod}.py, urls.py, wsgi.py
├── apps/
│   ├── core/               health/ready views, RFC 9457 error handler, logging, middleware
│   ├── market_data/        models.py, clients/elexon.py, parsers.py, ingestion.py,
│   │                       snapshots.py, management/commands/ (load_snapshot, ingest_elexon, validate_data)
│   └── analytics/
│       ├── domain/         cutoff.py, selection.py, matching.py, metrics.py, history.py, pipeline.py
│       ├── selectors.py
│       ├── services.py
│       └── api/            params.py, serializers.py, views.py, urls.py
├── data/snapshots/         real Elexon responses (gzipped) + README
├── tests/                  unit/ (no DB), integration/ (DB), api/ (HTTP), fixtures/
├── schema.yml              the OpenAPI contract (the frontend generates its types from it)
├── pyproject.toml, uv.lock dependencies (requirements.txt is exported from uv.lock)
└── deploy/                 old PythonAnywhere runbook (the backend now runs on Render)
```

## Data flow

**1. Getting data in (offline).** Two Elexon datasets are used:

- **WINDFOR**: wind generation *forecasts*. Elexon re-publishes the forecast many times, so every delivery hour has 15–23 versions, each with its own `publish_time`.
- **FUELHH** (`fuelType=WIND`): *actual* wind generation, one row per half-hour.

`load_snapshot` reads the committed files in `data/snapshots/`, so you don't need the internet or an API key. `ingest_elexon` downloads fresh data from Elexon. Both go through the **same parser and validator** and write to two tables: `WindForecast` and `GenerationActual`. Every run is logged in `IngestionRun`. Running it twice creates no duplicates.

**2. Answering a request.** For `GET /api/v1/summary?policy=daily_gate&gate_time=10:00&gate_days_before=1`:

1. `params.py` checks the query. Bad input gets a **422** with a clear message, never a 500.
2. `services.py` builds a cache key. If the answer is cached, it returns straight away.
3. `selectors.py` loads every forecast version and every actual for the date range.
4. The domain code runs `cutoff → selection → matching → metrics`:
   - **cutoff**: for each delivery hour `T`, work out the decision deadline `C`. For a daily gate that is "the day before at 10:00 UK time".
   - **selection**: keep only versions with `publish_time <= C`, and take the **latest** one. This is the **no look-ahead** rule.
   - **matching**: join each hourly forecast to the actual half-hour that **starts at the same time**.
   - **metrics**: `error = forecast - actual` (positive means over-forecast), then MAE, RMSE, bias and so on, using matched hours only.
5. The serializer turns the result into JSON. All times are UTC with a `Z`, and the response echoes the normalised `query` and a `data_version`.

The wiki's [Data flow](https://github.com/Harijohnson/engridapi/wiki/Data-Flow) page walks through a real worked example.

## API endpoints

Interactive docs (Swagger UI): <http://127.0.0.1:8000/api/docs> when running locally. Raw schema: `/api/schema`.

| Endpoint | What it returns |
| --- | --- |
| `GET /health` | `{"status":"ok"}`: the process is alive (no DB check) |
| `GET /ready` | Row counts. Returns **503** if the DB is unreachable or empty |
| `GET /api/v1/meta` | Evaluation window, policy defaults and limits, data status |
| `GET /api/v1/summary` | Headline metrics: MAE, RMSE, bias, coverage, median/P90/max error, averages |
| `GET /api/v1/timeseries` | Selected forecast per hour and actual per half-hour, for the chart |
| `GET /api/v1/intervals` | One row per delivery hour (paginated, sortable with `ordering`) |
| `GET /api/v1/accuracy/breakdown` | Metrics grouped by `group_by` = `day` or `hour_of_day` |
| `GET /api/v1/accuracy/by-lead-time` | Metrics for each required lead time from `min_h` to `max_h` hours |
| `GET /api/v1/forecasts/history` | Every published version for one `start_time`, labelled selected / superseded / after cutoff / after delivery start |

Common query params: `date_from`, `date_to` (UK dates inside January 2026), `policy` (`daily_gate` or `lead_time`), `gate_time` (`HH:MM`), `gate_days_before` (1–3), `lead_time_h` (0–48). Leave them out to get the defaults from `/api/v1/meta`.

## Run it locally (quick version)

You need **git** and **[uv](https://docs.astral.sh/uv/getting-started/installation/)**. uv downloads Python 3.12 for you. The [wiki guide](https://github.com/Harijohnson/engridapi/wiki/Fork-and-Run-Locally) explains every step, including forking and Windows specifics.

#### macOS / Linux

```bash
git clone https://github.com/<you>/engridapi.git
cd engridapi
cp .env.example .env
uv run python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"
#   → paste the printed key into .env as DJANGO_SECRET_KEY=...
uv sync
uv run python manage.py migrate
uv run python manage.py load_snapshot
uv run python manage.py validate_data
uv run python manage.py runserver
```

#### Windows (PowerShell)

```powershell
git clone https://github.com/<you>/engridapi.git
cd engridapi
Copy-Item .env.example .env
uv run python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"
#   → paste the printed key into .env as DJANGO_SECRET_KEY=...
uv sync
uv run python manage.py migrate
uv run python manage.py load_snapshot
uv run python manage.py validate_data
uv run python manage.py runserver
```

Then open <http://127.0.0.1:8000/ready>. You should see `{"status":"ready","forecast_rows":21024,"actual_rows":1488}`.

## Everyday commands

```bash
uv run pytest                                   # 127 tests + coverage (must stay ≥ 80 %)
uv run ruff check . && uv run ruff format --check .   # lint + format check
uv run ruff format .                            # auto-format
uv run mypy .                                   # strict type check
uv run python manage.py spectacular --validate --fail-on-warn --file schema.yml   # regenerate the API contract
uv export --locked --no-dev --format requirements.txt -o requirements.txt         # after changing dependencies
```

Refresh data from Elexon (optional, needs internet, no API key):

```bash
uv run python manage.py ingest_elexon --dataset windfor --from 2025-12-27T00:00Z --to 2026-02-01T00:00Z
uv run python manage.py ingest_elexon --dataset fuelhh --from 2026-01-01 --to 2026-01-31
uv run python manage.py validate_data --strict
```

## Configuration

All settings come from environment variables or a `.env` file next to `manage.py`. [`.env.example`](.env.example) documents every variable. The ones you'll touch:

| Variable | Local value | Production (Render) |
| --- | --- | --- |
| `DJANGO_SETTINGS_MODULE` | `engridapi.settings.dev` (default for `manage.py`) | `engridapi.settings.prod` |
| `DJANGO_SECRET_KEY` | any generated key | a new generated key, kept secret |
| `DJANGO_DEBUG` | `true` | `false` |
| `DJANGO_ALLOWED_HOSTS` | `127.0.0.1,localhost` | `<service>.onrender.com` |
| `DATABASE_URL` | `sqlite:///db.sqlite3` (the default) | `postgres://user:password@host:5432/dbname` |

Never commit `.env`. Only `.env.example` belongs in git.

## Rules this code follows

- **No look-ahead:** a forecast can be used for hour `T` with cutoff `C` only if `publish_time <= C <= T`. The latest such version wins. This is checked at runtime and by a property-based test.
- **Missing data is shown, never invented:** no interpolation and no filling. Missing hours are counted and left out of the metrics. With `n = 0`, metrics are `null`, not `0`.
- **UTC everywhere** in the DB and the API. UK time (`Europe/London`) is used only to work out gate times.
- **The forecast publish schedule is never hard-coded.** Real `publish_time` values are used, because Elexon's documented times don't match the data.

## Deployment

The API runs on **Render.com** as a Python web service (`gunicorn engridapi.wsgi:application`) with a PostgreSQL database. The step-by-step guide is in the wiki: [Deploy the backend to Render](https://github.com/Harijohnson/engridapi/wiki/Deploy-Backend-to-Render). The frontend on Vercel reaches it through a same-origin `/api/*` rewrite, so the API needs no CORS.

## Limitations

- The tool measures **forecast accuracy**. It does **not** calculate how much power to buy, because demand data isn't part of the datasets.
- Only January 2026 is exposed. The window is configuration (`ENGRID_EVALUATION_DATE_FROM/TO`), not code.
