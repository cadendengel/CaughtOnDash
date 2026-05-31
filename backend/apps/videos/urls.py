from django.urls import path

from .views import (
    upload_file_view,
    complete_upload_view,
    upload_url_view,
    video_view_count_view,
    video_like_view,
    video_comments_view,
    video_comment_like_view,
    video_detail_view,
    video_analysis_view,
    video_reanalyze_view,
    video_status_view,
    video_update_delete_view,
    admin_video_delete_view,
    admin_comment_delete_view,
    admin_video_tags_view,
    search_videos,
)

urlpatterns = [
    # POST /api/videos/upload-url/ - create a presigned upload target for a new video.
    path('upload-url/', upload_url_view, name='video-upload-url'),
    path('upload/', upload_file_view, name='video-upload'),
    # GET /api/videos/search/?q=... - search videos (Postgres FT when available)
    path('search/', search_videos, name='video-search'),
    # POST /api/videos/complete/ - mark an upload complete and enqueue processing.
    path('complete/', complete_upload_view, name='video-complete-upload'),
    # POST /api/videos/<video_id>/view/ - increment the view count once per play.
    path('<uuid:video_id>/view/', video_view_count_view, name='video-view-count'),
    # GET/POST /api/videos/<video_id>/comments/ - list or add comments.
    path('<uuid:video_id>/comments/', video_comments_view, name='video-comments'),
    # POST /api/videos/comments/<comment_id>/like/ - toggle comment likes.
    path('comments/<uuid:comment_id>/like/', video_comment_like_view, name='video-comment-like'),
    # DELETE /api/admin/videos/<video_id>/ - permanently delete a video.
    path('admin/videos/<uuid:video_id>/', admin_video_delete_view, name='admin-video-delete'),
    # DELETE /api/admin/comments/<comment_id>/ - permanently delete a comment or reply.
    path('admin/comments/<uuid:comment_id>/', admin_comment_delete_view, name='admin-comment-delete'),
    # PATCH /api/videos/admin/videos/<video_id>/tags/ - update a video's tags.
    path('admin/videos/<uuid:video_id>/tags/', admin_video_tags_view, name='admin-video-tags'),
    # POST /api/videos/<video_id>/like/ - toggle a like for the current user.
    path('<uuid:video_id>/like/', video_like_view, name='video-like'),
    # GET /api/videos/<video_id>/ - return the full video object and metadata.
    path('<uuid:video_id>/', video_detail_view, name='video-detail'),
    path('<uuid:video_id>/analysis/', video_analysis_view, name='video-analysis'),
    path('<uuid:video_id>/analysis/reanalyze/', video_reanalyze_view, name='video-reanalyze'),
    # GET /api/videos/<video_id>/status/ - return processing/upload status.
    path('<uuid:video_id>/status/', video_status_view, name='video-status'),
    # PATCH/DELETE /api/videos/<video_id>/ - update or remove a video.
    path('<uuid:video_id>/manage/', video_update_delete_view, name='video-manage'),
]
