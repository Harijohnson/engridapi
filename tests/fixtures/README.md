# Test fixtures (FIXTURES, not the analysis dataset)

Small slices of real Elexon responses, cut from `data/snapshots/` on 2026-10-24, used to mock the
Elexon API in tests (respx). They are test inputs only and are never shown as results.

| File | Contents |
| --- | --- |
| `windfor_two_publishes_2026-01-14.json` | WINDFOR rows of the publishes at 2026-01-14 09:30Z and 11:30Z (73 rows each) |
| `fuelhh_wind_2026-01-15.json` | FUELHH `fuelType=WIND` rows for settlement date 2026-01-15 (48 rows) |
