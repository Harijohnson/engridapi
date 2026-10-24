"""Elexon Insights API client (offline ingestion only, never on a request path: R11).

One method per dataset. Requests are split into chunks so a failure is small and resumable;
transient failures (network errors, 429, 5xx) are retried with exponential backoff.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Self

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

WINDFOR_STREAM_PATH = "/datasets/WINDFOR/stream"
FUELHH_STREAM_PATH = "/datasets/FUELHH/stream"
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


class ElexonError(RuntimeError):
    pass


class ElexonRetryableError(ElexonError):
    pass


@dataclass(frozen=True)
class ElexonChunk:
    """Raw rows of one request, with exactly what was asked for (for the audit trail)."""

    path: str
    params: dict[str, str]
    rows: list[dict[str, Any]]


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, ElexonRetryableError | httpx.TransportError)


def iter_datetime_windows(
    start: datetime, end: datetime, chunk: timedelta
) -> Iterator[tuple[datetime, datetime]]:
    """Split [start, end) into consecutive windows of at most `chunk`."""
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("window bounds must be timezone-aware")
    cursor = start
    while cursor < end:
        window_end = min(cursor + chunk, end)
        yield cursor, window_end
        cursor = window_end


def iter_date_windows(first: date, last: date, chunk_days: int) -> Iterator[tuple[date, date]]:
    """Split the inclusive date range [first, last] into inclusive, non-overlapping windows."""
    cursor = first
    while cursor <= last:
        window_last = min(cursor + timedelta(days=chunk_days - 1), last)
        yield cursor, window_last
        cursor = window_last + timedelta(days=1)


def _iso_z(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


class ElexonClient:
    def __init__(
        self,
        *,
        base_url: str,
        timeout_s: float,
        user_agent: str,
        max_attempts: int,
        chunk_days: int,
        transport: httpx.BaseTransport | None = None,
        backoff_max_s: float = 30.0,
    ) -> None:
        self._http = httpx.Client(
            base_url=base_url,
            timeout=httpx.Timeout(timeout_s, connect=min(timeout_s, 10.0)),
            headers={"User-Agent": user_agent, "Accept": "application/json"},
            transport=transport,
        )
        self._chunk_days = chunk_days
        self._get_json = retry(
            reraise=True,
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential(multiplier=0.5, max=backoff_max_s),
            retry=retry_if_exception(_is_retryable),
        )(self._get_json_once)

    @classmethod
    def from_settings(cls) -> Self:
        from django.conf import settings

        return cls(
            base_url=settings.ELEXON_BASE_URL,
            timeout_s=settings.ELEXON_TIMEOUT_S,
            user_agent=settings.ELEXON_USER_AGENT,
            max_attempts=settings.ELEXON_MAX_ATTEMPTS,
            chunk_days=settings.ELEXON_CHUNK_DAYS,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _get_json_once(self, path: str, params: dict[str, str]) -> list[dict[str, Any]]:
        response = self._http.get(path, params=params)
        if response.status_code in RETRYABLE_STATUS_CODES:
            logger.warning(
                "Elexon returned a retryable status",
                extra={"status": response.status_code, "path": path},
            )
            raise ElexonRetryableError(f"Elexon {path} returned {response.status_code}")
        if response.status_code != httpx.codes.OK:
            raise ElexonError(
                f"Elexon {path} returned {response.status_code}: {response.text[:200]}"
            )
        payload = response.json()
        # The stream endpoints return a bare list; other endpoints wrap rows in {"data": [...]}.
        rows = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise ElexonError(f"Elexon {path} returned an unexpected payload shape")
        return rows

    def windfor(self, publish_from: datetime, publish_to: datetime) -> Iterator[ElexonChunk]:
        """WINDFOR forecasts by publish time, in chunks of `chunk_days` days."""
        for window_from, window_to in iter_datetime_windows(
            publish_from, publish_to, timedelta(days=self._chunk_days)
        ):
            params = {
                "publishDateTimeFrom": _iso_z(window_from),
                "publishDateTimeTo": _iso_z(window_to),
            }
            rows = self._get_json(WINDFOR_STREAM_PATH, params)
            logger.info("Fetched WINDFOR chunk", extra={"params": params, "rows": len(rows)})
            yield ElexonChunk(path=WINDFOR_STREAM_PATH, params=params, rows=rows)

    def fuelhh(
        self, settlement_from: date, settlement_to: date, fuel_type: str
    ) -> Iterator[ElexonChunk]:
        """FUELHH actuals by settlement date (never by publish date: it spills over month edges)."""
        for window_from, window_to in iter_date_windows(
            settlement_from, settlement_to, self._chunk_days
        ):
            params = {
                "settlementDateFrom": window_from.isoformat(),
                "settlementDateTo": window_to.isoformat(),
                "fuelType": fuel_type,
            }
            rows = self._get_json(FUELHH_STREAM_PATH, params)
            logger.info("Fetched FUELHH chunk", extra={"params": params, "rows": len(rows)})
            yield ElexonChunk(path=FUELHH_STREAM_PATH, params=params, rows=rows)
