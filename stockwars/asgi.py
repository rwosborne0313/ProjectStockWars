"""
ASGI config for stockwars project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/asgi/
"""

import os

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

import marketdata.routing

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'stockwars.settings')

django_asgi_app = get_asgi_application()

# Serve HTTP via Django, and WebSockets via Channels routing.
application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": AuthMiddlewareStack(URLRouter(marketdata.routing.websocket_urlpatterns)),
    }
)
