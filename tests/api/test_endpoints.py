"""HTTP-level tests through the DRF test client, on the real January 2026 snapshot."""

import io
from typing import Any

import pytest
from django.core.management import call_command
from django.test import override_settings
from rest_framework.test import APIClient
from rest_framework.throttling import AnonRateThrottle

pytestmark = pytest.mark.api

V1 = "/api/v1"


@pytest.fixture
def api() -> APIClient:
    return APIClient()


def get(api: APIClient, path: str, **params: Any) -> Any:
    return api.get(path, params)


# --- ops ------------------------------------------------------------------------------------
def test_health_needs_no_database(api: APIClient) -> None:
    response = api.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert "X-Request-ID" in response


@pytest.mark.usefixtures("empty_db")
def test_ready_is_503_without_data(api: APIClient) -> None:
    response = api.get("/ready")
    assert response.status_code == 503
    assert response["Content-Type"] == "application/problem+json"


@pytest.mark.usefixtures("snapshot_db")
def test_ready_with_data(api: APIClient) -> None:
    body = api.get("/ready").json()
    assert body == {"status": "ready", "forecast_rows": 21024, "actual_rows": 1488}


def test_unknown_path_is_a_json_problem(api: APIClient) -> None:
    response = api.get("/nope")
    assert response.status_code == 404
    assert response.json()["status"] == 404


def test_upstream_request_id_is_echoed(api: APIClient) -> None:
    response = api.get("/health", HTTP_X_REQUEST_ID="abcdef0123456789")
    assert response["X-Request-ID"] == "abcdef0123456789"


# --- meta / summary -------------------------------------------------------------------------
@pytest.mark.usefixtures("snapshot_db")
def test_meta(api: APIClient) -> None:
    body = api.get(f"{V1}/meta").json()
    assert body["evaluation_window"] == {
        "date_from": "2026-01-01",
        "date_to": "2026-01-31",
        "timezone": "Europe/London",
    }
    assert body["policies"]["default"] == "daily_gate"
    assert body["policies"]["daily_gate"]["gate_time_default"] == "10:00"
    assert body["policies"]["lead_time"]["lead_time_h_default"] == 24
    assert body["coverage"]["forecast"]["present"] == 744
    assert body["coverage"]["actual"]["present"] == 1488
    assert {run["dataset"] for run in body["ingestion"]} == {"WINDFOR", "FUELHH"}


@pytest.mark.usefixtures("snapshot_db")
def test_summary_defaults_to_d1_gate(api: APIClient) -> None:
    body = api.get(f"{V1}/summary").json()
    assert body["query"] == {
        "date_from": "2026-01-01",
        "date_to": "2026-01-31",
        "timezone": "Europe/London",
        "policy": "daily_gate",
        "gate_time": "10:00",
        "gate_days_before": 1,
        "lead_time_h": None,
    }
    assert body["metrics"]["n_matched"] == 744
    assert body["metrics"]["coverage_pct"] == 100.0
    assert body["data_version"] > 0


@pytest.mark.usefixtures("snapshot_db")
def test_summary_with_missing_hours_reports_them(api: APIClient) -> None:
    metrics = get(api, f"{V1}/summary", policy="lead_time", lead_time_h=48).json()["metrics"]
    assert metrics["n_missing_forecast"] == 155
    assert metrics["n_matched"] == 744 - 155


@pytest.mark.usefixtures("snapshot_db")
def test_one_day_summary(api: APIClient) -> None:
    body = get(api, f"{V1}/summary", date_from="2026-01-15", date_to="2026-01-15").json()
    assert body["metrics"]["n_expected"] == 24


# --- 422 problems ---------------------------------------------------------------------------
@pytest.mark.usefixtures("snapshot_db")
@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({"date_from": "2025-12-31"}, "date_from"),
        ({"date_to": "2026-02-01"}, "date_to"),
        ({"date_from": "2026-01-20", "date_to": "2026-01-10"}, "date_to"),
        ({"date_from": "15/01/2026"}, "date_from"),
        ({"policy": "magic"}, "policy"),
        ({"policy": "lead_time", "gate_time": "10:00"}, "gate_time"),
        ({"policy": "daily_gate", "lead_time_h": "4"}, "lead_time_h"),
        ({"lead_time_h": "4"}, "lead_time_h"),
        ({"policy": "lead_time", "lead_time_h": "49"}, "lead_time_h"),
        ({"policy": "lead_time", "lead_time_h": "-1"}, "lead_time_h"),
        ({"gate_days_before": "0"}, "gate_days_before"),
        ({"gate_days_before": "4"}, "gate_days_before"),
        ({"gate_time": "25:00"}, "gate_time"),
        ({"polcy": "lead_time"}, "polcy"),
    ],
)
def test_invalid_params_are_422_problems(
    api: APIClient, params: dict[str, str], field: str
) -> None:
    response = get(api, f"{V1}/summary", **params)
    assert response.status_code == 422
    assert response["Content-Type"] == "application/problem+json"
    body = response.json()
    assert body["status"] == 422
    assert body["type"].endswith("invalid-parameters")
    assert field in body["errors"]


