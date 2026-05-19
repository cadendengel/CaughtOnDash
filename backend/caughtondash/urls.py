"""Root URL configuration for the backend.

Keep this file as the top-level router and include app URL modules below.
"""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/auth/', include('apps.accounts.urls')),
    path('api/videos/', include('apps.videos.urls')),
    path('api/feed/', include('apps.feed.urls')),
]
