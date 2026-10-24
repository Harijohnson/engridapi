"""Seed the database from the committed Elexon snapshot (D10). No network needed."""

from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.market_data.ingestion import ingest
from apps.market_data.models import IngestionSource
from apps.market_data.snapshots import SNAPSHOT_FILES, SnapshotError, read_snapshot


class Command(BaseCommand):
    help = "Load the committed snapshot files (WINDFOR + FUELHH) into the database. Idempotent."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "files",
            nargs="*",
            type=Path,
            help="Snapshot files to load (default: every file in data/snapshots/).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        files: list[Path] = options["files"] or [
            settings.ENGRID_SNAPSHOT_DIR / name for name in SNAPSHOT_FILES.values()
        ]
        for path in files:
            if not path.exists():
                raise CommandError(f"Snapshot not found: {path}")
            try:
                snapshot = read_snapshot(path)
            except SnapshotError as exc:
                raise CommandError(str(exc)) from exc
            run = ingest(
                snapshot.dataset,
                [snapshot.rows],
                source=IngestionSource.SNAPSHOT,
                request_params={
                    "file": path.name,
                    "endpoint": snapshot.endpoint,
                    "fetched_at": snapshot.fetched_at,
                    **snapshot.request_params,
                },
                fuel_type=settings.ENGRID_FUEL_TYPE,
            )
            self.stdout.write(
                f"{snapshot.dataset} from {path.name}: run #{run.pk} {run.status}; "
                f"received {run.rows_received}, created {run.rows_created}, "
                f"updated {run.rows_updated}, unchanged {run.rows_unchanged}, "
                f"rejected {run.rows_rejected}"
            )
