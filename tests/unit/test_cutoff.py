from datetime import time

import pandas as pd
import pytest

from apps.analytics.domain.cutoff import (
    DailyGatePolicy,
    InvalidCutoffPolicyError,
    LeadTimePolicy,
    compute_cutoffs,
)
from apps.analytics.domain.frames import FrameContractError
from tests.conftest import ts

pytestmark = pytest.mark.unit


def starts(*values: str) -> pd.Series:
    return pd.Series([ts(v) for v in values])


def test_lead_time_subtracts_hours() -> None:
    cutoffs = compute_cutoffs(LeadTimePolicy(4), starts("2026-01-15T18:00Z"))
    assert cutoffs.iloc[0] == ts("2026-01-15T14:00Z")


def test_lead_time_zero_is_the_delivery_start() -> None:
    cutoffs = compute_cutoffs(LeadTimePolicy(0), starts("2026-01-15T18:00Z"))
    assert cutoffs.iloc[0] == ts("2026-01-15T18:00Z")


def test_negative_lead_time_is_rejected() -> None:
    with pytest.raises(InvalidCutoffPolicyError):
        LeadTimePolicy(-1)


def test_daily_gate_in_january_is_gmt() -> None:
    policy = DailyGatePolicy(gate_time=time(10, 0), days_before=1)
    cutoffs = compute_cutoffs(
        policy, starts("2026-01-15T00:00Z", "2026-01-15T18:00Z", "2026-01-15T23:00Z")
    )
    assert set(cutoffs) == {ts("2026-01-14T10:00Z")}


def test_daily_gate_uses_bst_offset_after_spring_change() -> None:
    # UK clocks go forward on Sunday 2026-03-29. Delivery on 03-31 -> gate 03-30 10:00 BST.
    policy = DailyGatePolicy(gate_time=time(10, 0), days_before=1)
    cutoffs = compute_cutoffs(policy, starts("2026-03-31T12:00Z"))
    assert cutoffs.iloc[0] == ts("2026-03-30T09:00Z")


def test_daily_gate_on_the_spring_change_day_uses_gmt_gate_date() -> None:
    # Delivery on 03-29 (the change day) -> gate on 03-28, still GMT.
    policy = DailyGatePolicy(gate_time=time(10, 0), days_before=1)
    cutoffs = compute_cutoffs(policy, starts("2026-03-29T05:00Z"))
    assert cutoffs.iloc[0] == ts("2026-03-28T10:00Z")


def test_daily_gate_after_autumn_change() -> None:
    # Clocks go back on Sunday 2026-10-25. Delivery 10-26 -> gate 10-25 10:00 GMT.
    policy = DailyGatePolicy(gate_time=time(10, 0), days_before=1)
    cutoffs = compute_cutoffs(policy, starts("2026-10-26T06:00Z"))
    assert cutoffs.iloc[0] == ts("2026-10-25T10:00Z")


def test_daily_gate_uses_uk_delivery_date_not_utc_date() -> None:
    # 2026-07-01T23:00Z is 00:00 BST on 07-02, so the UK delivery date is 07-02.
    policy = DailyGatePolicy(gate_time=time(10, 0), days_before=1)
    cutoffs = compute_cutoffs(policy, starts("2026-07-01T23:00Z"))
    assert cutoffs.iloc[0] == ts("2026-07-01T09:00Z")


def test_gate_after_delivery_raises() -> None:
    # Same-day gate at 10:00 cannot serve a 06:00 delivery.
    policy = DailyGatePolicy(gate_time=time(10, 0), days_before=0)
    with pytest.raises(InvalidCutoffPolicyError):
        compute_cutoffs(policy, starts("2026-01-15T06:00Z"))


def test_naive_start_times_are_rejected() -> None:
    with pytest.raises(FrameContractError):
        compute_cutoffs(LeadTimePolicy(1), pd.Series([pd.Timestamp("2026-01-15T18:00")]))


def test_policy_description_is_auditable() -> None:
    assert DailyGatePolicy(time(9, 30), 2).describe() == {
        "policy": "daily_gate",
        "gate_time": "09:30",
        "gate_days_before": 2,
    }
    assert LeadTimePolicy(24).describe() == {"policy": "lead_time", "lead_time_h": 24}
