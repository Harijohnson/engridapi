import json
import logging
from datetime import date

import pytest

from apps.core.logging import JsonFormatter, RequestIdFilter
from apps.core.request_context import request_id_var
from apps.core.time import delivery_window_utc, iso_z, uk_midnight_utc

pytestmark = pytest.mark.unit


def test_json_log_line_carries_request_id_and_extras() -> None:
    record = logging.makeLogRecord(
        {"name": "engrid", "levelname": "INFO", "msg": "hello %s", "args": ("world",), "rows": 3}
    )
    token = request_id_var.set("req-12345678")
    try:
        RequestIdFilter().filter(record)
    finally:
        request_id_var.reset(token)
    line = json.loads(JsonFormatter().format(record))
    assert line["message"] == "hello world"
    assert line["request_id"] == "req-12345678"
    assert line["rows"] == 3
    assert line["time"].endswith("+00:00")


def test_json_log_line_includes_exceptions() -> None:
    try:
        raise ValueError("bad")
    except ValueError:
        import sys

        record = logging.makeLogRecord({"msg": "failed", "exc_info": sys.exc_info()})
    assert "ValueError: bad" in json.loads(JsonFormatter().format(record))["exc_info"]


def test_uk_day_lengths_across_clock_changes() -> None:
    spring = delivery_window_utc(date(2026, 3, 29), date(2026, 3, 29))
    autumn = delivery_window_utc(date(2026, 10, 25), date(2026, 10, 25))
    assert (spring[1] - spring[0]).total_seconds() == 23 * 3600
    assert (autumn[1] - autumn[0]).total_seconds() == 25 * 3600
    assert iso_z(uk_midnight_utc(date(2026, 7, 1))) == "2026-06-30T23:00:00Z"


def test_window_order_and_naive_formatting_are_rejected() -> None:
    from datetime import datetime

    with pytest.raises(ValueError, match="date_from"):
        delivery_window_utc(date(2026, 1, 2), date(2026, 1, 1))
    with pytest.raises(ValueError, match="naive"):
        iso_z(datetime(2026, 1, 1))  # noqa: DTZ001
