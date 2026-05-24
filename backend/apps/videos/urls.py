from django.urls import path

from .views import (
    upload_file_view,
    complete_upload_view,
    upload_url_view,
    video_view_count_view,
    video_detail_view,
    video_status_view,
    video_update_delete_view,
)

urlpatterns = [
    # POST /api/videos/upload-url/ - create a presigned upload target for a new video.
    path('upload-url/', upload_url_view, name='video-upload-url'),
    path('upload/', upload_file_view, name='video-upload'),
    # POST /api/videos/complete/ - mark an upload complete and enqueue processing.
    path('complete/', complete_upload_view, name='video-complete-upload'),
    # POST /api/videos/<video_id>/view/ - increment the view count once per play.
    path('<uuid:video_id>/view/', video_view_count_view, name='video-view-count'),
    # GET /api/videos/<video_id>/ - return the full video object and metadata.
    path('<uuid:video_id>/', video_detail_view, name='video-detail'),
    # GET /api/videos/<video_id>/status/ - return processing/upload status.
    path('<uuid:video_id>/status/', video_status_view, name='video-status'),
    # PATCH/DELETE /api/videos/<video_id>/ - update or remove a video.
    path('<uuid:video_id>/manage/', video_update_delete_view, name='video-manage'),
]
