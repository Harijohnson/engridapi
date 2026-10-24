import itertools
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from apps.market_data.clients.elexon import iter_date_windows, iter_datetime_windows
from apps.market_data.parsers import RowError, parse_actuals, parse_forecasts, parse_utc

pytestmark = pytest.mark.unit


def windfor(**overrides: Any) -> dict[str, Any]:
    row = {
        "dataset": "WINDFOR",
        "publishTime": "2026-01-14T09:30:00Z",
        "startTime": "2026-01-15T18:00:00Z",
        "generation": 9053,
    }
    row.update(overrides)
    return row


def fuelhh(**overrides: Any) -> dict[str, Any]:
    row = {
        "dataset": "FUELHH",
        "publishTime": "2026-01-15T18:30:00Z",
        "startTime": "2026-01-15T18:00:00Z",
        "settlementDate": "2026-01-15",
        "settlementPeriod": 37,
        "fuelType": "WIND",
        "generation": 7672,
    }
    row.update(overrides)
    return row


def test_parses_real_payload_shape() -> None:
    result = parse_forecasts([windfor()])
    assert not result.rejected
    row = result.rows[0]
    assert row.publish_time == datetime(2026, 1, 14, 9, 30, tzinfo=UTC)
    assert row.start_time.tzinfo is not None
    assert row.generation_mw == 9053


def test_naive_timestamps_are_rejected() -> None:
    with pytest.raises(RowError):
        parse_utc("2026-01-15T18:00:00", "startTime")


def test_offsets_are_normalised_to_utc() -> None:
    assert parse_utc("2026-07-01T19:00:00+01:00", "t") == datetime(2026, 7, 1, 18, tzinfo=UTC)


@pytest.mark.parametrize(
    "bad",
    [
        windfor(generation=None),
        windfor(generation="9053"),
        windfor(generation=True),
        windfor(generation=-1),
        windfor(generation=12.5),
        windfor(startTime="2026-01-15T18:30:00Z"),
        windfor(publishTime="yesterday"),
        windfor(dataset="FUELHH"),
    ],
)
def test_malformed_forecast_rows_are_rejected(bad: dict[str, Any]) -> None:
    result = parse_forecasts([bad])
    assert result.rows == []
    assert len(result.rejected) == 1


def test_exact_duplicate_forecasts_collapse() -> None:
    result = parse_forecasts([windfor(), windfor()])
    assert len(result.rows) == 1
    assert result.duplicates == 1


def test_conflicting_forecast_duplicate_is_rejected() -> None:
    result = parse_forecasts([windfor(), windfor(generation=1)])
    assert len(result.rows) == 1
    assert result.rows[0].generation_mw == 9053
    assert len(result.rejected) == 1


@pytest.mark.parametrize(
    "bad",
    [
        fuelhh(settlementPeriod=0),
        fuelhh(settlementPeriod=51),
        fuelhh(settlementPeriod="37"),
        fuelhh(fuelType="CCGT"),
        fuelhh(startTime="2026-01-15T18:10:00Z"),
        fuelhh(settlementDate="2026-02-15"),
        fuelhh(settlementDate="nope"),
    ],
)
def test_malformed_actual_rows_are_rejected(bad: dict[str, Any]) -> None:
    result = parse_actuals([bad], fuel_type="WIND")
    assert result.rows == []
    assert len(result.rejected) == 1


def test_settlement_period_50_is_valid() -> None:
    # Autumn clock-change days have 50 periods; never assume 48.
    assert parse_actuals([fuelhh(settlementPeriod=50)], fuel_type="WIND").rows


def test_republished_actual_latest_publish_wins() -> None:
    newer = fuelhh(publishTime="2026-01-15T20:00:00Z", generation=7700)
    result = parse_actuals([newer, fuelhh()], fuel_type="WIND")
    assert [r.generation_mw for r in result.rows] == [7700]
    assert result.duplicates == 1


def test_datetime_windows_are_contiguous() -> None:
    start = datetime(2025, 12, 27, tzinfo=UTC)
    end = datetime(2026, 2, 1, tzinfo=UTC)
    windows = list(iter_datetime_windows(start, end, timedelta(days=7)))
    assert windows[0][0] == start
    assert windows[-1][1] == end
    assert all(a[1] == b[0] for a, b in itertools.pairwise(windows))
    assert len(windows) == 6


def test_datetime_windows_need_aware_bounds() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        list(iter_datetime_windows(datetime(2026, 1, 1), datetime(2026, 1, 2), timedelta(days=1)))  # noqa: DTZ001


def test_date_windows_are_inclusive_and_disjoint() -> None:
    windows = list(iter_date_windows(date(2026, 1, 1), date(2026, 1, 31), 7))
    assert windows[0] == (date(2026, 1, 1), date(2026, 1, 7))
    assert windows[-1] == (date(2026, 1, 29), date(2026, 1, 31))
    assert sum((b - a).days + 1 for a, b in windows) == 31
