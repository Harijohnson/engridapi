"""ORM reads -> pandas DataFrames. No business rules here; only the columns that are needed."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

import pandas as pd

from apps.analytics.domain.frames import UTC_DTYPE
from apps.market_data.models import GenerationActual, WindForecast


def _frame(
    records: Sequence[tuple[Any, ...]], columns: list[str], time_columns: list[str]
) -> pd.DataFrame:
    df = pd.DataFrame.from_records(records, columns=columns)
    for column in time_columns:
        df[column] = pd.to_datetime(df[column], utc=True).astype(UTC_DTYPE)
    return df


def forecasts_frame(start_from: datetime, start_to: datetime) -> pd.DataFrame:
    """Every forecast version for delivery hours in [start_from, start_to).

    Columns: start_time, publish_time (datetime64[ns, UTC]); generation_mw (int64 MW).
    """
    records = list(
        WindForecast.objects.filter(start_time__gte=start_from, start_time__lt=start_to)
        .order_by("start_time", "publish_time")
        .values_list("start_time", "publish_time", "generation_mw")
    )
    return _frame(
        records, ["start_time", "publish_time", "generation_mw"], ["start_time", "publish_time"]
    )


def forecast_versions_frame(start_time: datetime) -> pd.DataFrame:
    """Every version for one delivery hour. Columns: publish_time (UTC), generation_mw (MW)."""
    records = list(
        WindForecast.objects.filter(start_time=start_time)
        .order_by("publish_time")
        .values_list("publish_time", "generation_mw")
    )
    return _frame(records, ["publish_time", "generation_mw"], ["publish_time"])


def actuals_frame(start_from: datetime, start_to: datetime, fuel_type: str) -> pd.DataFrame:
    """Actual half-hours in [start_from, start_to) for one fuel type.

    Columns: start_time (datetime64[ns, UTC]); generation_mw (int64 MW, 30-minute average);
    settlement_date (date); settlement_period (int).
    """
    records = list(
        GenerationActual.objects.filter(
            fuel_type=fuel_type, start_time__gte=start_from, start_time__lt=start_to
        )
        .order_by("start_time")
        .values_list("start_time", "generation_mw", "settlement_date", "settlement_period")
    )
    return _frame(
        records,
        ["start_time", "generation_mw", "settlement_date", "settlement_period"],
        ["start_time"],
    )
