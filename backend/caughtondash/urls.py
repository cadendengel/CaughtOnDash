"""Root URL configuration for the backend.

Keep this file as the top-level router and include app URL modules below.
"""

from django.contrib import admin
from django.urls import include, path

from apps.incidents.views import incident_photos_view, incident_view
from apps.incidents.worker_views import upload_artifact_view
from caughtondash.health import health

urlpatterns = [
    path('admin/', admin.site.urls),
    # Not under an app prefix: it reports on the deployment, not on a feature.
    path('api/health/', health, name='health'),
    path('api/auth/', include('apps.accounts.urls')),
    # Before the videos include only for readability; the paths do not overlap.
    path('api/videos/<uuid:video_id>/incident/', incident_view, name='video-incident'),
    path('api/videos/<uuid:video_id>/incident/photos/', incident_photos_view, name='video-incident-photos'),
    path('api/videos/worker/jobs/<uuid:job_id>/artifacts/', upload_artifact_view, name='worker-job-artifacts'),
    path('api/videos/', include('apps.videos.urls')),
    path('api/feed/', include('apps.feed.urls')),
]
