"""Idempotent ingestion of raw Elexon rows into the database, with an IngestionRun audit row.

Semantics (PLAN.md §5.4):
- Forecasts are immutable: insert-ignore. A different value for an existing key is a
  data-quality error: logged, counted as rejected, and the original row is kept.
- Actuals may be republished: the row with the latest publish_time wins.
- One transaction per chunk. A failed run never deletes existing data.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.market_data.models import (
    Dataset,
    GenerationActual,
    IngestionRun,
    IngestionSource,
    IngestionStatus,
    WindForecast,
)
from apps.market_data.parsers import (
    ActualRow,
    ForecastRow,
    RejectedRow,
    parse_actuals,
    parse_forecasts,
)

logger = logging.getLogger(__name__)

BULK_BATCH_SIZE = 2000
MAX_LOGGED_REJECTIONS = 20


@dataclass
class _Counts:
    received: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    rejected: int = 0


class _PayloadHasher:
    """SHA-256 over the canonical JSON of every raw row, in arrival order."""

    def __init__(self) -> None:
        self._sha = hashlib.sha256()

    def update(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            self._sha.update(json.dumps(row, sort_keys=True, separators=(",", ":")).encode())
            self._sha.update(b"\n")

    def hexdigest(self) -> str:
        return self._sha.hexdigest()


def _log_rejections(dataset: str, rejected: list[RejectedRow]) -> None:
    for item in rejected[:MAX_LOGGED_REJECTIONS]:
        logger.warning("Rejected %s row: %s", dataset, item.reason, extra={"raw": item.raw})
    if len(rejected) > MAX_LOGGED_REJECTIONS:
        logger.warning("%d more %s rows rejected", len(rejected) - MAX_LOGGED_REJECTIONS, dataset)


def _write_forecasts(rows: list[ForecastRow], run: IngestionRun, counts: _Counts) -> None:
    if not rows:
        return
    start_min = min(r.start_time for r in rows)
    start_max = max(r.start_time for r in rows)
    existing: dict[tuple[datetime, datetime], int] = {
        (start, publish): generation_mw
        for start, publish, generation_mw in WindForecast.objects.filter(
            start_time__gte=start_min, start_time__lte=start_max
        ).values_list("start_time", "publish_time", "generation_mw")
    }
    new_rows: list[WindForecast] = []
    for row in rows:
        stored = existing.get((row.start_time, row.publish_time))
        if stored is None:
            new_rows.append(
                WindForecast(
                    publish_time=row.publish_time,
                    start_time=row.start_time,
                    generation_mw=row.generation_mw,
                    ingestion_run=run,
                )
            )
        elif stored == row.generation_mw:
            counts.unchanged += 1
        else:
            counts.rejected += 1
            logger.error(
                "WINDFOR conflict: existing forecast kept",
                extra={
                    "start_time": row.start_time.isoformat(),
                    "publish_time": row.publish_time.isoformat(),
                    "stored_mw": stored,
                    "incoming_mw": row.generation_mw,
                },
            )
    WindForecast.objects.bulk_create(new_rows, batch_size=BULK_BATCH_SIZE, ignore_conflicts=True)
    counts.created += len(new_rows)


def _write_actuals(rows: list[ActualRow], run: IngestionRun, counts: _Counts) -> None:
    if not rows:
        return
    fuel_types = {r.fuel_type for r in rows}
    existing = {
        (actual.fuel_type, actual.start_time): actual
        for actual in GenerationActual.objects.filter(
            fuel_type__in=fuel_types,
            start_time__gte=min(r.start_time for r in rows),
            start_time__lte=max(r.start_time for r in rows),
        )
    }
    to_create: list[GenerationActual] = []
    to_update: list[GenerationActual] = []
    for row in rows:
        stored = existing.get((row.fuel_type, row.start_time))
        if stored is None:
            to_create.append(
                GenerationActual(
                    start_time=row.start_time,
                    settlement_date=row.settlement_date,
                    settlement_period=row.settlement_period,
                    fuel_type=row.fuel_type,
                    generation_mw=row.generation_mw,
                    publish_time=row.publish_time,
                    ingestion_run=run,
                )
            )
        elif row.publish_time > stored.publish_time and (
            row.generation_mw != stored.generation_mw
            or row.settlement_period != stored.settlement_period
            or row.settlement_date != stored.settlement_date
        ):
            stored.generation_mw = row.generation_mw
            stored.settlement_date = row.settlement_date
            stored.settlement_period = row.settlement_period
            stored.publish_time = row.publish_time
            stored.ingestion_run = run
            to_update.append(stored)
        else:
            counts.unchanged += 1
    GenerationActual.objects.bulk_create(to_create, batch_size=BULK_BATCH_SIZE)
    GenerationActual.objects.bulk_update(
        to_update,
        ["generation_mw", "settlement_date", "settlement_period", "publish_time", "ingestion_run"],
        batch_size=BULK_BATCH_SIZE,
    )
    counts.created += len(to_create)
    counts.updated += len(to_update)


def ingest(
    dataset: Dataset,
    chunks: Iterable[list[dict[str, Any]]],
    *,
    source: IngestionSource,
    request_params: dict[str, Any],
    fuel_type: str = "WIND",
) -> IngestionRun:
    """Ingest raw rows chunk by chunk and return the finished IngestionRun.

    `chunks` may be a lazy iterator (the live client) or a single list (a snapshot). On any
    exception the run is marked failed with the message, and the exception is re-raised.
    """
    run = IngestionRun.objects.create(
        dataset=dataset,
        source=source,
        request_params=request_params,
        started_at=timezone.now(),
        status=IngestionStatus.RUNNING,
    )
    counts = _Counts()
    hasher = _PayloadHasher()
    try:
        for raw_rows in chunks:
            hasher.update(raw_rows)
            counts.received += len(raw_rows)
            with transaction.atomic():
                if dataset == Dataset.WINDFOR:
                    forecasts = parse_forecasts(raw_rows)
                    counts.unchanged += forecasts.duplicates
                    counts.rejected += len(forecasts.rejected)
                    _log_rejections(dataset, forecasts.rejected)
                    _write_forecasts(forecasts.rows, run, counts)
                else:
                    actuals = parse_actuals(raw_rows, fuel_type=fuel_type)
                    counts.unchanged += actuals.duplicates
                    counts.rejected += len(actuals.rejected)
                    _log_rejections(dataset, actuals.rejected)
                    _write_actuals(actuals.rows, run, counts)
    except Exception as exc:
        run.status = IngestionStatus.FAILED
        run.error_message = f"{type(exc).__name__}: {exc}"[:2000]
        raise
    else:
        run.status = IngestionStatus.SUCCEEDED
    finally:
        run.finished_at = timezone.now()
        run.rows_received = counts.received
        run.rows_created = counts.created
        run.rows_updated = counts.updated
        run.rows_unchanged = counts.unchanged
        run.rows_rejected = counts.rejected
        run.payload_sha256 = hasher.hexdigest()
        run.save()
        logger.info(
            "Ingestion run finished",
            extra={
                "run_id": run.pk,
                "dataset": dataset,
                "source": source,
                "status": run.status,
                "rows_received": counts.received,
                "rows_created": counts.created,
                "rows_updated": counts.updated,
                "rows_unchanged": counts.unchanged,
                "rows_rejected": counts.rejected,
            },
        )
    return run
