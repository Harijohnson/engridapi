"""Liveness, readiness and the JSON fallbacks for non-API 404/500 (all DRF)."""

from typing import Any

from django.conf import settings
from django.db import DatabaseError
from drf_spectacular.utils import extend_schema
from rest_framework import exceptions
from rest_framework.decorators import api_view, renderer_classes, throttle_classes
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.exceptions import ServiceUnavailable
from apps.core.serializers import HealthSerializer, ProblemSerializer, ReadySerializer
from apps.market_data.services import readiness


class HealthView(APIView):
    """Liveness: the process is up. Never touches the database."""

    throttle_classes = ()

    @extend_schema(responses={200: HealthSerializer}, tags=["ops"])
    def get(self, request: Request) -> Response:
        return Response(HealthSerializer({"status": "ok"}).data)


class ReadyView(APIView):
    """Readiness: the database is reachable and both datasets are present."""

    throttle_classes = ()

    @extend_schema(responses={200: ReadySerializer, 503: ProblemSerializer}, tags=["ops"])
    def get(self, request: Request) -> Response:
        try:
            state = readiness(settings.ENGRID_FUEL_TYPE)
        except DatabaseError as exc:
            raise ServiceUnavailable("The database is not reachable.") from exc
        if not state.ready:
            raise ServiceUnavailable("No forecast or actual data has been loaded yet.")
        return Response(
            ReadySerializer(
                {
                    "status": "ready",
                    "forecast_rows": state.forecast_rows,
                    "actual_rows": state.actual_rows,
                }
            ).data
        )


_ALL_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]


@extend_schema(exclude=True)
@api_view(_ALL_METHODS)
@renderer_classes([JSONRenderer])
@throttle_classes([])
def not_found(request: Request, /, exception: Exception | None = None, **_: Any) -> Response:
    raise exceptions.NotFound("No such endpoint.")


@extend_schema(exclude=True)
@api_view(_ALL_METHODS)
@renderer_classes([JSONRenderer])
@throttle_classes([])
def server_error(request: Request, /, **_: Any) -> Response:
    raise exceptions.APIException("An unexpected error occurred.")
