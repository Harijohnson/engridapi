"""Forecast selection at a decision cutoff (R1, R2, R3). Pure: pandas only.

For each delivery hour T with cutoff C, the selected version is the one with the greatest
publish_time such that publish_time <= C (and C <= T always). No eligible version means the
hour is `missing_forecast`: never filled from another hour or a later version.
"""

from __future__ import annotations

from enum import StrEnum

import numpy as np
import pandas as pd

from apps.analytics.domain.frames import (
    FrameContractError,
    as_utc,
    empty_utc,
    hours_between,
    require_columns,
)


class SelectionStatus(StrEnum):
    SELECTED = "selected"
    MISSING_FORECAST = "missing_forecast"


class LookAheadError(AssertionError):
    """A selected forecast was published after its cutoff, or a cutoff is after delivery."""


def normalise_forecasts(forecasts: pd.DataFrame) -> pd.DataFrame:
    """Validate and normalise a forecasts frame.

    Columns: start_time (tz-aware), publish_time (tz-aware), generation_mw (int or float, MW).
    """
    require_columns(forecasts, ("start_time", "publish_time", "generation_mw"), "forecasts")
    out = pd.DataFrame(
        {
            "start_time": as_utc(forecasts["start_time"], "forecasts.start_time"),
            "publish_time": as_utc(forecasts["publish_time"], "forecasts.publish_time"),
            "generation_mw": forecasts["generation_mw"].astype("float64"),
        }
    )
    if out.duplicated(["start_time", "publish_time"]).any():
        raise FrameContractError("forecasts has duplicate (start_time, publish_time) pairs")
    return out


def select_forecasts(forecasts: pd.DataFrame, targets: pd.DataFrame) -> pd.DataFrame:
    """Pick, for every target delivery hour, the latest version published at or before its cutoff.

    Args:
        forecasts: start_time, publish_time (tz-aware), generation_mw (MW). All versions.
        targets: start_time, cutoff_time (tz-aware); one row per delivery hour.
    Returns:
        One row per target, ordered by start_time, with columns
        start_time, cutoff_time (datetime64[ns, UTC]);
        publish_time (datetime64[ns, UTC], NaT if missing);
        forecast_mw (float64 MW, NaN if missing);
        lead_time_h (float64 hours = start_time - publish_time, NaN if missing);
        selection_status (SelectionStatus value).
    Raises:
        LookAheadError: if the result would break R1 (checked on every call).
    """
    require_columns(targets, ("start_time", "cutoff_time"), "targets")
    versions = normalise_forecasts(forecasts)
    wanted = pd.DataFrame(
        {
            "start_time": as_utc(targets["start_time"], "targets.start_time"),
            "cutoff_time": as_utc(targets["cutoff_time"], "targets.cutoff_time"),
        }
    )
    if wanted["start_time"].duplicated().any():
        raise FrameContractError("targets has duplicate start_time values")
    if (wanted["cutoff_time"] > wanted["start_time"]).any():
        raise LookAheadError("a target cutoff is after its delivery start")

    if wanted.empty or versions.empty:
        selected = wanted.assign(
            publish_time=empty_utc(wanted.index), forecast_mw=np.full(len(wanted), np.nan)
        )
    else:
        # merge_asof (backward, exact matches allowed) returns, per start_time, the last version
        # whose publish_time <= cutoff_time: exactly "latest eligible wins", inclusive cutoff.
        selected = pd.merge_asof(
            wanted.sort_values("cutoff_time"),
            versions.sort_values("publish_time"),
            left_on="cutoff_time",
            right_on="publish_time",
            by="start_time",
            direction="backward",
            allow_exact_matches=True,
        ).rename(columns={"generation_mw": "forecast_mw"})

    selected = selected.sort_values("start_time", ignore_index=True)
    has_forecast = selected["publish_time"].notna()
    selected["lead_time_h"] = hours_between(selected["start_time"], selected["publish_time"])
    selected["selection_status"] = np.where(
        has_forecast, SelectionStatus.SELECTED.value, SelectionStatus.MISSING_FORECAST.value
    )
    assert_no_look_ahead(selected)
    return selected[
        [
            "start_time",
            "cutoff_time",
            "publish_time",
            "forecast_mw",
            "lead_time_h",
            "selection_status",
        ]
    ]


def assert_no_look_ahead(selected: pd.DataFrame) -> None:
    """R1 runtime check: publish_time <= cutoff_time <= start_time for every selected row."""
    picked = selected[selected["publish_time"].notna()]
    if (picked["publish_time"] > picked["cutoff_time"]).any():
        raise LookAheadError("a selected forecast was published after its cutoff")
    if (selected["cutoff_time"] > selected["start_time"]).any():
        raise LookAheadError("a cutoff is after its delivery start")
