"""Decision cutoff policies (D1). Pure: stdlib + pandas only.

- Lead time:  C = T - H hours.
- Daily gate: C = (UK delivery date of T - k days) at HH:MM Europe/London, converted to UTC.

Invariant: C <= T for every delivery time T. A policy that violates it raises.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

import pandas as pd

from apps.analytics.domain.frames import UTC_DTYPE, as_utc

UK_TIMEZONE = "Europe/London"
_UK_TZ = ZoneInfo(UK_TIMEZONE)


class CutoffPolicyKind(StrEnum):
    DAILY_GATE = "daily_gate"
    LEAD_TIME = "lead_time"


class InvalidCutoffPolicyError(ValueError):
    pass


@dataclass(frozen=True)
class LeadTimePolicy:
    """The forecast must have been published at least `lead_time_h` hours before delivery."""

    lead_time_h: int

    def __post_init__(self) -> None:
        if self.lead_time_h < 0:
            raise InvalidCutoffPolicyError("lead_time_h must be >= 0")

    @property
    def kind(self) -> CutoffPolicyKind:
        return CutoffPolicyKind.LEAD_TIME

    def raw_cutoffs(self, start_times: pd.Series) -> pd.Series:
        return start_times - pd.Timedelta(hours=self.lead_time_h)

    def describe(self) -> dict[str, object]:
        return {"policy": self.kind.value, "lead_time_h": self.lead_time_h}


@dataclass(frozen=True)
class DailyGatePolicy:
    """A fixed daily deadline: `days_before` days before the UK delivery date, at `gate_time`
    UK local time (GMT or BST, whichever applies on the gate's date)."""

    gate_time: time
    days_before: int

    def __post_init__(self) -> None:
        if self.days_before < 0:
            raise InvalidCutoffPolicyError("days_before must be >= 0")
        if self.gate_time.tzinfo is not None:
            raise InvalidCutoffPolicyError("gate_time is a UK wall-clock time; pass it naive")

    @property
    def kind(self) -> CutoffPolicyKind:
        return CutoffPolicyKind.DAILY_GATE

    def gate_for_delivery_date(self, delivery_date: date) -> datetime:
        """The gate instant (aware, UTC) for a UK delivery date."""
        gate_date = delivery_date - timedelta(days=self.days_before)
        return datetime.combine(gate_date, self.gate_time, tzinfo=_UK_TZ).astimezone(
            ZoneInfo("UTC")
        )

    def raw_cutoffs(self, start_times: pd.Series) -> pd.Series:
        local_dates = start_times.dt.tz_convert(UK_TIMEZONE).dt.date
        # Few distinct dates (31 a month): compute each gate once with zoneinfo, so the DST
        # offset is that of the gate's own date.
        gates = {d: pd.Timestamp(self.gate_for_delivery_date(d)) for d in local_dates.unique()}
        return pd.Series([gates[d] for d in local_dates], index=start_times.index, dtype=UTC_DTYPE)

    def describe(self) -> dict[str, object]:
        return {
            "policy": self.kind.value,
            "gate_time": self.gate_time.strftime("%H:%M"),
            "gate_days_before": self.days_before,
        }


CutoffPolicy = LeadTimePolicy | DailyGatePolicy


def compute_cutoffs(policy: CutoffPolicy, start_times: pd.Series) -> pd.Series:
    """Cutoff per delivery time.

    Args:
        start_times: delivery-hour starts, tz-aware (any zone; normalised to UTC).
    Returns:
        Series `cutoff_time` (datetime64[ns, UTC]) aligned with `start_times`.
    Raises:
        InvalidCutoffPolicyError: if any cutoff would fall after its delivery time.
    """
    starts = as_utc(start_times, "start_time")
    if starts.empty:
        return starts.copy()
    cutoffs = as_utc(policy.raw_cutoffs(starts), "cutoff_time")
    violations = cutoffs > starts
    if violations.any():
        first = starts[violations].iloc[0]
        raise InvalidCutoffPolicyError(
            f"{policy.describe()} gives a cutoff after delivery start "
            f"(first at {first.isoformat()})"
        )
    return cutoffs.rename("cutoff_time")
