"""Live ingestion from the Elexon Insights API (offline step; never on a request path, R11).

manage.py ingest_elexon --dataset windfor --from 2025-12-27T00:00Z --to 2026-02-01T00:00Z
manage.py ingest_elexon --dataset fuelhh --from 2026-01-01 --to 2026-01-31
add --save-snapshot to also (re)write the committed snapshot file from what was fetched
"""

from collections.abc import Iterator
from datetime import date, datetime
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.utils import timezone

from apps.core.time import iso_z
from apps.market_data.clients.elexon import ElexonChunk, ElexonClient
from apps.market_data.ingestion import ingest
from apps.market_data.models import Dataset, IngestionSource
from apps.market_data.parsers import RowError, parse_utc
from apps.market_data.snapshots import SNAPSHOT_FILES, write_snapshot


def _parse_bound(value: str) -> datetime:
    try:
        return parse_utc(value, "bound")
    except RowError as exc:
        raise CommandError(f"{value!r} must be an ISO-8601 datetime with a time zone") from exc


class Command(BaseCommand):
    help = "Fetch WINDFOR or FUELHH from Elexon and upsert it into the database."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--dataset", required=True, choices=["windfor", "fuelhh"])
        parser.add_argument(
            "--from",
            dest="start",
            required=True,
            help="windfor: publish datetime (UTC, e.g. 2025-12-27T00:00Z); fuelhh: settlement date",
        )
        parser.add_argument(
            "--to",
            dest="end",
            required=True,
            help="windfor: publish datetime (exclusive); fuelhh: settlement date (inclusive)",
        )
        parser.add_argument(
            "--save-snapshot",
            action="store_true",
            help="Also write the fetched raw rows to data/snapshots/ (D10).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        dataset = Dataset.WINDFOR if options["dataset"] == "windfor" else Dataset.FUELHH
        fuel_type = settings.ENGRID_FUEL_TYPE
        fetched: list[dict[str, Any]] = []
        endpoint = ""

        with ElexonClient.from_settings() as client:
            if dataset == Dataset.WINDFOR:
                start, end = _parse_bound(options["start"]), _parse_bound(options["end"])
                if start >= end:
                    raise CommandError("--from must be before --to")
                stream = client.windfor(start, end)
                request_params: dict[str, Any] = {
                    "publishDateTimeFrom": iso_z(start),
                    "publishDateTimeTo": iso_z(end),
                }
            else:
                try:
                    first = date.fromisoformat(options["start"])
                    last = date.fromisoformat(options["end"])
                except ValueError as exc:
                    raise CommandError("fuelhh --from/--to must be YYYY-MM-DD") from exc
                if first > last:
                    raise CommandError("--from must be on or before --to")
                stream = client.fuelhh(first, last, fuel_type)
                request_params = {
                    "settlementDateFrom": first.isoformat(),
                    "settlementDateTo": last.isoformat(),
                    "fuelType": fuel_type,
                }

            def rows_of(chunks: Iterator[ElexonChunk]) -> Iterator[list[dict[str, Any]]]:
                nonlocal endpoint
                for chunk in chunks:
                    endpoint = settings.ELEXON_BASE_URL + chunk.path
                    fetched.extend(chunk.rows)
                    yield chunk.rows

            run = ingest(
                dataset,
                rows_of(stream),
                source=IngestionSource.API,
                request_params=request_params,
                fuel_type=fuel_type,
            )

        self.stdout.write(
            f"{dataset} run #{run.pk} {run.status}: received {run.rows_received}, "
            f"created {run.rows_created}, updated {run.rows_updated}, "
            f"unchanged {run.rows_unchanged}, rejected {run.rows_rejected}"
        )
        if options["save_snapshot"]:
            path = settings.ENGRID_SNAPSHOT_DIR / SNAPSHOT_FILES[dataset]
            write_snapshot(
                path,
                dataset=dataset,
                endpoint=endpoint,
                request_params=request_params,
                fetched_at=timezone.now(),
                rows=fetched,
            )
            self.stdout.write(f"Snapshot written: {path} ({len(fetched)} rows)")
