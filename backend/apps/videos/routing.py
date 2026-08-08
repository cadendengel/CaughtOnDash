"""WebSocket routes for the videos app.

Two endpoints with deliberately different rules: /ws/analysis/ is anonymous and
read-only for browsers, /ws/worker/ is bearer-authenticated and accepts writes
from the desktop worker. Keeping them separate means no misconfiguration can
let a browser reach the worker channel.
"""

from django.urls import path

from apps.videos.consumers import AnalysisStatusConsumer, WorkerHeartbeatConsumer

websocket_urlpatterns = [
    path('ws/analysis/', AnalysisStatusConsumer.as_asgi()),
    path('ws/worker/', WorkerHeartbeatConsumer.as_asgi()),
]
