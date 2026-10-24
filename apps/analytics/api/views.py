"""Thin DRF views: validate params -> call one service -> serialise.

HTTP caching (D16): every response carries Cache-Control and a weak ETag derived from the
normalised query and data_version; a matching If-None-Match gets 304 without recomputation.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

from django.conf import settings
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.pagination import PageNumberPagination
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.analytics import services
from apps.analytics.api import params as p
from apps.analytics.api import serializers as s
from apps.analytics.domain.metrics import GroupBy
from apps.core.exceptions import UnprocessableParameters
from apps.core.serializers import ProblemSerializer, Serializer

INVALID = OpenApiResponse(ProblemSerializer, description="Invalid query parameters (RFC 9457).")
ANALYTICS_TAG = ["analytics"]


def _etag(path: str, normalised: dict[str, Any], version: int) -> str:
    digest = hashlib.sha1(
        json.dumps({"path": path, **normalised}, sort_keys=True, default=str).encode(),
        usedforsecurity=False,
    ).hexdigest()[:20]
    return f'W/"v{version}-{digest}"'


class AnalyticsView(APIView):
    def respond(
        self,
        request: Request,
        normalised: dict[str, Any],
        build: Callable[[int], Any],
        serializer_class: type[Serializer],
    ) -> Response:
        version = services.current_data_version()
        etag = _etag(request.path, normalised, version)
        headers = {
            "ETag": etag,
            "Cache-Control": (
                f"public, max-age={settings.ENGRID_HTTP_MAX_AGE_S}, "
                f"s-maxage={settings.ENGRID_HTTP_S_MAXAGE_S}"
            ),
        }
        if etag in request.headers.get("If-None-Match", ""):
            return Response(status=status.HTTP_304_NOT_MODIFIED, headers=headers)
        payload = build(version)
        return Response(serializer_class(payload).data, headers=headers)


class MetaView(AnalyticsView):
    @extend_schema(
        operation_id="meta",
        summary="Evaluation window, policy defaults and bounds, coverage, ingestion status",
        responses={200: s.MetaResponseSerializer},
        tags=ANALYTICS_TAG,
    )
    def get(self, request: Request) -> Response:
        p.StrictParams.parse(request.query_params)
        return self.respond(request, {}, services.meta, s.MetaResponseSerializer)


class SummaryView(AnalyticsView):
    @extend_schema(
        operation_id="summary",
        summary="KPI block: MAE, RMSE, bias and the other accuracy metrics",
        parameters=[p.AnalyticsParams],
        responses={200: s.SummaryResponseSerializer, 422: INVALID},
        tags=ANALYTICS_TAG,
    )
    def get(self, request: Request) -> Response:
        query = p.AnalyticsParams.parse(request.query_params)["query"]
        return self.respond(
            request,
            query.describe(),
            lambda version: services.summary(query, version),
            s.SummaryResponseSerializer,
        )


class TimeseriesView(AnalyticsView):
    @extend_schema(
        operation_id="timeseries",
        summary="Selected hourly forecast and half-hourly actuals for the chart",
        parameters=[p.AnalyticsParams],
        responses={200: s.TimeseriesResponseSerializer, 422: INVALID},
        tags=ANALYTICS_TAG,
    )
    def get(self, request: Request) -> Response:
        query = p.AnalyticsParams.parse(request.query_params)["query"]
        return self.respond(
            request,
            query.describe(),
            lambda version: services.timeseries(query, version),
            s.TimeseriesResponseSerializer,
        )


class IntervalPagination(PageNumberPagination):
    """Page-number pagination. Params are validated by IntervalListParams beforehand; links are
    page numbers (not absolute URLs) because clients reach us through a proxy."""

    page_size = p.DEFAULT_PAGE_SIZE
    page_size_query_param = "page_size"
    max_page_size = p.MAX_PAGE_SIZE

    def paginate_queryset(self, queryset: Any, request: Request, view: Any = None) -> Any:
        try:
            return super().paginate_queryset(queryset, request, view)
        except NotFound as exc:
            raise UnprocessableParameters({"page": [str(exc.detail)]}) from exc


class IntervalListView(AnalyticsView):
    @extend_schema(
        operation_id="intervals",
        summary="Interval table: one row per delivery hour, sortable and paginated",
        parameters=[p.IntervalListParams],
        responses={200: s.IntervalPageSerializer, 422: INVALID},
        tags=ANALYTICS_TAG,
    )
    def get(self, request: Request) -> Response:
        attrs = p.IntervalListParams.parse(request.query_params)
        query, ordering = attrs["query"], attrs["ordering"]
        page_number, page_size = attrs["page"], attrs["page_size"]

        def build(version: int) -> dict[str, Any]:
            result = services.intervals(query, version, ordering)
            paginator = IntervalPagination()
            rows = paginator.paginate_queryset(result.rows, request, view=self)
            page = paginator.page
            assert page is not None  # set by paginate_queryset
            return {
                "query": result.query,
                "data_version": result.data_version,
                "ordering": result.ordering,
                "count": page.paginator.count,
                "page": page.number,
                "page_size": page_size,
                "num_pages": page.paginator.num_pages,
                "next": page.next_page_number() if page.has_next() else None,
                "previous": page.previous_page_number() if page.has_previous() else None,
                "results": rows,
            }

        normalised = {
            **query.describe(),
            "ordering": ordering,
            "page": page_number,
            "page_size": page_size,
        }
        return self.respond(request, normalised, build, s.IntervalPageSerializer)


class BreakdownView(AnalyticsView):
    @extend_schema(
        operation_id="accuracyBreakdown",
        summary="Accuracy metrics per UK day or hour of day",
        parameters=[p.BreakdownParams],
        responses={200: s.BreakdownResponseSerializer, 422: INVALID},
        tags=ANALYTICS_TAG,
    )
    def get(self, request: Request) -> Response:
        attrs = p.BreakdownParams.parse(request.query_params)
        query, group_by = attrs["query"], GroupBy(attrs["group_by"])
        return self.respond(
            request,
            {**query.describe(), "group_by": group_by.value},
            lambda version: services.accuracy_breakdown(query, version, group_by),
            s.BreakdownResponseSerializer,
        )


class LeadTimeCurveView(AnalyticsView):
    @extend_schema(
        operation_id="accuracyByLeadTime",
        summary="Accuracy for each required lead time H (the improvement curve)",
        parameters=[p.LeadTimeCurveParams],
        responses={200: s.LeadTimeCurveResponseSerializer, 422: INVALID},
        tags=ANALYTICS_TAG,
    )
    def get(self, request: Request) -> Response:
        query = p.LeadTimeCurveParams.parse(request.query_params)["query"]
        return self.respond(
            request,
            query.describe(),
            lambda version: services.accuracy_by_lead_time(query, version),
            s.LeadTimeCurveResponseSerializer,
        )


class ForecastHistoryView(AnalyticsView):
    @extend_schema(
        operation_id="forecastHistory",
        summary="Every forecast version for one delivery hour, labelled against the cutoff",
        parameters=[p.HistoryParams],
        responses={200: s.HistoryResponseSerializer, 422: INVALID},
        tags=ANALYTICS_TAG,
    )
    def get(self, request: Request) -> Response:
        query = p.HistoryParams.parse(request.query_params)["query"]
        return self.respond(
            request,
            query.describe(),
            lambda version: services.forecast_history(query, version),
            s.HistoryResponseSerializer,
        )
