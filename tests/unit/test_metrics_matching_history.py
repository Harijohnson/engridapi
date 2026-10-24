import math

import numpy as np
import pandas as pd
import pytest

from apps.analytics.domain.frames import FrameContractError
from apps.analytics.domain.history import VersionStatus, label_versions
from apps.analytics.domain.matching import IntervalStatus, SameStartTimeMatching
from apps.analytics.domain.metrics import GroupBy, breakdown, summarize
from apps.analytics.domain.selection import LookAheadError
from tests.conftest import ts

pytestmark = pytest.mark.unit


def intervals(errors: list[float | None], forecast: float = 1000.0) -> pd.DataFrame:
    """Matched intervals with the given errors (None = missing actual)."""
    status = [
        IntervalStatus.MATCHED.value if e is not None else IntervalStatus.MISSING_ACTUAL.value
        for e in errors
    ]
    return pd.DataFrame(
        {
            "start_time": pd.date_range("2026-01-01", periods=len(errors), freq="h", tz="UTC"),
            "status": status,
            "forecast_mw": [forecast] * len(errors),
            "actual_mw": [forecast - e if e is not None else np.nan for e in errors],
            "error_mw": [e if e is not None else np.nan for e in errors],
        }
    )


# --- metrics -------------------------------------------------------------------------------
def test_hand_computed_metrics() -> None:
    m = summarize(intervals([500, -300, 100]))
    assert m.n_matched == 3
    assert m.mae_mw == pytest.approx(300.0)
    assert m.rmse_mw == pytest.approx(math.sqrt((500**2 + 300**2 + 100**2) / 3))
    assert m.rmse_mw == pytest.approx(341.565, abs=1e-3)
    assert m.bias_mw == pytest.approx(100.0)
    assert m.median_abs_error_mw == pytest.approx(300.0)
    assert m.max_abs_error_mw == pytest.approx(500.0)
    # linear interpolation: sorted |e| = [100, 300, 500]; P90 at rank 1.8 -> 300 + 0.8 * 200
    assert m.p90_abs_error_mw == pytest.approx(460.0)
    assert m.mean_forecast_mw == pytest.approx(1000.0)
    assert m.mean_actual_mw == pytest.approx(900.0)
    assert m.coverage_pct == pytest.approx(100.0)


def test_no_matched_pairs_gives_none_not_zero() -> None:
    m = summarize(intervals([None, None]))
    assert m.n_matched == 0
    assert m.n_missing_actual == 2
    assert m.coverage_pct == 0.0
    for value in (
        m.mae_mw,
        m.rmse_mw,
        m.bias_mw,
        m.median_abs_error_mw,
        m.p90_abs_error_mw,
        m.max_abs_error_mw,
        m.mean_forecast_mw,
        m.mean_actual_mw,
    ):
        assert value is None


def test_empty_frame_has_no_coverage() -> None:
    m = summarize(intervals([]))
    assert m.n_expected == 0
    assert m.coverage_pct is None


def test_single_row() -> None:
    m = summarize(intervals([-250]))
    assert (m.mae_mw, m.rmse_mw, m.bias_mw) == (250.0, 250.0, -250.0)


def test_missing_rows_are_excluded_but_counted() -> None:
    m = summarize(intervals([100, None, -100]))
    assert m.n_expected == 3
    assert m.n_matched == 2
    assert m.n_missing_actual == 1
    assert m.bias_mw == 0.0
    assert m.coverage_pct == pytest.approx(200 / 3)


def test_breakdown_by_hour_keeps_groups_without_matches() -> None:
    frame = intervals([100, None])
    groups = breakdown(frame, GroupBy.HOUR_OF_DAY)
    assert [key for key, _ in groups] == [0, 1]
    assert groups[1][1].mae_mw is None


def test_breakdown_by_day_uses_uk_dates() -> None:
    frame = intervals([10] * 25)
    groups = breakdown(frame, GroupBy.DAY)
    assert [key for key, _ in groups] == ["2026-01-01", "2026-01-02"]
    assert groups[0][1].n_expected == 24


