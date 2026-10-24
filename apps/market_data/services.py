"""Read-side services over the stored Elexon data: coverage, data version, readiness."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from django.db.models import Count, F, Max, Min

from apps.core.time import delivery_window_utc
from apps.market_data.models import (
    Dataset,
    GenerationActual,
    IngestionRun,
    IngestionStatus,
    WindForecast,
)

FORECAST_STEP = timedelta(hours=1)
ACTUAL_STEP = timedelta(minutes=30)


@dataclass(frozen=True)
class ForecastCoverage:
    rows: int
    distinct_publishes: int
    hours_expected: int
    hours_present: int
    hours_missing: list[datetime]
    versions_per_hour_min: int | None
    versions_per_hour_max: int | None
    versions_per_hour_mean: float | None


@dataclass(frozen=True)
class ActualCoverage:
    half_hours_expected: int
    half_hours_present: int
    half_hours_missing: list[datetime]
    settlement_period_min: int | None
    settlement_period_max: int | None


@dataclass(frozen=True)
class CoverageReport:
    date_from: date
    date_to: date
    forecast: ForecastCoverage
    actual: ActualCoverage


def _expected_starts(start: datetime, end: datetime, step: timedelta) -> list[datetime]:
    starts = []
    cursor = start
    while cursor < end:
        starts.append(cursor)
        cursor += step
    return starts


def coverage_report(date_from: date, date_to: date, fuel_type: str) -> CoverageReport:
    """Coverage of both datasets over the UK delivery dates [date_from, date_to].

    A delivery hour counts as present if at least one version was published at or before its
    start (the most lenient cutoff, lead time 0 h). Later versions are never decision inputs.
    """
    window_start, window_end = delivery_window_utc(date_from, date_to)

    forecasts = WindForecast.objects.filter(start_time__gte=window_start, start_time__lt=window_end)
    eligible_versions: dict[datetime, int] = {
        row["start_time"]: row["versions"]
        for row in forecasts.filter(publish_time__lte=F("start_time"))
        .values("start_time")
        .annotate(versions=Count("id"))
    }
    expected_hours = _expected_starts(window_start, window_end, FORECAST_STEP)
    version_counts = [eligible_versions[h] for h in expected_hours if h in eligible_versions]
    forecast = ForecastCoverage(
        rows=forecasts.count(),
        distinct_publishes=forecasts.values("publish_time").distinct().count(),
        hours_expected=len(expected_hours),
        hours_present=len(version_counts),
        hours_missing=[h for h in expected_hours if h not in eligible_versions],
        versions_per_hour_min=min(version_counts) if version_counts else None,
        versions_per_hour_max=max(version_counts) if version_counts else None,
        versions_per_hour_mean=(
            round(sum(version_counts) / len(version_counts), 2) if version_counts else None
        ),
    )

    actuals = GenerationActual.objects.filter(
        fuel_type=fuel_type, start_time__gte=window_start, start_time__lt=window_end
    )
    present = set(actuals.values_list("start_time", flat=True))
    expected_half_hours = _expected_starts(window_start, window_end, ACTUAL_STEP)
    period_bounds = actuals.aggregate(lo=Min("settlement_period"), hi=Max("settlement_period"))
    actual = ActualCoverage(
        half_hours_expected=len(expected_half_hours),
        half_hours_present=sum(1 for h in expected_half_hours if h in present),
        half_hours_missing=[h for h in expected_half_hours if h not in present],
        settlement_period_min=period_bounds["lo"],
        settlement_period_max=period_bounds["hi"],
    )
    return CoverageReport(date_from=date_from, date_to=date_to, forecast=forecast, actual=actual)


def latest_successful_runs() -> dict[str, IngestionRun]:
    """The most recent successful ingestion run per dataset."""
    runs: dict[str, IngestionRun] = {}
    for dataset in Dataset:
        run = (
            IngestionRun.objects.filter(dataset=dataset, status=IngestionStatus.SUCCEEDED)
            .order_by("-id")
            .first()
        )
        if run is not None:
            runs[dataset.value] = run
    return runs


def data_version() -> int:
    """Id of the latest successful ingestion run (0 if none). Changes whenever data may change;
    used in cache keys and ETags."""
    latest = IngestionRun.objects.filter(status=IngestionStatus.SUCCEEDED).aggregate(v=Max("id"))[
        "v"
    ]
    return int(latest or 0)


@dataclass(frozen=True)
class Readiness:
    ready: bool
    forecast_rows: int
    actual_rows: int


def readiness(fuel_type: str) -> Readiness:
    """Database reachable and both datasets present."""
    forecast_rows = WindForecast.objects.count()
    actual_rows = GenerationActual.objects.filter(fuel_type=fuel_type).count()
    return Readiness(
        ready=forecast_rows > 0 and actual_rows > 0,
        forecast_rows=forecast_rows,
        actual_rows=actual_rows,
    )
