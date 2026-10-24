"""Composes cutoff -> selection -> matching -> metrics. Pure: pandas only."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

import pandas as pd

from apps.analytics.domain.cutoff import CutoffPolicy, LeadTimePolicy, compute_cutoffs
from apps.analytics.domain.frames import UTC_DTYPE
from apps.analytics.domain.matching import DEFAULT_MATCHING, MatchingMethod
from apps.analytics.domain.metrics import MetricSummary, summarize
from apps.analytics.domain.selection import normalise_forecasts, select_forecasts


def delivery_hours(window_start: datetime, window_end: datetime) -> pd.Series:
    """Hourly delivery starts in the half-open UTC window [window_start, window_end)."""
    hours = pd.date_range(window_start, window_end, freq="h", inclusive="left", tz="UTC")
    return pd.Series(hours, name="start_time").astype(UTC_DTYPE)


def half_hours(window_start: datetime, window_end: datetime) -> pd.Series:
    """Half-hourly settlement starts in the half-open UTC window."""
    slots = pd.date_range(window_start, window_end, freq="30min", inclusive="left", tz="UTC")
    return pd.Series(slots, name="start_time").astype(UTC_DTYPE)


def evaluate(
    policy: CutoffPolicy,
    forecasts: pd.DataFrame,
    actuals: pd.DataFrame,
    start_times: pd.Series,
    matching: MatchingMethod = DEFAULT_MATCHING,
) -> pd.DataFrame:
    """One row per delivery hour in `start_times`: the forecast selected under `policy`, matched
    to its actual. Columns as documented in `select_forecasts` and `MatchingMethod.match`."""
    targets = pd.DataFrame(
        {"start_time": start_times, "cutoff_time": compute_cutoffs(policy, start_times)}
    )
    return matching.match(select_forecasts(forecasts, targets), actuals)


def lead_time_curve(
    forecasts: pd.DataFrame,
    actuals: pd.DataFrame,
    start_times: pd.Series,
    lead_times_h: Iterable[int],
    matching: MatchingMethod = DEFAULT_MATCHING,
) -> list[tuple[int, MetricSummary]]:
    """MetricSummary for each required lead time H: how accuracy changes as H grows."""
    versions = normalise_forecasts(forecasts)
    return [
        (h, summarize(evaluate(LeadTimePolicy(h), versions, actuals, start_times, matching)))
        for h in lead_times_h
    ]