# --- matching (D2) ---------------------------------------------------------------------------
def selected(rows: list[tuple[str, float | None]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "start_time": [ts(r[0]) for r in rows],
            "cutoff_time": [ts(r[0]) for r in rows],
            "publish_time": [ts(r[0]) if r[1] is not None else pd.NaT for r in rows],
            "forecast_mw": [r[1] if r[1] is not None else np.nan for r in rows],
            "lead_time_h": [0.0 if r[1] is not None else np.nan for r in rows],
            "selection_status": [
                "selected" if r[1] is not None else "missing_forecast" for r in rows
            ],
        }
    )


def actuals(rows: list[tuple[str, int]]) -> pd.DataFrame:
    return pd.DataFrame(
        {"start_time": [ts(r[0]) for r in rows], "generation_mw": [r[1] for r in rows]}
    )


def test_matching_uses_the_half_hour_starting_at_the_same_time() -> None:
    result = SameStartTimeMatching().match(
        selected([("2026-01-15T18:00Z", 9053)]),
        actuals([("2026-01-15T18:00Z", 7672), ("2026-01-15T18:30Z", 7593)]),
    )
    row = result.iloc[0]
    assert row["actual_mw"] == 7672
    assert row["error_mw"] == 1381  # forecast - actual: positive = over-forecast
    assert row["status"] == IntervalStatus.MATCHED


def test_matching_statuses() -> None:
    result = SameStartTimeMatching().match(
        selected(
            [
                ("2026-01-15T10:00Z", 100),
                ("2026-01-15T11:00Z", None),
                ("2026-01-15T12:00Z", 100),
                ("2026-01-15T13:00Z", None),
            ]
        ),
        actuals([("2026-01-15T10:00Z", 150), ("2026-01-15T11:00Z", 90)]),
    )
    assert list(result["status"]) == [
        "matched",
        "missing_forecast",
        "missing_actual",
        "missing_both",
    ]
    assert list(result["error_mw"].isna()) == [False, True, True, True]
    assert result.iloc[0]["abs_error_mw"] == 50


def test_duplicate_actuals_are_rejected() -> None:
    with pytest.raises(FrameContractError):
        SameStartTimeMatching().match(
            selected([("2026-01-15T10:00Z", 100)]),
            actuals([("2026-01-15T10:00Z", 1), ("2026-01-15T10:00Z", 2)]),
        )


# --- publication history ---------------------------------------------------------------------
def test_history_labels() -> None:
    versions = pd.DataFrame(
        {
            "publish_time": [
                ts(v)
                for v in (
                    "2026-01-15T22:30Z",
                    "2026-01-14T09:30Z",
                    "2026-01-14T11:30Z",
                    "2026-01-15T18:30Z",
                    "2026-01-13T02:30Z",
                    "2026-01-15T15:30Z",
                )
            ],
            "generation_mw": [8362, 9053, 9151, 8362, 10619, 8302],
        }
    )
    labelled = label_versions(versions, ts("2026-01-15T18:00Z"), ts("2026-01-14T10:00Z"))
    assert list(labelled["publish_time"]) == sorted(labelled["publish_time"])
    assert list(labelled["status"]) == [
        VersionStatus.SUPERSEDED,
        VersionStatus.SELECTED,
        VersionStatus.AFTER_CUTOFF,
        VersionStatus.AFTER_CUTOFF,
        VersionStatus.AFTER_DELIVERY_START,
        VersionStatus.AFTER_DELIVERY_START,
    ]
    assert labelled.iloc[-1]["lead_time_h"] == pytest.approx(-4.5)


def test_history_with_no_eligible_version_has_no_selected() -> None:
    versions = pd.DataFrame({"publish_time": [ts("2026-01-15T15:30Z")], "generation_mw": [1]})
    labelled = label_versions(versions, ts("2026-01-15T18:00Z"), ts("2026-01-15T12:00Z"))
    assert list(labelled["status"]) == [VersionStatus.AFTER_CUTOFF]


def test_history_rejects_cutoff_after_start() -> None:
    versions = pd.DataFrame({"publish_time": [ts("2026-01-15T15:30Z")], "generation_mw": [1]})
    with pytest.raises(LookAheadError):
        label_versions(versions, ts("2026-01-15T18:00Z"), ts("2026-01-15T19:00Z"))
