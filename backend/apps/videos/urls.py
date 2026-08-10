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
    video_status_view,
    video_update_delete_view,
    video_request_analysis_view,
    video_approval_view,
    admin_video_delete_view,
    admin_comment_delete_view,
    admin_video_tags_view,
    search_videos,
)
from .worker_views import (
    list_queue,
    list_review_queue,
    reorder_queue_view,
    worker_decide_approval,
    worker_status,
    worker_heartbeat,
    get_next_job,
    claim_job_view,
    update_job_progress_view,
    complete_job_view,
    fail_job_view,
    cancel_job_view,
    admin_retry_job_view,
    requeue_stale_version_view,
    moderation_overview,
    reset_stale_jobs_view,
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
    # POST /api/videos/<video_id>/analyze/ - owner requests re-analysis (needs re-approval).
    path('<uuid:video_id>/analyze/', video_request_analysis_view, name='video-request-analysis'),
    # POST /api/videos/<video_id>/approval/ - approve or reject for analysis.
    path('<uuid:video_id>/approval/', video_approval_view, name='video-approval'),
    # POST /api/videos/<video_id>/like/ - toggle a like for the current user.
    path('<uuid:video_id>/like/', video_like_view, name='video-like'),
    # GET /api/videos/<video_id>/ - return the full video object and metadata.
    path('<uuid:video_id>/', video_detail_view, name='video-detail'),
    # GET /api/videos/<video_id>/status/ - return processing/upload status.
    path('<uuid:video_id>/status/', video_status_view, name='video-status'),
    # PATCH/DELETE /api/videos/<video_id>/ - update or remove a video.
    path('<uuid:video_id>/manage/', video_update_delete_view, name='video-manage'),
    
    # Worker API routes
    # GET /api/videos/worker/status/ - get worker status
    path('worker/status/', worker_status, name='worker-status'),
    # POST /api/videos/worker/heartbeat/ - send worker heartbeat
    path('worker/heartbeat/', worker_heartbeat, name='worker-heartbeat'),
    # GET /api/videos/worker/jobs/next/ - get next pending job
    path('worker/jobs/next/', get_next_job, name='worker-jobs-next'),
    # GET /api/videos/worker/jobs/ - the approved queue, in run order
    path('worker/jobs/', list_queue, name='worker-jobs-list'),
    # GET /api/videos/worker/jobs/review/ - videos awaiting approval
    path('worker/jobs/review/', list_review_queue, name='worker-jobs-review'),
    # POST /api/videos/worker/jobs/reorder/ - set queue order
    path('worker/jobs/reorder/', reorder_queue_view, name='worker-jobs-reorder'),
    # POST /api/videos/worker/jobs/requeue-stale/ - requeue all videos not on the
    # current analyzer version (re-run the corpus after an algorithm change)
    path('worker/jobs/requeue-stale/', requeue_stale_version_view, name='worker-jobs-requeue-stale'),
    # POST /api/videos/worker/jobs/<job_id>/approval/ - approve or reject
    path('worker/jobs/<uuid:job_id>/approval/', worker_decide_approval, name='worker-job-approval'),
    # POST /api/videos/worker/jobs/<job_id>/claim/ - claim a job
    path('worker/jobs/<uuid:job_id>/claim/', claim_job_view, name='worker-job-claim'),
    # POST /api/videos/worker/jobs/<job_id>/progress/ - update job progress
    path('worker/jobs/<uuid:job_id>/progress/', update_job_progress_view, name='worker-job-progress'),
    # POST /api/videos/worker/jobs/<job_id>/complete/ - complete a job
    path('worker/jobs/<uuid:job_id>/complete/', complete_job_view, name='worker-job-complete'),
    # POST /api/videos/worker/jobs/<job_id>/fail/ - fail a job
    path('worker/jobs/<uuid:job_id>/fail/', fail_job_view, name='worker-job-fail'),
    # POST /api/videos/worker/jobs/<job_id>/cancel/ - cancel a job
    path('worker/jobs/<uuid:job_id>/cancel/', cancel_job_view, name='worker-job-cancel'),
    # GET /api/videos/admin/moderation/ - everything needing a human decision.
    path('admin/moderation/', moderation_overview, name='admin-moderation'),
    # POST /api/videos/admin/jobs/<job_id>/retry/ - admin retry a job
    path('admin/jobs/<uuid:job_id>/retry/', admin_retry_job_view, name='admin-job-retry'),
    # POST /api/videos/admin/jobs/reset-stale/ - admin reset stale jobs
    path('admin/jobs/reset-stale/', reset_stale_jobs_view, name='admin-reset-stale'),
]
