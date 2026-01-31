from __future__ import annotations

from django.urls import re_path

from . import consumers

websocket_urlpatterns = [
    re_path(r"^ws/war-stream/$", consumers.WarStreamConsumer.as_asgi()),
]

