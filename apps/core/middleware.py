"""Request-id middleware: every log line and every response carries the same id."""

import re
import uuid
from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

from apps.core.request_context import request_id_var

REQUEST_ID_HEADER = "X-Request-ID"
# Accept an upstream id only if it is short and harmless; otherwise mint a new one.
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


class RequestIdMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        incoming = request.headers.get(REQUEST_ID_HEADER, "")
        request_id = incoming if _SAFE_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        try:
            response = self.get_response(request)
        finally:
            request_id_var.reset(token)
        response[REQUEST_ID_HEADER] = request_id
        return response
