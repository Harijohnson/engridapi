"""Golden test on REAL data (PLAN.md §2.4), domain level, straight from the committed snapshot."""

from datetime import time
from pathlib import Path

import pandas as pd
import pytest

from apps.analytics.domain.cutoff import CutoffPolicy, DailyGatePolicy, LeadTimePolicy
from apps.analytics.domain.pipeline import evaluate, lead_time_curve
from apps.market_data.parsers import parse_actuals, parse_forecasts
from apps.market_data.snapshots import read_snapshot
from tests.conftest import ts

pytestmark = pytest.mark.unit

SNAPSHOTS = Path(__file__).resolve().parents[2] / "data" / "snapshots"
DELIVERY = ts("2026-01-15T18:00Z")


@pytest.fixture(scope="module")
def frames() -> tuple[pd.DataFrame, pd.DataFrame]:
    fc = parse_forecasts(read_snapshot(SNAPSHOTS / "windfor.json.gz").rows).rows
    ac = parse_actuals(read_snapshot(SNAPSHOTS / "fuelhh_wind.json.gz").rows, fuel_type="WIND").rows
    forecasts = pd.DataFrame(
        {
            "start_time": pd.to_datetime([r.start_time for r in fc], utc=True),
            "publish_time": pd.to_datetime([r.publish_time for r in fc], utc=True),
            "generation_mw": [r.generation_mw for r in fc],
        }
    )
    actuals = pd.DataFrame(
        {
            "start_time": pd.to_datetime([r.start_time for r in ac], utc=True),
            "generation_mw": [r.generation_mw for r in ac],
        }
    )
    return forecasts, actuals


@pytest.mark.parametrize(
    ("policy", "publish", "forecast_mw", "error_mw"),
    [
        (DailyGatePolicy(time(10, 0), 1), "2026-01-14T09:30Z", 9053, 1381),
        (LeadTimePolicy(4), "2026-01-15T12:25Z", 9053, 1381),
        (LeadTimePolicy(0), "2026-01-15T15:30Z", 8302, 630),
    ],
)
def test_golden_delivery_hour(
    frames: tuple[pd.DataFrame, pd.DataFrame],
    policy: CutoffPolicy,
    publish: str,
    forecast_mw: int,
    error_mw: int,
) -> None:
    forecasts, actuals = frames
    row = evaluate(policy, forecasts, actuals, pd.Series([DELIVERY])).iloc[0]
    assert row["publish_time"] == ts(publish)
    assert row["forecast_mw"] == forecast_mw
    assert row["actual_mw"] == 7672
    assert row["error_mw"] == error_mw


def test_versions_after_delivery_start_are_never_selected(
    frames: tuple[pd.DataFrame, pd.DataFrame],
) -> None:
    forecasts, actuals = frames
    late = {ts("2026-01-15T18:30Z"), ts("2026-01-15T22:30Z")}
    for h in range(0, 49):
        row = evaluate(LeadTimePolicy(h), forecasts, actuals, pd.Series([DELIVERY])).iloc[0]
        assert row["publish_time"] not in late


def test_full_month_coverage_by_lead_time(frames: tuple[pd.DataFrame, pd.DataFrame]) -> None:
    forecasts, actuals = frames
    hours = pd.Series(
        pd.date_range("2026-01-01", "2026-02-01", freq="h", inclusive="left", tz="UTC")
    )
    curve = dict(lead_time_curve(forecasts, actuals, hours, [0, 43, 44, 48]))
    assert curve[0].n_expected == 744
    assert curve[0].n_matched == 744
    assert curve[43].n_missing_forecast == 0
    assert curve[44].n_missing_forecast == 31
    assert curve[48].n_missing_forecast == 155
