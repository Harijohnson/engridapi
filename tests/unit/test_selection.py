from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from apps.analytics.domain.frames import FrameContractError
from apps.analytics.domain.selection import (
    LookAheadError,
    SelectionStatus,
    assert_no_look_ahead,
    select_forecasts,
)
from tests.conftest import ts

pytestmark = pytest.mark.unit


def forecasts(*rows: tuple[str, str, int]) -> pd.DataFrame:
    """rows: (start_time, publish_time, generation_mw)"""
    return pd.DataFrame(
        {
            "start_time": [ts(r[0]) for r in rows],
            "publish_time": [ts(r[1]) for r in rows],
            "generation_mw": [r[2] for r in rows],
        }
    )


def targets(*rows: tuple[str, str]) -> pd.DataFrame:
    """rows: (start_time, cutoff_time)"""
    return pd.DataFrame(
        {"start_time": [ts(r[0]) for r in rows], "cutoff_time": [ts(r[1]) for r in rows]}
    )


BRIEF_VERSIONS = forecasts(
    ("2026-01-15T18:00Z", "2026-01-14T08:00Z", 5000),
    ("2026-01-15T18:00Z", "2026-01-14T12:00Z", 4800),
    ("2026-01-15T18:00Z", "2026-01-15T08:00Z", 4600),
)


def test_brief_example_selects_5000_at_10_00_cutoff() -> None:
    result = select_forecasts(BRIEF_VERSIONS, targets(("2026-01-15T18:00Z", "2026-01-14T10:00Z")))
    row = result.iloc[0]
    assert row["forecast_mw"] == 5000
    assert row["publish_time"] == ts("2026-01-14T08:00Z")
    assert row["selection_status"] == SelectionStatus.SELECTED
    assert row["lead_time_h"] == pytest.approx(34.0)


def test_publish_equal_to_cutoff_is_eligible() -> None:
    result = select_forecasts(BRIEF_VERSIONS, targets(("2026-01-15T18:00Z", "2026-01-14T12:00Z")))
    assert result.iloc[0]["forecast_mw"] == 4800


def test_no_eligible_version_is_missing_forecast() -> None:
    result = select_forecasts(BRIEF_VERSIONS, targets(("2026-01-15T18:00Z", "2026-01-14T07:59Z")))
    row = result.iloc[0]
    assert row["selection_status"] == SelectionStatus.MISSING_FORECAST
    assert pd.isna(row["forecast_mw"])
    assert pd.isna(row["publish_time"])
    assert pd.isna(row["lead_time_h"])


def test_missing_hour_is_never_filled_from_a_neighbour() -> None:
    versions = forecasts(("2026-01-15T17:00Z", "2026-01-14T08:00Z", 7000))
    result = select_forecasts(
        versions,
        targets(
            ("2026-01-15T17:00Z", "2026-01-15T17:00Z"), ("2026-01-15T18:00Z", "2026-01-15T18:00Z")
        ),
    )
    assert list(result["selection_status"]) == ["selected", "missing_forecast"]
    assert pd.isna(result.iloc[1]["forecast_mw"])


def test_past_dated_versions_are_never_selected() -> None:
    # A version published after the hour started is never a decision input, even at H = 0.
    versions = forecasts(
        ("2026-01-15T18:00Z", "2026-01-15T15:30Z", 8302),
        ("2026-01-15T18:00Z", "2026-01-15T18:30Z", 8362),
    )
    result = select_forecasts(versions, targets(("2026-01-15T18:00Z", "2026-01-15T18:00Z")))
    assert result.iloc[0]["forecast_mw"] == 8302


def test_irregular_publish_time_is_handled() -> None:
    versions = forecasts(
        ("2026-01-15T18:00Z", "2026-01-15T09:30Z", 9100),
        ("2026-01-15T18:00Z", "2026-01-15T12:25Z", 9053),
        ("2026-01-15T18:00Z", "2026-01-15T15:30Z", 8302),
    )
    result = select_forecasts(versions, targets(("2026-01-15T18:00Z", "2026-01-15T14:00Z")))
    assert result.iloc[0]["publish_time"] == ts("2026-01-15T12:25Z")


