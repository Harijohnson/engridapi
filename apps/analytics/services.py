"""Analytics orchestration: selectors -> pure domain -> plain result objects, with caching.

Results are frozen dataclasses of plain Python values (no NaN, no pandas types), ready for the
API serializers. Every result carries the normalised query and `data_version` (R8).

Caching (D16): local-memory cache keyed by (namespace, normalised params, data_version). The
data is historical; a new ingestion run changes data_version, so stale entries are never read.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, cast

import pandas as pd
from django.conf import settings
from django.core.cache import cache

from apps.analytics.domain.cutoff import (
    UK_TIMEZONE,
    CutoffPolicy,
    CutoffPolicyKind,
    compute_cutoffs,
)
from apps.analytics.domain.history import VersionStatus, label_versions
from apps.analytics.domain.matching import DEFAULT_MATCHING
from apps.analytics.domain.metrics import GroupBy, MetricSummary, breakdown, summarize
from apps.analytics.domain.pipeline import delivery_hours, evaluate, half_hours, lead_time_curve
from apps.analytics.selectors import actuals_frame, forecast_versions_frame, forecasts_frame
from apps.core.time import delivery_window_utc
from apps.market_data import services as market_data
from apps.market_data.models import IngestionRun

TIMEZONE_NOTE = (
    "All timestamps in the API are UTC. UK delivery dates and gate times use Europe/London "
    "(GMT in January, so UK time equals UTC for the whole evaluation window)."
)

HALF_HOUR = timedelta(minutes=30)

INTERVAL_ORDERING_FIELDS = (
    "start_time",
    "forecast_mw",
    "actual_mw",
    "error_mw",
    "abs_error_mw",
    "publish_time",
    "lead_time_h",
)


# --- query objects -------------------------------------------------------------------------
@dataclass(frozen=True)
class AnalyticsQuery:
    date_from: date
    date_to: date
    policy: CutoffPolicy

    def describe(self) -> dict[str, Any]:
        described: dict[str, Any] = {
            "date_from": self.date_from.isoformat(),
            "date_to": self.date_to.isoformat(),
            "timezone": UK_TIMEZONE,
            "policy": self.policy.kind.value,
            "gate_time": None,
            "gate_days_before": None,
            "lead_time_h": None,
        }
        described.update(self.policy.describe())
        return described


@dataclass(frozen=True)
class LeadTimeCurveQuery:
    date_from: date
    date_to: date
    min_h: int
    max_h: int

    def describe(self) -> dict[str, Any]:
        return {
            "date_from": self.date_from.isoformat(),
            "date_to": self.date_to.isoformat(),
            "timezone": UK_TIMEZONE,
            "min_h": self.min_h,
            "max_h": self.max_h,
        }


@dataclass(frozen=True)
class HistoryQuery:
    start_time: datetime
    policy: CutoffPolicy

    def describe(self) -> dict[str, Any]:
        described: dict[str, Any] = {
            "start_time": self.start_time,
            "policy": self.policy.kind.value,
            "gate_time": None,
            "gate_days_before": None,
            "lead_time_h": None,
        }
        described.update(self.policy.describe())
        return described


# --- result objects ------------------------------------------------------------------------
@dataclass(frozen=True)
class SummaryResult:
    query: dict[str, Any]
    data_version: int
    matching_method: str
    metrics: MetricSummary


@dataclass(frozen=True)
class ForecastPoint:
    start_time: datetime
    cutoff_time: datetime
    publish_time: datetime | None
    forecast_mw: int | None
    lead_time_h: float | None
    actual_mw: int | None
    error_mw: int | None
    status: str


@dataclass(frozen=True)
class ActualPoint:
    start_time: datetime
    actual_mw: int | None
    settlement_date: date | None
    settlement_period: int | None


@dataclass(frozen=True)
class TimeseriesResult:
    query: dict[str, Any]
    data_version: int
    forecast: list[ForecastPoint]
    actual: list[ActualPoint]


@dataclass(frozen=True)
class IntervalRow:
    start_time: datetime
    forecast_mw: int | None
    actual_mw: int | None
    error_mw: int | None
    abs_error_mw: int | None
    publish_time: datetime | None
    lead_time_h: float | None
    cutoff_time: datetime
    status: str


@dataclass(frozen=True)
class IntervalsResult:
    query: dict[str, Any]
    data_version: int
    ordering: str
    rows: list[IntervalRow]


@dataclass(frozen=True)
class BreakdownGroup:
    key: str
    metrics: MetricSummary


@dataclass(frozen=True)
class BreakdownResult:
    query: dict[str, Any]
    data_version: int
    group_by: str
    groups: list[BreakdownGroup]


@dataclass(frozen=True)
class LeadTimePoint:
    lead_time_h: int
    metrics: MetricSummary


@dataclass(frozen=True)
class LeadTimeCurveResult:
    query: dict[str, Any]
    data_version: int
    points: list[LeadTimePoint]


@dataclass(frozen=True)
class HistoryVersion:
    publish_time: datetime
    forecast_mw: int
    lead_time_h: float
    status: str


@dataclass(frozen=True)
class HistoryResult:
    query: dict[str, Any]
    data_version: int
    start_time: datetime
    cutoff_time: datetime
    actual_mw: int | None
    selected_publish_time: datetime | None
    selected_forecast_mw: int | None
    selected_error_mw: int | None
    selected_lead_time_h: float | None
    versions: list[HistoryVersion]


# --- helpers -------------------------------------------------------------------------------
def _opt_int(value: object) -> int | None:
    if value is None or pd.isna(cast(Any, value)):
        return None
    return int(cast(Any, value))


def _opt_float(value: object) -> float | None:
    if value is None or pd.isna(cast(Any, value)):
        return None
    number = float(cast(Any, value))
    return number if math.isfinite(number) else None


def _opt_date(value: object) -> date | None:
    if value is None or pd.isna(cast(Any, value)):
        return None
    return cast(date, value)


def _opt_dt(value: object) -> datetime | None:
    if value is None or pd.isna(cast(Any, value)):
        return None
    return cast(pd.Timestamp, value).to_pydatetime()


def _dt(value: object) -> datetime:
    result = _opt_dt(value)
    if result is None:
        raise ValueError("expected a timestamp, got a missing value")
    return result


def _cached[T](namespace: str, params: dict[str, Any], version: int, compute: Callable[[], T]) -> T:
    digest = hashlib.sha1(
        json.dumps(params, sort_keys=True, default=str).encode(), usedforsecurity=False
    ).hexdigest()
    key = f"engrid:{namespace}:v{version}:{digest}"
    sentinel = object()
    hit = cache.get(key, sentinel)
    if hit is not sentinel:
        return cast(T, hit)
    value = compute()
    cache.set(key, value)
    return value


def current_data_version() -> int:
    return market_data.data_version()


def _source_frames(
    date_from: date, date_to: date, version: int
) -> tuple[pd.DataFrame, pd.DataFrame]:
    def load() -> tuple[pd.DataFrame, pd.DataFrame]:
        start, end = delivery_window_utc(date_from, date_to)
        return (
            forecasts_frame(start, end),
            actuals_frame(start, end, settings.ENGRID_FUEL_TYPE),
        )

    return _cached(
        "frames", {"from": date_from.isoformat(), "to": date_to.isoformat()}, version, load
    )


def _intervals_frame(query: AnalyticsQuery, version: int) -> pd.DataFrame:
    def compute() -> pd.DataFrame:
        forecasts, actuals = _source_frames(query.date_from, query.date_to, version)
        start, end = delivery_window_utc(query.date_from, query.date_to)
        return evaluate(query.policy, forecasts, actuals, delivery_hours(start, end))

    return _cached("intervals-frame", query.describe(), version, compute)


# --- public services -----------------------------------------------------------------------
def summary(query: AnalyticsQuery, version: int) -> SummaryResult:
    return SummaryResult(
        query=query.describe(),
        data_version=version,
        matching_method=DEFAULT_MATCHING.name,
        metrics=summarize(_intervals_frame(query, version)),
    )


def timeseries(query: AnalyticsQuery, version: int) -> TimeseriesResult:
    def compute() -> TimeseriesResult:
        intervals = _intervals_frame(query, version)
        _, actuals = _source_frames(query.date_from, query.date_to, version)
        start, end = delivery_window_utc(query.date_from, query.date_to)
        slots = pd.DataFrame({"start_time": half_hours(start, end)}).merge(
            actuals, on="start_time", how="left"
        )
        return TimeseriesResult(
            query=query.describe(),
            data_version=version,
            forecast=[
                ForecastPoint(
                    start_time=_dt(row.start_time),
                    cutoff_time=_dt(row.cutoff_time),
                    publish_time=_opt_dt(row.publish_time),
                    forecast_mw=_opt_int(row.forecast_mw),
                    lead_time_h=_opt_float(row.lead_time_h),
                    actual_mw=_opt_int(row.actual_mw),
                    error_mw=_opt_int(row.error_mw),
                    status=str(row.status),
                )
                for row in intervals.itertuples(index=False)
            ],
            actual=[
                ActualPoint(
                    start_time=_dt(row.start_time),
                    actual_mw=_opt_int(row.generation_mw),
                    settlement_date=_opt_date(row.settlement_date),
                    settlement_period=_opt_int(row.settlement_period),
                )
                for row in slots.itertuples(index=False)
            ],
        )

    return _cached("timeseries", query.describe(), version, compute)


def intervals(query: AnalyticsQuery, version: int, ordering: str) -> IntervalsResult:
    """All delivery hours, sorted by `ordering` (a whitelisted field, optional '-' prefix).
    Missing values sort last in both directions; ties break on start_time."""
    field = ordering.removeprefix("-")
    if field not in INTERVAL_ORDERING_FIELDS:
        raise ValueError(f"unsupported ordering {ordering!r}")

    def compute() -> list[IntervalRow]:
        frame = _intervals_frame(query, version)
        ascending = not ordering.startswith("-")
        if field == "start_time":
            ordered = frame.sort_values("start_time", ascending=ascending)
        else:
            ordered = frame.sort_values(
                [field, "start_time"], ascending=[ascending, True], na_position="last"
            )
        return [
            IntervalRow(
                start_time=_dt(row.start_time),
                forecast_mw=_opt_int(row.forecast_mw),
                actual_mw=_opt_int(row.actual_mw),
                error_mw=_opt_int(row.error_mw),
                abs_error_mw=_opt_int(row.abs_error_mw),
                publish_time=_opt_dt(row.publish_time),
                lead_time_h=_opt_float(row.lead_time_h),
                cutoff_time=_dt(row.cutoff_time),
                status=str(row.status),
            )
            for row in ordered.itertuples(index=False)
        ]

    rows = _cached("intervals", {**query.describe(), "ordering": ordering}, version, compute)
    return IntervalsResult(
        query=query.describe(), data_version=version, ordering=ordering, rows=rows
    )


def accuracy_breakdown(query: AnalyticsQuery, version: int, group_by: GroupBy) -> BreakdownResult:
    groups = [
        BreakdownGroup(key=str(key), metrics=metrics)
        for key, metrics in breakdown(_intervals_frame(query, version), group_by)
    ]
    return BreakdownResult(
        query=query.describe(), data_version=version, group_by=group_by.value, groups=groups
    )


def accuracy_by_lead_time(query: LeadTimeCurveQuery, version: int) -> LeadTimeCurveResult:
    def compute() -> list[LeadTimePoint]:
        forecasts, actuals = _source_frames(query.date_from, query.date_to, version)
        start, end = delivery_window_utc(query.date_from, query.date_to)
        curve = lead_time_curve(
            forecasts, actuals, delivery_hours(start, end), range(query.min_h, query.max_h + 1)
        )
        return [LeadTimePoint(lead_time_h=h, metrics=m) for h, m in curve]

    points = _cached("lead-time-curve", query.describe(), version, compute)
    return LeadTimeCurveResult(query=query.describe(), data_version=version, points=points)


def forecast_history(query: HistoryQuery, version: int) -> HistoryResult:
    def compute() -> HistoryResult:
        start = pd.Timestamp(query.start_time).tz_convert("UTC")
        cutoff = compute_cutoffs(query.policy, pd.Series([start])).iloc[0]
        labelled = label_versions(forecast_versions_frame(query.start_time), start, cutoff)
        actuals = actuals_frame(
            query.start_time, query.start_time + HALF_HOUR, settings.ENGRID_FUEL_TYPE
        )
        actual_mw = _opt_int(actuals["generation_mw"].iloc[0]) if len(actuals) else None
        chosen = labelled[labelled["status"] == VersionStatus.SELECTED.value]
        selected_mw = _opt_int(chosen["forecast_mw"].iloc[0]) if len(chosen) else None
        return HistoryResult(
            query=query.describe(),
            data_version=version,
            start_time=start.to_pydatetime(),
            cutoff_time=_dt(cutoff),
            actual_mw=actual_mw,
            selected_publish_time=_opt_dt(chosen["publish_time"].iloc[0]) if len(chosen) else None,
            selected_forecast_mw=selected_mw,
            selected_error_mw=(
                selected_mw - actual_mw
                if selected_mw is not None and actual_mw is not None
                else None
            ),
            selected_lead_time_h=_opt_float(chosen["lead_time_h"].iloc[0]) if len(chosen) else None,
            versions=[
                HistoryVersion(
                    publish_time=_dt(row.publish_time),
                    forecast_mw=int(cast(float, row.forecast_mw)),
                    lead_time_h=float(cast(float, row.lead_time_h)),
                    status=str(row.status),
                )
                for row in labelled.itertuples(index=False)
            ],
        )

    return _cached("history", query.describe(), version, compute)


# --- meta ----------------------------------------------------------------------------------
@dataclass(frozen=True)
class IngestionInfo:
    dataset: str
    source: str
    run_id: int
    status: str
    started_at: datetime
    finished_at: datetime | None
    rows_received: int
    rows_created: int
    rows_rejected: int


@dataclass(frozen=True)
class MetaResult:
    data_version: int
    evaluation_window: dict[str, Any]
    policies: dict[str, Any]
    matching: dict[str, str]
    coverage: dict[str, Any]
    ingestion: list[IngestionInfo]
    timezone_note: str
    data_sources: list[dict[str, str]]
    limitations: list[str]


def _ingestion_info(run: IngestionRun) -> IngestionInfo:
    return IngestionInfo(
        dataset=run.dataset,
        source=run.source,
        run_id=run.pk,
        status=run.status,
        started_at=run.started_at,
        finished_at=run.finished_at,
        rows_received=run.rows_received,
        rows_created=run.rows_created,
        rows_rejected=run.rows_rejected,
    )


def meta(version: int) -> MetaResult:
    def compute() -> MetaResult:
        date_from = settings.ENGRID_EVALUATION_DATE_FROM
        date_to = settings.ENGRID_EVALUATION_DATE_TO
        report = market_data.coverage_report(date_from, date_to, settings.ENGRID_FUEL_TYPE)
        return MetaResult(
            data_version=version,
            evaluation_window={
                "date_from": date_from.isoformat(),
                "date_to": date_to.isoformat(),
                "timezone": UK_TIMEZONE,
            },
            policies={
                "default": settings.ENGRID_DEFAULT_POLICY,
                "available": [kind.value for kind in CutoffPolicyKind],
                "daily_gate": {
                    "gate_time_default": settings.ENGRID_DEFAULT_GATE_TIME,
                    "gate_days_before_default": settings.ENGRID_DEFAULT_GATE_DAYS_BEFORE,
                    "gate_days_before_min": 1,
                    "gate_days_before_max": settings.ENGRID_GATE_DAYS_BEFORE_MAX,
                },
                "lead_time": {
                    "lead_time_h_default": settings.ENGRID_DEFAULT_LEAD_TIME_H,
                    "lead_time_h_min": 0,
                    "lead_time_h_max": settings.ENGRID_LEAD_TIME_H_MAX,
                },
            },
            matching={"method": DEFAULT_MATCHING.name, "description": DEFAULT_MATCHING.description},
            coverage={
                "forecast": {
                    "expected": report.forecast.hours_expected,
                    "present": report.forecast.hours_present,
                    "missing": len(report.forecast.hours_missing),
                    "rows": report.forecast.rows,
                    "distinct_publishes": report.forecast.distinct_publishes,
                    "versions_per_hour_min": report.forecast.versions_per_hour_min,
                    "versions_per_hour_max": report.forecast.versions_per_hour_max,
                    "versions_per_hour_mean": report.forecast.versions_per_hour_mean,
                },
                "actual": {
                    "expected": report.actual.half_hours_expected,
                    "present": report.actual.half_hours_present,
                    "missing": len(report.actual.half_hours_missing),
                },
            },
            ingestion=[_ingestion_info(r) for r in market_data.latest_successful_runs().values()],
            timezone_note=TIMEZONE_NOTE,
            data_sources=[
                {
                    "dataset": "WINDFOR",
                    "name": "Wind generation forecast",
                    "url": "https://bmrs.elexon.co.uk/api-documentation/endpoint/datasets/WINDFOR",
                    "description": (
                        "Hourly forecasts of wind generation (MW) for wind farms visible to the "
                        "ESO with operational metering, re-published several times a day."
                    ),
                },
                {
                    "dataset": "FUELHH",
                    "name": "Generation by fuel type (half-hourly), fuelType=WIND",
                    "url": "https://bmrs.elexon.co.uk/api-documentation/endpoint/datasets/FUELHH",
                    "description": "Average MW over each 30-minute settlement period.",
                },
            ],
            limitations=[
                "This tool measures wind forecast accuracy. It does not calculate procurement "
                "volumes: demand data is not part of the supplied datasets.",
                DEFAULT_MATCHING.description,
            ],
        )

    return _cached("meta", {}, version, compute)
