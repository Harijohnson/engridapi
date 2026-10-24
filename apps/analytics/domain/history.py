"""Publication history of one delivery hour (S5): every version, labelled. Pure: pandas only.

Labels, for delivery time T and cutoff C (C <= T):
- selected:             the latest version with publish_time <= C
- superseded:           publish_time <= C, but a later eligible version exists
- after_cutoff:         C < publish_time <= T  (published too late for the decision)
- after_delivery_start: publish_time > T       (never a valid decision input)
"""

from __future__ import annotations

from enum import StrEnum

import numpy as np
import pandas as pd

from apps.analytics.domain.frames import SECONDS_PER_HOUR, as_utc, require_columns
from apps.analytics.domain.selection import LookAheadError


class VersionStatus(StrEnum):
    SELECTED = "selected"
    SUPERSEDED = "superseded"
    AFTER_CUTOFF = "after_cutoff"
    AFTER_DELIVERY_START = "after_delivery_start"


def label_versions(
    versions: pd.DataFrame, start_time: pd.Timestamp, cutoff_time: pd.Timestamp
) -> pd.DataFrame:
    """Label every version of one delivery hour.

    Args:
        versions: publish_time (tz-aware), generation_mw (MW). All versions for `start_time`.
        start_time, cutoff_time: tz-aware; cutoff_time <= start_time.
    Returns:
        publish_time (datetime64[ns, UTC]), forecast_mw (float64), lead_time_h (float64,
        start_time - publish_time; negative if published after delivery start) and status
        (VersionStatus value), ordered by publish_time.
    """
    require_columns(versions, ("publish_time", "generation_mw"), "versions")
    start = pd.Timestamp(start_time).tz_convert("UTC")
    cutoff = pd.Timestamp(cutoff_time).tz_convert("UTC")
    if cutoff > start:
        raise LookAheadError("cutoff is after delivery start")

    out = pd.DataFrame(
        {
            "publish_time": as_utc(versions["publish_time"], "versions.publish_time"),
            "forecast_mw": versions["generation_mw"].astype("float64"),
        }
    ).sort_values("publish_time", ignore_index=True)
    out["lead_time_h"] = (start - out["publish_time"]).dt.total_seconds() / SECONDS_PER_HOUR

    eligible = out["publish_time"] <= cutoff
    status = np.select(
        [eligible, out["publish_time"] <= start],
        [VersionStatus.SUPERSEDED.value, VersionStatus.AFTER_CUTOFF.value],
        default=VersionStatus.AFTER_DELIVERY_START.value,
    ).astype(object)
    if eligible.any():
        status[int(out.index[eligible][-1])] = VersionStatus.SELECTED.value
    out["status"] = status
    return out
