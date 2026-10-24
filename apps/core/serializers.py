from enum import Enum
from typing import TYPE_CHECKING, Any

from rest_framework import serializers

if TYPE_CHECKING:
    Serializer = serializers.Serializer[Any]
else:
    Serializer = serializers.Serializer


def enum_choices(enum: type[Enum]) -> list[tuple[str, str]]:
    """(value, NAME) pairs: the same shape drf-spectacular derives from an Enum, so the
    ENUM_NAME_OVERRIDES entries match and every field shares one named schema enum."""
    return [(str(member.value), member.name) for member in enum]


class ProblemSerializer(Serializer):
    """RFC 9457 problem details (application/problem+json)."""

    type = serializers.CharField()
    title = serializers.CharField()
    status = serializers.IntegerField()
    detail = serializers.CharField()
    instance = serializers.CharField(allow_null=True)
    request_id = serializers.CharField()
    # Shadows Serializer.errors only in the class body; DRF moves declared fields aside.
    errors = serializers.DictField(  # type: ignore[assignment]
        required=False, help_text="Field-level validation errors."
    )


class HealthSerializer(Serializer):
    status = serializers.CharField()


class ReadySerializer(Serializer):
    status = serializers.CharField()
    forecast_rows = serializers.IntegerField()
    actual_rows = serializers.IntegerField()
