from django.urls import path

from . import views

app_name = "simulator"

urlpatterns = [
    path("dashboard/", views.dashboard, name="dashboard"),
    path("watchlist/", views.watchlist, name="watchlist"),
    path("watchlist/timeseries/", views.watchlist_timeseries, name="watchlist_timeseries"),
    path(
        "competitions/<int:competition_id>/metrics/ohlc/",
        views.competition_metrics_ohlc,
        name="competition_metrics_ohlc",
    ),
    path(
        "competitions/<int:competition_id>/dashboard/",
        views.dashboard_for_competition,
        name="dashboard_for_competition",
    ),
]

