"""Ingestion against the database, with Elexon mocked by respx (no real network)."""

import io
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from django.conf import settings
from django.core.management import call_command

from apps.market_data.clients.elexon import ElexonClient, ElexonError
from apps.market_data.ingestion import ingest
from apps.market_data.models import (
    Dataset,
    GenerationActual,
    IngestionRun,
    IngestionSource,
    IngestionStatus,
    WindForecast,
)
from apps.market_data.snapshots import read_snapshot, write_snapshot
from tests.conftest import load_fixture

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("empty_db")]

WINDFOR_ROWS = load_fixture("windfor_two_publishes_2026-01-14.json")
FUELHH_ROWS = load_fixture("fuelhh_wind_2026-01-15.json")
BASE = "https://elexon.test/bmrs/api/v1"


def client(**overrides: Any) -> ElexonClient:
    options: dict[str, Any] = {
        "base_url": BASE,
        "timeout_s": 5.0,
        "user_agent": "engrid-tests",
        "max_attempts": 3,
        "chunk_days": 7,
        "backoff_max_s": 0.0,
    }
    options.update(overrides)
    return ElexonClient(**options)


def run_windfor(rows: list[dict[str, Any]]) -> IngestionRun:
    return ingest(Dataset.WINDFOR, [rows], source=IngestionSource.API, request_params={})


def test_ingestion_is_idempotent() -> None:
    first = run_windfor(WINDFOR_ROWS)
    second = run_windfor(WINDFOR_ROWS)
    assert first.status == IngestionStatus.SUCCEEDED
    assert first.rows_created == 146
    assert second.rows_created == 0
    assert second.rows_unchanged == 146
    assert WindForecast.objects.count() == 146
    assert first.payload_sha256 == second.payload_sha256


def test_conflicting_forecast_keeps_the_original() -> None:
    run_windfor(WINDFOR_ROWS[:1])
    changed = {**WINDFOR_ROWS[0], "generation": WINDFOR_ROWS[0]["generation"] + 1}
    run = run_windfor([changed])
    assert run.rows_rejected == 1
    assert run.rows_created == 0
    assert WindForecast.objects.get().generation_mw == WINDFOR_ROWS[0]["generation"]


def test_malformed_rows_are_counted_not_written() -> None:
    run = run_windfor([*WINDFOR_ROWS[:2], {"dataset": "WINDFOR", "generation": "x"}])
    assert run.rows_received == 3
    assert run.rows_created == 2
    assert run.rows_rejected == 1


def test_republished_actual_updates_the_row() -> None:
    ingest(Dataset.FUELHH, [FUELHH_ROWS], source=IngestionSource.API, request_params={})
    newer = {**FUELHH_ROWS[0], "publishTime": "2026-01-20T00:00:00Z", "generation": 1}
    run = ingest(Dataset.FUELHH, [[newer]], source=IngestionSource.API, request_params={})
    assert run.rows_updated == 1
    assert GenerationActual.objects.get(start_time=newer["startTime"]).generation_mw == 1
    assert GenerationActual.objects.count() == 48


def test_failed_run_is_recorded_and_existing_data_kept() -> None:
    run_windfor(WINDFOR_ROWS)

    def exploding_chunks() -> Any:
        yield WINDFOR_ROWS[:1]
        raise ElexonError("boom")

    with pytest.raises(ElexonError):
        ingest(Dataset.WINDFOR, exploding_chunks(), source=IngestionSource.API, request_params={})
    failed = IngestionRun.objects.order_by("-id").first()
    assert failed is not None
    assert failed.status == IngestionStatus.FAILED
    assert "boom" in failed.error_message
    assert WindForecast.objects.count() == 146


@respx.mock
def test_client_retries_on_429_and_5xx() -> None:
    route = respx.get(f"{BASE}/datasets/FUELHH/stream").mock(
        side_effect=[
            httpx.Response(429),
            httpx.Response(503),
            httpx.Response(200, json=FUELHH_ROWS),
        ]
    )
    with client() as elexon:
        chunks = list(elexon.fuelhh(date(2026, 1, 15), date(2026, 1, 15), "WIND"))
    assert route.call_count == 3
    assert len(chunks[0].rows) == 48
    assert route.calls.last.request.url.params["fuelType"] == "WIND"
    assert route.calls.last.request.headers["User-Agent"] == "engrid-tests"


@respx.mock
def test_client_gives_up_after_max_attempts() -> None:
    respx.get(f"{BASE}/datasets/FUELHH/stream").mock(return_value=httpx.Response(500))
    with client() as elexon, pytest.raises(ElexonError):
        list(elexon.fuelhh(date(2026, 1, 15), date(2026, 1, 15), "WIND"))


@respx.mock
def test_client_does_not_retry_client_errors() -> None:
    route = respx.get(f"{BASE}/datasets/WINDFOR/stream").mock(return_value=httpx.Response(400))
    with client() as elexon, pytest.raises(ElexonError):
        list(elexon.windfor(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC)))
    assert route.call_count == 1


@respx.mock
def test_windfor_is_requested_in_chunks_by_publish_time() -> None:
    route = respx.get(f"{BASE}/datasets/WINDFOR/stream").mock(
        return_value=httpx.Response(200, json=[])
    )
    with client() as elexon:
        list(elexon.windfor(datetime(2025, 12, 27, tzinfo=UTC), datetime(2026, 2, 1, tzinfo=UTC)))
    assert route.call_count == 6
    first = route.calls[0].request.url.params
    assert first["publishDateTimeFrom"] == "2025-12-27T00:00:00Z"
    assert first["publishDateTimeTo"] == "2026-01-03T00:00:00Z"


@respx.mock
def test_snapshot_path_equals_api_path(tmp_path: Path) -> None:
    respx.get(f"{BASE}/datasets/WINDFOR/stream").mock(
        return_value=httpx.Response(200, json=WINDFOR_ROWS)
    )
    with client(chunk_days=31) as elexon:
        chunks = list(
            elexon.windfor(datetime(2026, 1, 14, tzinfo=UTC), datetime(2026, 1, 15, tzinfo=UTC))
        )
    ingest(Dataset.WINDFOR, [c.rows for c in chunks], source=IngestionSource.API, request_params={})
    from_api = set(WindForecast.objects.values_list("start_time", "publish_time", "generation_mw"))

    WindForecast.objects.all().delete()
    path = tmp_path / "windfor.json.gz"
    write_snapshot(
        path,
        dataset=Dataset.WINDFOR,
        endpoint=BASE,
        request_params={},
        fetched_at=datetime(2026, 10, 24, tzinfo=UTC),
        rows=chunks[0].rows,
    )
    call_command("load_snapshot", str(path), stdout=io.StringIO())
    from_snapshot = set(
        WindForecast.objects.values_list("start_time", "publish_time", "generation_mw")
    )
    assert from_snapshot == from_api
    assert len(from_snapshot) == 146
    assert read_snapshot(path).rows == WINDFOR_ROWS


def test_committed_snapshot_reproduces_plan_counts() -> None:
    out = io.StringIO()
    call_command("load_snapshot", stdout=out)
    assert WindForecast.objects.count() == 21024
    assert GenerationActual.objects.filter(fuel_type="WIND").count() == 1488
    report = io.StringIO()
    call_command("validate_data", "--strict", stdout=report)
    text = report.getvalue()
    assert "744 / 744" in text
    assert "1488 / 1488" in text
    assert "min 15, max 23" in text
    assert settings.ENGRID_SNAPSHOT_DIR.exists()
