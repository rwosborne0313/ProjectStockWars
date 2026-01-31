from django.urls import path

from . import views

app_name = "marketdata"

urlpatterns = [
    path("war-stream/", views.war_stream, name="war_stream"),
]

