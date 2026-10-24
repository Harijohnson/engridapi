"""DataFrame contracts shared by the domain functions (pure: stdlib + pandas only).

Every datetime column inside the domain is `datetime64[ns, UTC]`. Inputs are normalised to that
dtype at the entry points, so joins never fail on a resolution or time-zone mismatch.
"""

from collections.abc import Iterable
from typing import cast

import pandas as pd

UTC_DTYPE = pd.DatetimeTZDtype(unit="ns", tz="UTC")
SECONDS_PER_HOUR = 3600.0


class FrameContractError(ValueError):
    """A DataFrame passed into the domain is missing columns or has the wrong types."""


def require_columns(df: pd.DataFrame, columns: Iterable[str], name: str) -> None:
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise FrameContractError(f"{name} is missing columns: {missing}")


def as_utc(series: pd.Series, name: str) -> pd.Series:
    """Return `series` as datetime64[ns, UTC]. Naive datetimes are rejected, never assumed UTC."""
    if not isinstance(series.dtype, pd.DatetimeTZDtype):
        if len(series) == 0:
            return pd.Series(pd.to_datetime(series, utc=True)).astype(UTC_DTYPE)
        raise FrameContractError(f"{name} must be timezone-aware, got dtype {series.dtype}")
    return cast(pd.Series, series.dt.tz_convert("UTC").astype(UTC_DTYPE))


def hours_between(later: pd.Series, earlier: pd.Series) -> pd.Series:
    """(later - earlier) in hours, float64; NaN where either side is missing."""
    return cast(pd.Series, (later - earlier).dt.total_seconds() / SECONDS_PER_HOUR)


def empty_utc(index: pd.Index | None = None) -> pd.Series:
    return pd.Series(
        pd.NaT, index=index if index is not None else pd.RangeIndex(0), dtype=UTC_DTYPE
    )