# --- timeseries / intervals -----------------------------------------------------------------
@pytest.mark.usefixtures("snapshot_db")
def test_timeseries_shapes(api: APIClient) -> None:
    body = get(api, f"{V1}/timeseries", date_from="2026-01-15", date_to="2026-01-15").json()
    assert len(body["forecast"]) == 24
    assert len(body["actual"]) == 48
    point = next(p for p in body["forecast"] if p["start_time"] == "2026-01-15T18:00:00Z")
    assert point == {
        "start_time": "2026-01-15T18:00:00Z",
        "cutoff_time": "2026-01-14T10:00:00Z",
        "publish_time": "2026-01-14T09:30:00Z",
        "forecast_mw": 9053,
        "lead_time_h": 32.5,
        "actual_mw": 7672,
        "error_mw": 1381,
        "status": "matched",
    }


@pytest.mark.usefixtures("snapshot_db")
def test_timeseries_keeps_missing_hours_as_nulls(api: APIClient) -> None:
    body = get(api, f"{V1}/timeseries", policy="lead_time", lead_time_h=48).json()
    missing = [p for p in body["forecast"] if p["status"] == "missing_forecast"]
    assert len(missing) == 155
    assert all(p["forecast_mw"] is None and p["publish_time"] is None for p in missing)


@pytest.mark.usefixtures("snapshot_db")
def test_intervals_pagination(api: APIClient) -> None:
    body = get(api, f"{V1}/intervals", page=2, page_size=100).json()
    assert body["count"] == 744
    assert body["page"] == 2
    assert body["num_pages"] == 8
    assert body["next"] == 3
    assert body["previous"] == 1
    assert len(body["results"]) == 100
    assert body["results"][0]["start_time"] == "2026-01-05T04:00:00Z"


@pytest.mark.usefixtures("snapshot_db")
def test_intervals_ordering(api: APIClient) -> None:
    rows = get(api, f"{V1}/intervals", ordering="-abs_error_mw", page_size=5).json()["results"]
    errors = [r["abs_error_mw"] for r in rows]
    assert errors == sorted(errors, reverse=True)
    assert set(rows[0]) == {
        "start_time",
        "forecast_mw",
        "actual_mw",
        "error_mw",
        "abs_error_mw",
        "publish_time",
        "lead_time_h",
        "cutoff_time",
        "status",
    }


@pytest.mark.usefixtures("snapshot_db")
def test_intervals_missing_values_sort_last(api: APIClient) -> None:
    rows = get(
        api,
        f"{V1}/intervals",
        policy="lead_time",
        lead_time_h=48,
        ordering="-forecast_mw",
        page_size=800,
    ).json()["results"]
    statuses = [r["status"] for r in rows]
    assert statuses[-155:] == ["missing_forecast"] * 155


@pytest.mark.usefixtures("snapshot_db")
@pytest.mark.parametrize(
    "params",
    [{"ordering": "generation_mw"}, {"ordering": "-status"}, {"page": "99"}, {"page_size": "801"}],
)
def test_intervals_rejects_bad_ordering_and_pages(api: APIClient, params: dict[str, str]) -> None:
    assert get(api, f"{V1}/intervals", **params).status_code == 422


# --- accuracy -------------------------------------------------------------------------------
@pytest.mark.usefixtures("snapshot_db")
def test_breakdown_by_hour_and_day(api: APIClient) -> None:
    hours = get(api, f"{V1}/accuracy/breakdown", group_by="hour_of_day").json()
    assert [g["key"] for g in hours["groups"]] == [str(h) for h in range(24)]
    days = get(api, f"{V1}/accuracy/breakdown", group_by="day").json()
    assert len(days["groups"]) == 31
    assert days["groups"][0]["key"] == "2026-01-01"
    assert get(api, f"{V1}/accuracy/breakdown").status_code == 422


@pytest.mark.usefixtures("snapshot_db")
def test_lead_time_curve(api: APIClient) -> None:
    body = get(api, f"{V1}/accuracy/by-lead-time", min_h=40, max_h=48).json()
    points = {p["lead_time_h"]: p["metrics"] for p in body["points"]}
    assert list(points) == list(range(40, 49))
    assert points[43]["n_missing_forecast"] == 0
    assert points[44]["n_missing_forecast"] == 31
    assert get(api, f"{V1}/accuracy/by-lead-time", min_h=10, max_h=5).status_code == 422


