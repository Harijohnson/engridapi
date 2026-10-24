"""Response serializers. They read the service result dataclasses and define the OpenAPI contract
the frontend generates its types from. No logic here: only shape and documentation."""

from rest_framework import serializers

from apps.analytics.domain.cutoff import CutoffPolicyKind
from apps.analytics.domain.history import VersionStatus
from apps.analytics.domain.matching import IntervalStatus
from apps.analytics.domain.metrics import GroupBy
from apps.core.serializers import Serializer, enum_choices

POLICY_CHOICES = enum_choices(CutoffPolicyKind)


class QueryEchoSerializer(Serializer):
    date_from = serializers.DateField()
    date_to = serializers.DateField()
    timezone = serializers.CharField(help_text="Zone of the delivery dates and gate time.")
    policy = serializers.ChoiceField(choices=POLICY_CHOICES)
    gate_time = serializers.CharField(allow_null=True, help_text="HH:MM Europe/London.")
    gate_days_before = serializers.IntegerField(allow_null=True)
    lead_time_h = serializers.IntegerField(allow_null=True)


class MetricSummarySerializer(Serializer):
    n_expected = serializers.IntegerField(help_text="Delivery hours in scope.")
    n_matched = serializers.IntegerField(help_text="Hours with both a forecast and an actual.")
    n_missing_forecast = serializers.IntegerField()
    n_missing_actual = serializers.IntegerField()
    coverage_pct = serializers.FloatField(allow_null=True)
    mae_mw = serializers.FloatField(allow_null=True)
    rmse_mw = serializers.FloatField(allow_null=True)
    bias_mw = serializers.FloatField(
        allow_null=True, help_text="mean(forecast - actual); positive = over-forecast."
    )
    median_abs_error_mw = serializers.FloatField(allow_null=True)
    p90_abs_error_mw = serializers.FloatField(allow_null=True)
    max_abs_error_mw = serializers.FloatField(allow_null=True)
    mean_forecast_mw = serializers.FloatField(allow_null=True)
    mean_actual_mw = serializers.FloatField(allow_null=True)


class SummaryResponseSerializer(Serializer):
    query = QueryEchoSerializer()
    data_version = serializers.IntegerField()
    matching_method = serializers.CharField()
    metrics = MetricSummarySerializer()


class ForecastPointSerializer(Serializer):
    start_time = serializers.DateTimeField()
    cutoff_time = serializers.DateTimeField()
    publish_time = serializers.DateTimeField(allow_null=True)
    forecast_mw = serializers.IntegerField(allow_null=True)
    lead_time_h = serializers.FloatField(allow_null=True)
    actual_mw = serializers.IntegerField(
        allow_null=True, help_text="Matched actual: the half-hour starting at start_time (D2)."
    )
    error_mw = serializers.IntegerField(allow_null=True, help_text="forecast - actual.")
    status = serializers.ChoiceField(choices=enum_choices(IntervalStatus))


class ActualPointSerializer(Serializer):
    start_time = serializers.DateTimeField()
    actual_mw = serializers.IntegerField(allow_null=True)
    settlement_date = serializers.DateField(allow_null=True)
    settlement_period = serializers.IntegerField(allow_null=True)


class TimeseriesResponseSerializer(Serializer):
    query = QueryEchoSerializer()
    data_version = serializers.IntegerField()
    forecast = ForecastPointSerializer(many=True, help_text="Hourly selected forecast.")
    actual = ActualPointSerializer(many=True, help_text="Half-hourly actuals (null = missing).")


class IntervalSerializer(Serializer):
    start_time = serializers.DateTimeField()
    forecast_mw = serializers.IntegerField(allow_null=True)
    actual_mw = serializers.IntegerField(allow_null=True)
    error_mw = serializers.IntegerField(allow_null=True, help_text="forecast - actual.")
    abs_error_mw = serializers.IntegerField(allow_null=True)
    publish_time = serializers.DateTimeField(allow_null=True)
    lead_time_h = serializers.FloatField(allow_null=True)
    cutoff_time = serializers.DateTimeField()
    status = serializers.ChoiceField(choices=enum_choices(IntervalStatus))


class IntervalPageSerializer(Serializer):
    query = QueryEchoSerializer()
    data_version = serializers.IntegerField()
    ordering = serializers.CharField()
    count = serializers.IntegerField()
    page = serializers.IntegerField()
    page_size = serializers.IntegerField()
    num_pages = serializers.IntegerField()
    next = serializers.IntegerField(allow_null=True, help_text="Next page number.")
    previous = serializers.IntegerField(allow_null=True, help_text="Previous page number.")
    results = IntervalSerializer(many=True)


class BreakdownGroupSerializer(Serializer):
    key = serializers.CharField(help_text="UK date (YYYY-MM-DD) or hour of day (0-23).")
    metrics = MetricSummarySerializer()


class BreakdownResponseSerializer(Serializer):
    query = QueryEchoSerializer()
    data_version = serializers.IntegerField()
    group_by = serializers.ChoiceField(choices=enum_choices(GroupBy))
    groups = BreakdownGroupSerializer(many=True)


