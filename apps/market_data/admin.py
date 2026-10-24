from django.contrib import admin

from apps.market_data.models import GenerationActual, IngestionRun, WindForecast


@admin.register(IngestionRun)
class IngestionRunAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("id", "dataset", "source", "status", "started_at", "rows_created")
    list_filter = ("dataset", "source", "status")


@admin.register(WindForecast)
class WindForecastAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("start_time", "publish_time", "generation_mw")
    date_hierarchy = "start_time"


@admin.register(GenerationActual)
class GenerationActualAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = (
        "start_time",
        "settlement_date",
        "settlement_period",
        "fuel_type",
        "generation_mw",
    )
    list_filter = ("fuel_type",)