def test_versions_for_other_hours_do_not_leak() -> None:
    versions = forecasts(
        ("2026-01-15T18:00Z", "2026-01-14T08:00Z", 5000),
        ("2026-01-15T19:00Z", "2026-01-14T09:00Z", 9999),
    )
    result = select_forecasts(versions, targets(("2026-01-15T18:00Z", "2026-01-14T10:00Z")))
    assert result.iloc[0]["forecast_mw"] == 5000


def test_empty_forecasts_give_all_missing() -> None:
    empty = forecasts()
    result = select_forecasts(empty, targets(("2026-01-15T18:00Z", "2026-01-15T18:00Z")))
    assert list(result["selection_status"]) == ["missing_forecast"]


def test_output_is_ordered_by_start_time() -> None:
    versions = forecasts(
        ("2026-01-15T18:00Z", "2026-01-15T08:00Z", 1),
        ("2026-01-15T17:00Z", "2026-01-15T09:00Z", 2),
    )
    result = select_forecasts(
        versions,
        # cutoffs in the opposite order to the start times
        targets(
            ("2026-01-15T18:00Z", "2026-01-15T08:00Z"), ("2026-01-15T17:00Z", "2026-01-15T09:00Z")
        ),
    )
    assert list(result["forecast_mw"]) == [2, 1]


def test_target_cutoff_after_start_is_rejected() -> None:
    with pytest.raises(LookAheadError):
        select_forecasts(BRIEF_VERSIONS, targets(("2026-01-15T18:00Z", "2026-01-15T19:00Z")))


def test_duplicate_versions_are_rejected() -> None:
    duplicated = pd.concat([BRIEF_VERSIONS, BRIEF_VERSIONS.iloc[:1]])
    with pytest.raises(FrameContractError):
        select_forecasts(duplicated, targets(("2026-01-15T18:00Z", "2026-01-14T10:00Z")))


def test_runtime_assertion_catches_look_ahead() -> None:
    bad = pd.DataFrame(
        {
            "start_time": [ts("2026-01-15T18:00Z")],
            "cutoff_time": [ts("2026-01-15T14:00Z")],
            "publish_time": [ts("2026-01-15T15:30Z")],
        }
    )
    with pytest.raises(LookAheadError):
        assert_no_look_ahead(bad)


# --- property-based: R1 and R2 hold for arbitrary versions and cutoffs ----------------------
BASE = datetime(2026, 1, 10, tzinfo=UTC)


@st.composite
def scenarios(draw: st.DrawFn) -> tuple[pd.DataFrame, pd.DataFrame]:
    hours = draw(st.lists(st.integers(0, 47), min_size=1, max_size=8, unique=True))
    rows: list[tuple[datetime, datetime, int]] = []
    for hour in hours:
        start = BASE + timedelta(hours=hour)
        offsets = draw(st.lists(st.integers(-1600, 4000), max_size=10, unique=True))
        rows.extend(
            (start, start - timedelta(minutes=off), draw(st.integers(0, 20000))) for off in offsets
        )
    target_rows = [
        (
            BASE + timedelta(hours=h),
            BASE + timedelta(hours=h) - timedelta(minutes=draw(st.integers(0, 4000))),
        )
        for h in hours
    ]
    fc = pd.DataFrame(rows, columns=["start_time", "publish_time", "generation_mw"])
    tg = pd.DataFrame(target_rows, columns=["start_time", "cutoff_time"])
    for frame in (fc, tg):
        for column in frame.columns:
            if column.endswith("_time"):
                frame[column] = pd.to_datetime(frame[column], utc=True)
    return fc, tg


@settings(max_examples=200, deadline=None)
@given(scenarios())
def test_selected_is_latest_eligible_and_never_after_cutoff(
    scenario: tuple[pd.DataFrame, pd.DataFrame],
) -> None:
    fc, tg = scenario
    result = select_forecasts(fc, tg)
    assert len(result) == len(tg)
    for row in result.itertuples(index=False):
        assert row.cutoff_time <= row.start_time
        eligible = fc[
            (fc["start_time"] == row.start_time) & (fc["publish_time"] <= row.cutoff_time)
        ]
        if eligible.empty:
            assert row.selection_status == "missing_forecast"
            assert pd.isna(row.forecast_mw)
        else:
            assert row.publish_time <= row.cutoff_time
            assert row.publish_time == eligible["publish_time"].max()
            latest = eligible.loc[eligible["publish_time"].idxmax(), "generation_mw"]
            assert row.forecast_mw == latest
