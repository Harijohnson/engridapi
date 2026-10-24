"""Coverage / gaps report for the evaluation window (reproduces PLAN.md §2 from the database)."""

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser

from apps.core.time import iso_z
from apps.market_data.services import coverage_report

MAX_LISTED_GAPS = 10


class Command(BaseCommand):
    help = (
        "Print forecast and actual coverage for the evaluation window. Exit 1 on gaps if --strict."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--strict", action="store_true", help="Fail if anything is missing.")

    def handle(self, *args: Any, **options: Any) -> None:
        report = coverage_report(
            settings.ENGRID_EVALUATION_DATE_FROM,
            settings.ENGRID_EVALUATION_DATE_TO,
            settings.ENGRID_FUEL_TYPE,
        )
        f, a = report.forecast, report.actual
        self.stdout.write(f"Evaluation window (UK dates): {report.date_from} .. {report.date_to}")
        self.stdout.write("WINDFOR")
        self.stdout.write(f"  rows in window:            {f.rows}")
        self.stdout.write(f"  distinct publishes:        {f.distinct_publishes}")
        self.stdout.write(
            f"  delivery hours covered:    {f.hours_present} / {f.hours_expected}"
            " (at least one version published at or before the hour starts)"
        )
        self.stdout.write(
            f"  versions per hour:         min {f.versions_per_hour_min}, "
            f"max {f.versions_per_hour_max}, mean {f.versions_per_hour_mean}"
        )
        for missing in f.hours_missing[:MAX_LISTED_GAPS]:
            self.stdout.write(self.style.WARNING(f"  missing forecast: {iso_z(missing)}"))
        self.stdout.write(f"FUELHH ({settings.ENGRID_FUEL_TYPE})")
        self.stdout.write(
            f"  half-hours present:        {a.half_hours_present} / {a.half_hours_expected}"
        )
        self.stdout.write(
            f"  settlement periods:        {a.settlement_period_min} .. {a.settlement_period_max}"
        )
        for missing in a.half_hours_missing[:MAX_LISTED_GAPS]:
            self.stdout.write(self.style.WARNING(f"  missing actual: {iso_z(missing)}"))

        complete = not f.hours_missing and not a.half_hours_missing
        if complete:
            self.stdout.write(self.style.SUCCESS("Coverage complete."))
        elif options["strict"]:
            raise SystemExit(1)
        else:
            self.stdout.write(self.style.WARNING("Coverage has gaps (see above)."))
