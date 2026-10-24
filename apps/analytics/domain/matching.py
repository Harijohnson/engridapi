"""Matching selected hourly forecasts to half-hourly actuals (D2). Pure: pandas only.

D2 (resolved): the forecast for HH:00 is compared with the actual half-hour that *starts* at
HH:00. The method sits behind `MatchingMethod`, so changing D2 later is a local change.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol

import numpy as np
import pandas as pd

from apps.analytics.domain.frames import FrameContractError, as_utc, require_columns


class IntervalStatus(StrEnum):
    MATCHED = "matched"
    MISSING_FORECAST = "missing_forecast"
    MISSING_ACTUAL = "missing_actual"
    MISSING_BOTH = "missing_both"


def normalise_actuals(actuals: pd.DataFrame) -> pd.DataFrame:
    """Columns: start_time (tz-aware, half-hour start), generation_mw (MW average)."""
    require_columns(actuals, ("start_time", "generation_mw"), "actuals")
    out = pd.DataFrame(
        {
            "start_time": as_utc(actuals["start_time"], "actuals.start_time"),
            "actual_mw": actuals["generation_mw"].astype("float64"),
        }
    )
    if out["start_time"].duplicated().any():
        raise FrameContractError("actuals has duplicate start_time values")
    return out


class MatchingMethod(Protocol):
    name: str
    description: str

    def match(self, selected: pd.DataFrame, actuals: pd.DataFrame) -> pd.DataFrame: ...


class SameStartTimeMatching:
    name = "same_start_time"
    description = (
        "Each hourly forecast (HH:00) is compared with the actual half-hour that starts at the "
        "same time (HH:00-HH:30). Half-hours starting at HH:30 are shown but not scored."
    )

    def match(self, selected: pd.DataFrame, actuals: pd.DataFrame) -> pd.DataFrame:
        """Join selected forecasts to actuals and compute signed errors (R5).

        Args:
            selected: output of `select_forecasts` (start_time, cutoff_time, publish_time,
                forecast_mw, lead_time_h, selection_status).
            actuals: start_time, generation_mw.
        Returns:
            `selected` plus actual_mw (float64 MW, NaN if missing), error_mw = forecast - actual
            and abs_error_mw (float64 MW, NaN unless matched), and status (IntervalStatus value).
        """
        matched = selected.merge(normalise_actuals(actuals), on="start_time", how="left")
        has_forecast = matched["forecast_mw"].notna()
        has_actual = matched["actual_mw"].notna()
        matched["error_mw"] = (matched["forecast_mw"] - matched["actual_mw"]).where(
            has_forecast & has_actual
        )
        matched["abs_error_mw"] = matched["error_mw"].abs()
        matched["status"] = np.select(
            [has_forecast & has_actual, ~has_forecast & has_actual, has_forecast & ~has_actual],
            [
                IntervalStatus.MATCHED.value,
                IntervalStatus.MISSING_FORECAST.value,
                IntervalStatus.MISSING_ACTUAL.value,
            ],
            default=IntervalStatus.MISSING_BOTH.value,
        )
        return matched


DEFAULT_MATCHING: MatchingMethod = SameStartTimeMatching()
