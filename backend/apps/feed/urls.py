from django.urls import path

from .views import feed_view

urlpatterns = [
    # GET /api/feed/ - return the paginated community feed.
    path('', feed_view, name='feed'),
]
