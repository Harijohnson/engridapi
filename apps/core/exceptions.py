"""RFC 9457 problem details for every API error.

Validation errors are 422 (the request is well-formed but the parameters are not acceptable).
Unexpected exceptions become a 500 problem with the request id, and are logged with a trace.
"""

import logging
from http import HTTPStatus
from typing import Any

from django.core.exceptions import PermissionDenied
from django.http import Http404
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from apps.core.request_context import request_id_var

logger = logging.getLogger(__name__)

PROBLEM_CONTENT_TYPE = "application/problem+json"
PROBLEM_TYPE_BASE = "https://engrid.local/problems/"


class UnprocessableParameters(exceptions.ValidationError):
    """Query parameters failed validation. Rendered as 422, not DRF's default 400."""

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY


class ServiceUnavailable(exceptions.APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "The service is not ready."
    default_code = "not_ready"


def _problem(
    status_code: int,
    detail: str,
    *,
    problem_type: str,
    instance: str | None,
    errors: Any = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "type": PROBLEM_TYPE_BASE + problem_type,
        "title": HTTPStatus(status_code).phrase,
        "status": status_code,
        "detail": detail,
        "instance": instance,
        "request_id": request_id_var.get(),
    }
    if errors is not None:
        body["errors"] = errors
    return body


def problem_exception_handler(exc: Exception, context: dict[str, Any]) -> Response:
    request = context.get("request")
    instance = request.get_full_path() if request is not None else None

    if isinstance(exc, Http404):
        exc = exceptions.NotFound()
    elif isinstance(exc, PermissionDenied):
        exc = exceptions.PermissionDenied()

    if isinstance(exc, exceptions.ValidationError):
        body = _problem(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "One or more query parameters are invalid.",
            problem_type="invalid-parameters",
            instance=instance,
            errors=exc.detail,
        )
        return Response(
            body, status=status.HTTP_422_UNPROCESSABLE_ENTITY, content_type=PROBLEM_CONTENT_TYPE
        )

    response = drf_exception_handler(exc, context)
    if response is not None:
        detail = exc.detail if isinstance(exc, exceptions.APIException) else str(exc)
        code = exc.get_codes() if isinstance(exc, exceptions.APIException) else "error"
        response.data = _problem(
            response.status_code,
            str(detail),
            problem_type=str(code).replace("_", "-"),
            instance=instance,
        )
        response.content_type = PROBLEM_CONTENT_TYPE
        return response

    logger.exception("Unhandled API error", extra={"path": instance})
    body = _problem(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "An unexpected error occurred. Quote the request id when reporting it.",
        problem_type="internal-error",
        instance=instance,
    )
    return Response(
        body, status=status.HTTP_500_INTERNAL_SERVER_ERROR, content_type=PROBLEM_CONTENT_TYPE
    )
