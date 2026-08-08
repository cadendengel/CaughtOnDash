"""WebSocket routes for the videos app."""

from django.urls import path

from apps.videos.consumers import AnalysisStatusConsumer

websocket_urlpatterns = [
    path('ws/analysis/', AnalysisStatusConsumer.as_asgi()),
]
