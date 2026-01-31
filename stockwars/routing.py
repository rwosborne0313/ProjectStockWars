from __future__ import annotations

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter

import marketdata.routing

application = ProtocolTypeRouter(
    {
        "websocket": AuthMiddlewareStack(URLRouter(marketdata.routing.websocket_urlpatterns)),
    }
)

