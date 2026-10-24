# Elexon raw-data snapshot (D10)

Real Elexon Insights API responses, stored gzipped **exactly as returned** (inside a small JSON
envelope with the request metadata). `manage.py load_snapshot` seeds any database (local, CI,
PythonAnywhere) from these files without calling Elexon; the golden tests read them directly.

| File | Dataset | Request | Rows |
| --- | --- | --- | --- |
| `windfor.json.gz` | WINDFOR (wind generation forecast) | `GET https://data.elexon.co.uk/bmrs/api/v1/datasets/WINDFOR/stream?publishDateTimeFrom=…&publishDateTimeTo=…`, overall window `2025-12-27T00:00:00Z` → `2026-02-01T00:00:00Z`, fetched in 7-day chunks | 21,024 (288 publishes) |
| `fuelhh_wind.json.gz` | FUELHH, `fuelType=WIND` | `GET https://data.elexon.co.uk/bmrs/api/v1/datasets/FUELHH/stream?settlementDateFrom=…&settlementDateTo=…&fuelType=WIND`, settlement dates `2026-01-01` → `2026-01-31` | 1,488 |

- **Fetched:** 2026-10-24 (the exact `fetched_at` timestamp is inside each file).
- **No API key** is needed for these endpoints.
- The WINDFOR publish window starts 5 days before 1 January because forecast horizons reach
  about 67 hours (PLAN.md §9.1).
- FUELHH is filtered by **settlement date**, never by publish date (publish-date filters spill
  over the month edges).

## Envelope

```json
{"dataset": "WINDFOR", "endpoint": "https://…/datasets/WINDFOR/stream",
 "request_params": {"publishDateTimeFrom": "…", "publishDateTimeTo": "…"},
 "fetched_at": "2026-10-24T…Z", "row_count": 21024, "data": [ …raw Elexon rows… ]}
```

## Refreshing

```bash
uv run python manage.py ingest_elexon --dataset windfor --from 2025-12-27T00:00Z --to 2026-02-01T00:00Z --save-snapshot
uv run python manage.py ingest_elexon --dataset fuelhh --from 2026-01-01 --to 2026-01-31 --save-snapshot
uv run python manage.py validate_data --strict
```

The gzip files are written with a fixed mtime, so the same content gives the same bytes.
