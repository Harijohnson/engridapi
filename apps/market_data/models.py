"""Persisted Elexon data (PLAN.md §5.4). All datetimes are timezone-aware UTC."""

from django.db import models


class Dataset(models.TextChoices):
    WINDFOR = "WINDFOR", "WINDFOR (wind generation forecast)"
    FUELHH = "FUELHH", "FUELHH (half-hourly generation by fuel type)"


class IngestionSource(models.TextChoices):
    API = "api", "Elexon API"
    SNAPSHOT = "snapshot", "Committed snapshot"


class IngestionStatus(models.TextChoices):
    RUNNING = "running", "Running"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"


class IngestionRun(models.Model):
    """Audit trail of one ingestion of one dataset (feeds the data-status badge)."""

    dataset = models.CharField(max_length=16, choices=Dataset.choices)
    source = models.CharField(max_length=16, choices=IngestionSource.choices)
    request_params = models.JSONField(default=dict)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=16, choices=IngestionStatus.choices, default=IngestionStatus.RUNNING
    )
    rows_received = models.PositiveIntegerField(default=0)
    rows_created = models.PositiveIntegerField(default=0)
    rows_updated = models.PositiveIntegerField(default=0)
    rows_unchanged = models.PositiveIntegerField(default=0)
    rows_rejected = models.PositiveIntegerField(default=0)
    payload_sha256 = models.CharField(max_length=64, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ["-id"]
        indexes = [models.Index(fields=["dataset", "status"])]

    def __str__(self) -> str:
        return f"{self.dataset} {self.source} #{self.pk} ({self.status})"


class WindForecast(models.Model):
    """One WINDFOR forecast version for one delivery hour. Immutable once written."""

    publish_time = models.DateTimeField(db_index=True)
    start_time = models.DateTimeField()
    generation_mw = models.IntegerField()
    ingestion_run = models.ForeignKey(
        IngestionRun, on_delete=models.PROTECT, related_name="forecasts"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["start_time", "publish_time"], name="uniq_windforecast_start_publish"
            ),
            models.CheckConstraint(
                condition=models.Q(generation_mw__gte=0), name="windforecast_generation_nonneg"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.start_time:%Y-%m-%dT%H:%MZ} @ {self.publish_time:%Y-%m-%dT%H:%MZ}"


class GenerationActual(models.Model):
    """One FUELHH half-hour of actual generation for one fuel type."""

    start_time = models.DateTimeField()
    settlement_date = models.DateField()
    settlement_period = models.SmallIntegerField()
    fuel_type = models.CharField(max_length=32)
    generation_mw = models.IntegerField()
    publish_time = models.DateTimeField()
    ingestion_run = models.ForeignKey(
        IngestionRun, on_delete=models.PROTECT, related_name="actuals"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["fuel_type", "start_time"], name="uniq_generationactual_fuel_start"
            ),
            models.CheckConstraint(
                condition=models.Q(settlement_period__gte=1, settlement_period__lte=50),
                name="generationactual_settlement_period_range",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.fuel_type} {self.start_time:%Y-%m-%dT%H:%MZ}"
