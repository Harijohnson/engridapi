"""Strict parsing and validation of raw Elexon rows.

The live API path and the snapshot loader share these functions, so both produce identical
rows. Every rejected row is kept with a reason; nothing is silently dropped or coerced.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

MIN_SETTLEMENT_PERIOD = 1
MAX_SETTLEMENT_PERIOD = 50  # clock-change days have 46 or 50 periods; never assume 48
MAX_PLAUSIBLE_GENERATION_MW = 100_000


@dataclass(frozen=True)
class ForecastRow:
    publish_time: datetime
    start_time: datetime
    generation_mw: int


@dataclass(frozen=True)
class ActualRow:
    start_time: datetime
    settlement_date: date
    settlement_period: int
    fuel_type: str
    generation_mw: int
    publish_time: datetime


@dataclass(frozen=True)
class RejectedRow:
    raw: dict[str, Any]
    reason: str


@dataclass
class ParseResult[RowT]:
    rows: list[RowT] = field(default_factory=list)
    rejected: list[RejectedRow] = field(default_factory=list)
    duplicates: int = 0


class RowError(ValueError):
    pass


def parse_utc(value: object, name: str) -> datetime:
    """Parse an Elexon ISO-8601 timestamp into an aware UTC datetime. Naive values are rejected."""
    if not isinstance(value, str) or not value:
        raise RowError(f"{name} must be a non-empty ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise RowError(f"{name} is not ISO-8601: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RowError(f"{name} has no time zone: {value!r}")
    return parsed.astimezone(UTC)


def _parse_generation(value: object) -> int:
    # bool is an int subclass; reject it explicitly. Floats are accepted only if integral.
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise RowError(f"generation must be a number, got {value!r}")
    if isinstance(value, float) and not value.is_integer():
        raise RowError(f"generation must be integral MW, got {value!r}")
    generation_mw = int(value)
    if not 0 <= generation_mw <= MAX_PLAUSIBLE_GENERATION_MW:
        raise RowError(f"generation {generation_mw} MW is outside 0..{MAX_PLAUSIBLE_GENERATION_MW}")
    return generation_mw


def _expect_dataset(raw: dict[str, Any], expected: str) -> None:
    if raw.get("dataset") != expected:
        raise RowError(f"dataset must be {expected}, got {raw.get('dataset')!r}")


def parse_forecast_row(raw: dict[str, Any]) -> ForecastRow:
    _expect_dataset(raw, "WINDFOR")
    start_time = parse_utc(raw.get("startTime"), "startTime")
    if start_time.minute or start_time.second or start_time.microsecond:
        raise RowError(f"WINDFOR startTime must be on the hour, got {raw.get('startTime')!r}")
    return ForecastRow(
        publish_time=parse_utc(raw.get("publishTime"), "publishTime"),
        start_time=start_time,
        generation_mw=_parse_generation(raw.get("generation")),
    )


def parse_actual_row(raw: dict[str, Any], *, fuel_type: str) -> ActualRow:
    _expect_dataset(raw, "FUELHH")
    if raw.get("fuelType") != fuel_type:
        raise RowError(f"fuelType must be {fuel_type}, got {raw.get('fuelType')!r}")
    start_time = parse_utc(raw.get("startTime"), "startTime")
    if start_time.minute % 30 or start_time.second or start_time.microsecond:
        raise RowError(f"FUELHH startTime must be on a half hour, got {raw.get('startTime')!r}")
    period = raw.get("settlementPeriod")
    if isinstance(period, bool) or not isinstance(period, int):
        raise RowError(f"settlementPeriod must be an integer, got {period!r}")
    if not MIN_SETTLEMENT_PERIOD <= period <= MAX_SETTLEMENT_PERIOD:
        raise RowError(f"settlementPeriod {period} is outside 1..50")
    settlement_date_raw = raw.get("settlementDate")
    try:
        settlement_date = date.fromisoformat(str(settlement_date_raw))
    except ValueError as exc:
        raise RowError(f"settlementDate is not a date: {settlement_date_raw!r}") from exc
    # The settlement day can only differ from the UTC date of the period by a few hours.
    if abs(start_time.date() - settlement_date) > timedelta(days=1):
        raise RowError("settlementDate is inconsistent with startTime")
    return ActualRow(
        start_time=start_time,
        settlement_date=settlement_date,
        settlement_period=period,
        fuel_type=fuel_type,
        generation_mw=_parse_generation(raw.get("generation")),
        publish_time=parse_utc(raw.get("publishTime"), "publishTime"),
    )


def parse_forecasts(raw_rows: list[dict[str, Any]]) -> ParseResult[ForecastRow]:
    """Parse WINDFOR rows. Exact duplicates (from overlapping chunks) are collapsed; a repeated
    key with a different value is rejected as a data-quality error."""
    result: ParseResult[ForecastRow] = ParseResult()
    seen: dict[tuple[datetime, datetime], ForecastRow] = {}
    for raw in raw_rows:
        try:
            row = parse_forecast_row(raw)
        except RowError as exc:
            result.rejected.append(RejectedRow(raw=raw, reason=str(exc)))
            continue
        key = (row.start_time, row.publish_time)
        previous = seen.get(key)
        if previous is None:
            seen[key] = row
            result.rows.append(row)
        elif previous == row:
            result.duplicates += 1
        else:
            result.rejected.append(
                RejectedRow(raw=raw, reason="conflicting value for an existing (start, publish)")
            )
    return result


def parse_actuals(raw_rows: list[dict[str, Any]], *, fuel_type: str) -> ParseResult[ActualRow]:
    """Parse FUELHH rows. If a half-hour appears more than once, the latest publish wins."""
    result: ParseResult[ActualRow] = ParseResult()
    latest: dict[datetime, ActualRow] = {}
    for raw in raw_rows:
        try:
            row = parse_actual_row(raw, fuel_type=fuel_type)
        except RowError as exc:
            result.rejected.append(RejectedRow(raw=raw, reason=str(exc)))
            continue
        previous = latest.get(row.start_time)
        if previous is None:
            latest[row.start_time] = row
            continue
        result.duplicates += 1
        if row.publish_time > previous.publish_time:
            latest[row.start_time] = row
    result.rows = sorted(latest.values(), key=lambda r: r.start_time)
    return result