# --- publication history (golden) ----------------------------------------------------------
@pytest.mark.usefixtures("snapshot_db")
@pytest.mark.parametrize(
    ("params", "publish", "forecast_mw", "error_mw", "cutoff"),
    [
        ({}, "2026-01-14T09:30:00Z", 9053, 1381, "2026-01-14T10:00:00Z"),
        (
            {"policy": "lead_time", "lead_time_h": 4},
            "2026-01-15T12:25:00Z",
            9053,
            1381,
            "2026-01-15T14:00:00Z",
        ),
        (
            {"policy": "lead_time", "lead_time_h": 0},
            "2026-01-15T15:30:00Z",
            8302,
            630,
            "2026-01-15T18:00:00Z",
        ),
    ],
)
def test_history_golden(
    api: APIClient,
    params: dict[str, Any],
    publish: str,
    forecast_mw: int,
    error_mw: int,
    cutoff: str,
) -> None:
    body = get(api, f"{V1}/forecasts/history", start_time="2026-01-15T18:00:00Z", **params).json()
    assert body["actual_mw"] == 7672
    assert body["cutoff_time"] == cutoff
    assert body["selected_publish_time"] == publish
    assert body["selected_forecast_mw"] == forecast_mw
    assert body["selected_error_mw"] == error_mw
    statuses = {v["publish_time"]: v["status"] for v in body["versions"]}
    assert statuses[publish] == "selected"
    assert statuses["2026-01-15T18:30:00Z"] == "after_delivery_start"
    assert statuses["2026-01-15T22:30:00Z"] == "after_delivery_start"
    assert list(statuses.values()).count("selected") == 1


@pytest.mark.usefixtures("snapshot_db")
@pytest.mark.parametrize(
    "start_time",
    ["2026-01-15T18:30:00Z", "2026-01-15T18:00:00", "2026-02-01T00:00:00Z", "garbage"],
)
def test_history_rejects_bad_start_time(api: APIClient, start_time: str) -> None:
    response = get(api, f"{V1}/forecasts/history", start_time=start_time)
    assert response.status_code == 422
    assert "start_time" in response.json()["errors"]


# --- HTTP caching ---------------------------------------------------------------------------
@pytest.mark.usefixtures("snapshot_db")
def test_etag_and_304(api: APIClient) -> None:
    first = api.get(f"{V1}/summary")
    assert "s-maxage=" in first["Cache-Control"]
    etag = first["ETag"]
    second = api.get(f"{V1}/summary", HTTP_IF_NONE_MATCH=etag)
    assert second.status_code == 304
    other = api.get(f"{V1}/summary", {"policy": "lead_time"}, HTTP_IF_NONE_MATCH=etag)
    assert other.status_code == 200
    assert other["ETag"] != etag


@pytest.mark.usefixtures("empty_db")
def test_etag_changes_with_data_version(api: APIClient) -> None:
    before = api.get(f"{V1}/summary")["ETag"]
    call_command("load_snapshot", stdout=io.StringIO())
    assert api.get(f"{V1}/summary")["ETag"] != before


# --- throttling and schema ------------------------------------------------------------------
@pytest.mark.usefixtures("snapshot_db")
def test_throttle_keys_on_forwarded_client_ip(
    api: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(AnonRateThrottle, "THROTTLE_RATES", {"anon": "2/min"})
    # client -> Vercel -> PythonAnywhere: Django sees "client, vercel" and 2 proxies.
    with override_settings(
        REST_FRAMEWORK={
            "DEFAULT_THROTTLE_CLASSES": ["rest_framework.throttling.AnonRateThrottle"],
            "NUM_PROXIES": 2,
            "UNAUTHENTICATED_USER": None,
            "EXCEPTION_HANDLER": "apps.core.exceptions.problem_exception_handler",
        }
    ):
        alice = {"HTTP_X_FORWARDED_FOR": "203.0.113.5, 76.76.21.21"}
        bob = {"HTTP_X_FORWARDED_FOR": "198.51.100.7, 76.76.21.21"}
        assert api.get(f"{V1}/meta", **alice).status_code == 200
        assert api.get(f"{V1}/meta", **alice).status_code == 200
        limited = api.get(f"{V1}/meta", **alice)
        assert limited.status_code == 429
        assert limited["Content-Type"] == "application/problem+json"
        assert api.get(f"{V1}/meta", **bob).status_code == 200


def test_openapi_schema_generates(api: APIClient) -> None:
    out = io.StringIO()
    call_command("spectacular", "--validate", "--fail-on-warn", stdout=out)
    schema = out.getvalue()
    for path in ("/api/v1/summary", "/api/v1/forecasts/history", "/api/v1/accuracy/by-lead-time"):
        assert path in schema
