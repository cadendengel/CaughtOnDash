"""ASGI config for caughtondash.

HTTP is still handled by Django exactly as before; WebSocket connections are
routed to Channels consumers. Serving this needs an ASGI server --
daphne -- not gunicorn's default WSGI worker. Under WSGI the HTTP side keeps
working and WebSocket connections simply fail to open, which the frontend
treats as "no live updates" rather than an error.
"""

import os

from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'caughtondash.settings')

# Must be built before importing anything that touches models.
django_asgi_application = get_asgi_application()

from apps.videos.routing import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter({
    'http': django_asgi_application,
    # No AuthMiddlewareStack: the stream is read-only and carries a subset of
    # what the public feed already returns, so there is nothing to authorise.
    'websocket': URLRouter(websocket_urlpatterns),
})
