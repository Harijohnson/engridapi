"""Routes: /health, /ready, /api/v1/..., OpenAPI schema + docs. Admin only when DEBUG (D17)."""

from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from apps.core.views import HealthView, ReadyView

urlpatterns = [
    path("health", HealthView.as_view(), name="health"),
    path("ready", ReadyView.as_view(), name="ready"),
    path("api/v1/", include("apps.analytics.api.urls")),
    path("api/schema", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs", SpectacularSwaggerView.as_view(url_name="schema"), name="docs"),
]

if settings.DEBUG:
    urlpatterns.append(path("admin/", admin.site.urls))

handler404 = "apps.core.views.not_found"
handler500 = "apps.core.views.server_error"