class LeadTimeQuerySerializer(Serializer):
    date_from = serializers.DateField()
    date_to = serializers.DateField()
    timezone = serializers.CharField()
    min_h = serializers.IntegerField()
    max_h = serializers.IntegerField()


class LeadTimePointSerializer(Serializer):
    lead_time_h = serializers.IntegerField()
    metrics = MetricSummarySerializer()


class LeadTimeCurveResponseSerializer(Serializer):
    query = LeadTimeQuerySerializer()
    data_version = serializers.IntegerField()
    points = LeadTimePointSerializer(many=True)


class HistoryQuerySerializer(Serializer):
    start_time = serializers.DateTimeField()
    policy = serializers.ChoiceField(choices=POLICY_CHOICES)
    gate_time = serializers.CharField(allow_null=True)
    gate_days_before = serializers.IntegerField(allow_null=True)
    lead_time_h = serializers.IntegerField(allow_null=True)


class HistoryVersionSerializer(Serializer):
    publish_time = serializers.DateTimeField()
    forecast_mw = serializers.IntegerField()
    lead_time_h = serializers.FloatField(help_text="start_time - publish_time; negative = late.")
    status = serializers.ChoiceField(choices=enum_choices(VersionStatus))


class HistoryResponseSerializer(Serializer):
    query = HistoryQuerySerializer()
    data_version = serializers.IntegerField()
    start_time = serializers.DateTimeField()
    cutoff_time = serializers.DateTimeField()
    actual_mw = serializers.IntegerField(allow_null=True)
    selected_publish_time = serializers.DateTimeField(allow_null=True)
    selected_forecast_mw = serializers.IntegerField(allow_null=True)
    selected_error_mw = serializers.IntegerField(allow_null=True)
    selected_lead_time_h = serializers.FloatField(allow_null=True)
    versions = HistoryVersionSerializer(many=True)


# --- meta ------------------------------------------------------------------------------------
class EvaluationWindowSerializer(Serializer):
    date_from = serializers.DateField()
    date_to = serializers.DateField()
    timezone = serializers.CharField()


class DailyGateBoundsSerializer(Serializer):
    gate_time_default = serializers.CharField()
    gate_days_before_default = serializers.IntegerField()
    gate_days_before_min = serializers.IntegerField()
    gate_days_before_max = serializers.IntegerField()


class LeadTimeBoundsSerializer(Serializer):
    lead_time_h_default = serializers.IntegerField()
    lead_time_h_min = serializers.IntegerField()
    lead_time_h_max = serializers.IntegerField()


class PoliciesSerializer(Serializer):
    default = serializers.ChoiceField(choices=POLICY_CHOICES)
    available = serializers.ListField(child=serializers.ChoiceField(choices=POLICY_CHOICES))
    daily_gate = DailyGateBoundsSerializer()
    lead_time = LeadTimeBoundsSerializer()


class MatchingSerializer(Serializer):
    method = serializers.CharField()
    description = serializers.CharField()


class ForecastCoverageSerializer(Serializer):
    expected = serializers.IntegerField()
    present = serializers.IntegerField()
    missing = serializers.IntegerField()
    rows = serializers.IntegerField()
    distinct_publishes = serializers.IntegerField()
    versions_per_hour_min = serializers.IntegerField(allow_null=True)
    versions_per_hour_max = serializers.IntegerField(allow_null=True)
    versions_per_hour_mean = serializers.FloatField(allow_null=True)


class ActualCoverageSerializer(Serializer):
    expected = serializers.IntegerField()
    present = serializers.IntegerField()
    missing = serializers.IntegerField()


class CoverageSerializer(Serializer):
    forecast = ForecastCoverageSerializer()
    actual = ActualCoverageSerializer()


class IngestionInfoSerializer(Serializer):
    dataset = serializers.CharField()
    # A declared field named like Field.source; DRF moves declared fields out of the class.
    source = serializers.CharField()  # type: ignore[assignment]
    run_id = serializers.IntegerField()
    status = serializers.CharField()
    started_at = serializers.DateTimeField()
    finished_at = serializers.DateTimeField(allow_null=True)
    rows_received = serializers.IntegerField()
    rows_created = serializers.IntegerField()
    rows_rejected = serializers.IntegerField()


class DataSourceSerializer(Serializer):
    dataset = serializers.CharField()
    name = serializers.CharField()
    url = serializers.URLField()
    description = serializers.CharField()


class MetaResponseSerializer(Serializer):
    data_version = serializers.IntegerField()
    evaluation_window = EvaluationWindowSerializer()
    policies = PoliciesSerializer()
    matching = MatchingSerializer()
    coverage = CoverageSerializer()
    ingestion = IngestionInfoSerializer(many=True)
    timezone_note = serializers.CharField()
    data_sources = DataSourceSerializer(many=True)
    limitations = serializers.ListField(child=serializers.CharField())
