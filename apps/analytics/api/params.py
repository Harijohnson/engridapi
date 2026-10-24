"""Query-parameter validation (DRF serializers). Bad input raises a 422 problem response.

Rules (PLAN.md §5.6): dates are UK delivery dates inside the evaluation window, date_from <=
date_to; policy parameters must match the chosen policy (gate params with daily_gate only,
lead_time_h with lead_time only); unknown parameters are rejected rather than ignored.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Any

from django.conf import settings
from rest_framework import serializers

from apps.analytics.domain.cutoff import (
    CutoffPolicy,
    CutoffPolicyKind,
    DailyGatePolicy,
    LeadTimePolicy,
)
from apps.analytics.domain.metrics import GroupBy
from apps.analytics.services import (
    INTERVAL_ORDERING_FIELDS,
    AnalyticsQuery,
    HistoryQuery,
    LeadTimeCurveQuery,
)
from apps.core.exceptions import UnprocessableParameters
from apps.core.serializers import Serializer, enum_choices
from apps.core.time import delivery_window_utc

ORDERING_CHOICES = [f for field in INTERVAL_ORDERING_FIELDS for f in (field, f"-{field}")]
GATE_PARAMS = ("gate_time", "gate_days_before")
# One month is at most 31 x 25 = 775 delivery hours (October clock change), so one page fits all.
MAX_PAGE_SIZE = 800
DEFAULT_PAGE_SIZE = 50


class StrictParams(Serializer):
    """Base: rejects unknown query parameters (a typo must not silently fall back to defaults)."""

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        unknown = sorted(set(self.initial_data) - set(self.fields))
        if unknown:
            raise serializers.ValidationError(
                {name: ["Unknown query parameter."] for name in unknown}
            )
        return attrs

    @classmethod
    def parse(cls, query_params: Any) -> dict[str, Any]:
        serializer = cls(data={key: query_params.get(key) for key in query_params})
        if not serializer.is_valid():
            raise UnprocessableParameters(serializer.errors)
        return dict(serializer.validated_data)


class DateRangeParams(StrictParams):
    date_from = serializers.DateField(
        required=False, help_text="First UK delivery date (inclusive). Defaults to window start."
    )
    date_to = serializers.DateField(
        required=False, help_text="Last UK delivery date (inclusive). Defaults to window end."
    )

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        attrs = super().validate(attrs)
        window_from: date = settings.ENGRID_EVALUATION_DATE_FROM
        window_to: date = settings.ENGRID_EVALUATION_DATE_TO
        attrs.setdefault("date_from", window_from)
        attrs.setdefault("date_to", window_to)
        errors: dict[str, list[str]] = {}
        for name in ("date_from", "date_to"):
            if not window_from <= attrs[name] <= window_to:
                errors[name] = [f"Must be within the evaluation window {window_from}..{window_to}."]
        if not errors and attrs["date_from"] > attrs["date_to"]:
            errors["date_to"] = ["Must be on or after date_from."]
        if errors:
            raise serializers.ValidationError(errors)
        return attrs


class PolicyFields(Serializer):
    policy = serializers.ChoiceField(
        choices=enum_choices(CutoffPolicyKind),
        required=False,
        help_text="Decision cutoff policy. Defaults to the server default (see /api/v1/meta).",
    )
    gate_time = serializers.TimeField(
        required=False,
        format="%H:%M",
        input_formats=["%H:%M"],
        help_text="daily_gate only: UK wall-clock time (HH:MM, Europe/London).",
    )
    gate_days_before = serializers.IntegerField(
        required=False, min_value=1, help_text="daily_gate only: days before the delivery date."
    )
    lead_time_h = serializers.IntegerField(
        required=False, min_value=0, help_text="lead_time only: required lead time in hours."
    )


def build_policy(attrs: dict[str, Any]) -> CutoffPolicy:
    """Turn validated policy fields into a domain policy, applying the D1 defaults."""
    kind = CutoffPolicyKind(attrs.get("policy") or settings.ENGRID_DEFAULT_POLICY)
    errors: dict[str, list[str]] = {}
    if kind == CutoffPolicyKind.DAILY_GATE:
        if "lead_time_h" in attrs:
            errors["lead_time_h"] = ["Only valid with policy=lead_time."]
        days_before = attrs.get("gate_days_before", settings.ENGRID_DEFAULT_GATE_DAYS_BEFORE)
        days_max: int = settings.ENGRID_GATE_DAYS_BEFORE_MAX
        if days_before > days_max:
            errors["gate_days_before"] = [f"Ensure this value is less than or equal to {days_max}."]
        if errors:
            raise serializers.ValidationError(errors)
        gate_time: time = attrs.get("gate_time") or time.fromisoformat(
            settings.ENGRID_DEFAULT_GATE_TIME
        )
        return DailyGatePolicy(gate_time=gate_time, days_before=days_before)

    for name in GATE_PARAMS:
        if name in attrs:
            errors[name] = ["Only valid with policy=daily_gate."]
    lead_time_h = attrs.get("lead_time_h", settings.ENGRID_DEFAULT_LEAD_TIME_H)
    if lead_time_h > settings.ENGRID_LEAD_TIME_H_MAX:
        errors["lead_time_h"] = [
            f"Ensure this value is less than or equal to {settings.ENGRID_LEAD_TIME_H_MAX}."
        ]
    if errors:
        raise serializers.ValidationError(errors)
    return LeadTimePolicy(lead_time_h=lead_time_h)


class AnalyticsParams(DateRangeParams, PolicyFields):
    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        attrs = super().validate(attrs)
        attrs["query"] = AnalyticsQuery(
            date_from=attrs["date_from"], date_to=attrs["date_to"], policy=build_policy(attrs)
        )
        return attrs


class IntervalListParams(AnalyticsParams):
    page = serializers.IntegerField(min_value=1, default=1)
    page_size = serializers.IntegerField(
        min_value=1, max_value=MAX_PAGE_SIZE, default=DEFAULT_PAGE_SIZE
    )
    ordering = serializers.ChoiceField(
        choices=ORDERING_CHOICES,
        default="start_time",
        help_text="Sort field; prefix with '-' for descending. Missing values sort last.",
    )


class BreakdownParams(AnalyticsParams):
    group_by = serializers.ChoiceField(
        choices=enum_choices(GroupBy), help_text="UK-local day or hour of day."
    )


class LeadTimeCurveParams(DateRangeParams):
    min_h = serializers.IntegerField(min_value=0, default=0)
    max_h = serializers.IntegerField(required=False, min_value=0)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        attrs = super().validate(attrs)
        limit: int = settings.ENGRID_LEAD_TIME_H_MAX
        attrs.setdefault("max_h", limit)
        errors: dict[str, list[str]] = {}
        for name in ("min_h", "max_h"):
            if attrs[name] > limit:
                errors[name] = [f"Ensure this value is less than or equal to {limit}."]
        if not errors and attrs["min_h"] > attrs["max_h"]:
            errors["max_h"] = ["Must be greater than or equal to min_h."]
        if errors:
            raise serializers.ValidationError(errors)
        attrs["query"] = LeadTimeCurveQuery(
            date_from=attrs["date_from"],
            date_to=attrs["date_to"],
            min_h=attrs["min_h"],
            max_h=attrs["max_h"],
        )
        return attrs


class HistoryParams(StrictParams, PolicyFields):
    start_time = serializers.DateTimeField(
        help_text="Delivery hour start, ISO-8601 with a time zone (e.g. 2026-01-15T18:00:00Z)."
    )

    def validate_start_time(self, value: datetime) -> datetime:
        # DRF makes naive input aware in the default zone; R7 wants it rejected instead.
        try:
            raw = datetime.fromisoformat(str(self.initial_data.get("start_time")))
        except ValueError as exc:
            raise serializers.ValidationError("Must be ISO-8601.") from exc
        if raw.tzinfo is None:
            raise serializers.ValidationError("Must include a time zone (use Z for UTC).")
        value = value.astimezone(UTC)
        if value.minute or value.second or value.microsecond:
            raise serializers.ValidationError("Must be on the hour (forecasts are hourly).")
        window_start, window_end = delivery_window_utc(
            settings.ENGRID_EVALUATION_DATE_FROM, settings.ENGRID_EVALUATION_DATE_TO
        )
        if not window_start <= value < window_end:
            raise serializers.ValidationError("Must be within the evaluation window.")
        return value

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        attrs = super().validate(attrs)
        attrs["query"] = HistoryQuery(start_time=attrs["start_time"], policy=build_policy(attrs))
        return attrs
