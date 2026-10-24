"""Error metrics over matched pairs (R5, R6, D15). Pure: pandas + numpy only.

error = forecast - actual (positive = over-forecast).
MAE = mean(|e|), RMSE = sqrt(mean(e^2)), bias = mean(e). With n = 0 every metric is None.
Mean forecast and mean actual are taken over the same matched pairs, so bias equals their
difference. P90 uses linear interpolation between order statistics (numpy's default).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import cast

import numpy as np
import pandas as pd

from apps.analytics.domain.cutoff import UK_TIMEZONE
from apps.analytics.domain.frames import require_columns
from apps.analytics.domain.matching import IntervalStatus

P90 = 90


@dataclass(frozen=True)
class MetricSummary:
    n_expected: int
    n_matched: int
    n_missing_forecast: int
    n_missing_actual: int
    coverage_pct: float | None
    mae_mw: float | None
    rmse_mw: float | None
    bias_mw: float | None
    median_abs_error_mw: float | None
    p90_abs_error_mw: float | None
    max_abs_error_mw: float | None
    mean_forecast_mw: float | None
    mean_actual_mw: float | None


class GroupBy(StrEnum):
    DAY = "day"
    HOUR_OF_DAY = "hour_of_day"


def _finite_or_none(value: float) -> float | None:
    return float(value) if math.isfinite(value) else None


def summarize(intervals: pd.DataFrame) -> MetricSummary:
    """Summarise an intervals frame (output of a MatchingMethod: status, forecast_mw, actual_mw,
    error_mw columns). Only `matched` rows contribute to the error metrics."""
    require_columns(intervals, ("status", "forecast_mw", "actual_mw", "error_mw"), "intervals")
    status = intervals["status"]
    matched = intervals[status == IntervalStatus.MATCHED.value]
    n_expected = len(intervals)
    n_matched = len(matched)
    missing_both = int((status == IntervalStatus.MISSING_BOTH.value).sum())
    n_missing_forecast = int((status == IntervalStatus.MISSING_FORECAST.value).sum()) + missing_both
    n_missing_actual = int((status == IntervalStatus.MISSING_ACTUAL.value).sum()) + missing_both
    coverage_pct = 100.0 * n_matched / n_expected if n_expected else None

    if n_matched == 0:
        return MetricSummary(
            n_expected=n_expected,
            n_matched=0,
            n_missing_forecast=n_missing_forecast,
            n_missing_actual=n_missing_actual,
            coverage_pct=coverage_pct,
            mae_mw=None,
            rmse_mw=None,
            bias_mw=None,
            median_abs_error_mw=None,
            p90_abs_error_mw=None,
            max_abs_error_mw=None,
            mean_forecast_mw=None,
            mean_actual_mw=None,
        )

    errors = matched["error_mw"].to_numpy(dtype="float64")
    abs_errors = np.abs(errors)
    return MetricSummary(
        n_expected=n_expected,
        n_matched=n_matched,
        n_missing_forecast=n_missing_forecast,
        n_missing_actual=n_missing_actual,
        coverage_pct=coverage_pct,
        mae_mw=_finite_or_none(float(abs_errors.mean())),
        rmse_mw=_finite_or_none(float(np.sqrt(np.mean(errors**2)))),
        bias_mw=_finite_or_none(float(errors.mean())),
        median_abs_error_mw=_finite_or_none(float(np.median(abs_errors))),
        p90_abs_error_mw=_finite_or_none(float(np.percentile(abs_errors, P90))),
        max_abs_error_mw=_finite_or_none(float(abs_errors.max())),
        mean_forecast_mw=_finite_or_none(float(matched["forecast_mw"].mean())),
        mean_actual_mw=_finite_or_none(float(matched["actual_mw"].mean())),
    )


def group_keys(start_times: pd.Series, group_by: GroupBy) -> pd.Series:
    """UK-local grouping key: ISO date string for `day`, 0-23 for `hour_of_day`."""
    local = start_times.dt.tz_convert(UK_TIMEZONE)
    if group_by == GroupBy.DAY:
        return cast(pd.Series, local.dt.strftime("%Y-%m-%d"))
    return cast(pd.Series, local.dt.hour.astype("int64"))


def breakdown(intervals: pd.DataFrame, group_by: GroupBy) -> list[tuple[str | int, MetricSummary]]:
    """MetricSummary per UK-local day or hour of day, ordered by key. Groups with no matched
    pairs are kept (with None metrics) so gaps stay visible."""
    require_columns(intervals, ("start_time",), "intervals")
    if intervals.empty:
        return []
    keys = group_keys(intervals["start_time"], group_by)
    result: list[tuple[str | int, MetricSummary]] = []
    for key, group in intervals.groupby(keys, sort=True):
        result.append((key if isinstance(key, str) else int(cast(int, key)), summarize(group)))
    return result
