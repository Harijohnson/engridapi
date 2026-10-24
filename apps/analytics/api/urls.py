from django.urls import path

from apps.analytics.api import views

app_name = "analytics"

urlpatterns = [
    path("meta", views.MetaView.as_view(), name="meta"),
    path("summary", views.SummaryView.as_view(), name="summary"),
    path("timeseries", views.TimeseriesView.as_view(), name="timeseries"),
    path("intervals", views.IntervalListView.as_view(), name="intervals"),
    path("accuracy/breakdown", views.BreakdownView.as_view(), name="accuracy-breakdown"),
    path("accuracy/by-lead-time", views.LeadTimeCurveView.as_view(), name="accuracy-by-lead-time"),
    path("forecasts/history", views.ForecastHistoryView.as_view(), name="forecast-history"),
]
