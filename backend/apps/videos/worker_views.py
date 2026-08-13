"""Worker API views for desktop worker communication."""

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.accounts.auth import admin_required
from apps.videos.models import AnalysisRun, Video
from apps.videos.worker_auth import worker_required
from apps.videos.worker_serializers import (
    WorkerStatusSerializer,
    WorkerHeartbeatSerializer,
    JobDto,
    JobClaimSerializer,
    JobProgressSerializer,
    JobCompleteSerializer,
    JobFailSerializer,
    JobCancelSerializer,
    QueueEntrySerializer,
)
from apps.videos.worker_services import (
    claimable_jobs,
    decide_approval,
    failed_analyses,
    stuck_analyses,
    reorder_queue,
    review_queue,
    get_or_create_worker,
    apply_worker_heartbeat,
    get_next_pending_job,
    claim_job,
    update_job_progress,
    complete_job,
    fail_job,
    cancel_job,
    reset_stale_jobs,
    admin_retry_job,
    requeue_stale_version,
    STALE_PROCESSING_MINUTES,
)


@require_http_methods(["GET"])
@worker_required
def worker_status(request):
    """GET /api/worker/status/ - Return authenticated worker status."""
    # Extract worker_id from the request if available
    # For now, we'll just return a generic response
    return JsonResponse({
        'worker_id': 'caden-desktop-1',
        'status': 'idle',
        'last_seen_at': None,
        'current_job_id': None
    })


@csrf_exempt
@require_http_methods(["POST"])
@worker_required
def worker_heartbeat(request):
    """POST /api/worker/heartbeat/ - Send worker heartbeat and status."""
    try:
        import json
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    
    serializer = WorkerHeartbeatSerializer(data=data)
    if not serializer.is_valid():
        return JsonResponse({'error': 'Invalid heartbeat data', 'details': serializer.errors}, status=400)
    
    validated_data = serializer.validated_data
    worker_id = validated_data['worker_id']
    status = validated_data['status']
    current_job_id = validated_data.get('current_job_id')
    stage = validated_data.get('stage', '')
    progress = validated_data.get('progress', 0)
    
    # Shared with the worker WebSocket, so both transports mean the same thing
    # by a heartbeat. worker_name is accepted for wire compatibility but the
    # worker record is keyed on worker_id.
    worker = apply_worker_heartbeat(worker_id, status, current_job_id, stage, progress)

    return JsonResponse({
        'worker_id': worker.id,
        'status': worker.status,
        'last_seen_at': worker.last_seen_at.isoformat() if worker.last_seen_at else None,
    })


@require_http_methods(["GET"])
@worker_required
def get_next_job(request):
    """GET /api/worker/jobs/next/ - Get next pending job without claiming it."""
    job = get_next_pending_job()
    
    if not job:
        return JsonResponse({
            'job': None,
            'message': 'No pending jobs'
        })
    
    serializer = JobDto(job)
    return JsonResponse({
        'job': serializer.data,
        'message': 'Job available for claiming'
    })


# The all-videos table loads in one request and sorts client-side, which is
# simple and fine at the current corpus size. This ceiling is the point where
# that stops being true: past it the response is truncated and flagged, rather
# than growing unboundedly, and the table needs real pagination instead.
ALL_VIDEOS_LIMIT = 2000


def _queue_payload(queryset, limit=200):
    """Serialize a queue, pulling each video's run history in one extra query."""
    videos = list(queryset[:limit])
    runs_by_video = {}
    if videos:
        for run in AnalysisRun.objects.filter(
            video_id__in=[video.id for video in videos]
        ).order_by('-attempt_number'):
            runs_by_video.setdefault(run.video_id, []).append(run)

    for video in videos:
        # Newest attempt first, which is what the serializer's helpers expect.
        video.prefetched_runs = runs_by_video.get(video.id, [])

    return QueueEntrySerializer(videos, many=True).data


@require_http_methods(["GET"])
@worker_required
def list_queue(request):
    """GET /api/videos/worker/jobs/ - the approved queue, in run order."""
    entries = _queue_payload(claimable_jobs())
    return JsonResponse({'count': len(entries), 'items': entries})


@require_http_methods(["GET"])
@worker_required
def list_review_queue(request):
    """GET /api/videos/worker/jobs/review/ - videos awaiting a decision."""
    entries = _queue_payload(review_queue())
    return JsonResponse({'count': len(entries), 'items': entries})


@require_http_methods(["GET"])
@admin_required
def all_videos(request):
    """GET /api/videos/admin/all/ - every video, with its state and details.

    The queue views answer "what should run next"; this answers "what is the
    state of everything", which is a different question and had no home. It is
    also how a corpus-wide re-run is watched: `analyzer_version` per row shows
    what has been re-analyzed and what is still on the old model.

    Deliberately unpaginated and unsorted beyond a stable newest-first default:
    the client holds the whole list and sorts it by column. That is the right
    trade at this size and the wrong one later -- see the limit note below.
    """
    videos = Video.objects.filter(deleted_at__isnull=True)

    # _queue_payload caps at 200, which would silently truncate a table whose
    # whole promise is "every video". Pass an explicit ceiling and tell the
    # caller when it was hit, so the UI can say so rather than quietly lie.
    total = videos.count()
    entries = _queue_payload(videos, limit=ALL_VIDEOS_LIMIT)

    return JsonResponse({
        'count': len(entries),
        'total': total,
        'truncated': total > len(entries),
        'limit': ALL_VIDEOS_LIMIT,
        'items': entries,
    })


@require_http_methods(["GET"])
@admin_required
def moderation_overview(request):
    """GET /api/videos/admin/moderation/ - everything needing a human decision.

    Three groups, because each needs a different action and conflating them
    hides the difference:

    - awaiting review: nobody has judged it yet
    - failed: analysis broke, so there was nothing to judge
    - stuck: claims to be processing but its worker went quiet, which is the
      case nobody notices -- the site shows a progress bar that will never
      move and no error is ever raised

    Reuses the worker queue serializer so a moderator and the desktop app
    describe the same video the same way, attempt history included.
    """
    groups = {
        'awaiting_review': review_queue(),
        'failed': failed_analyses(),
        'stuck': stuck_analyses(),
    }

    payload = {name: _queue_payload(queryset) for name, queryset in groups.items()}
    counts = {name: len(entries) for name, entries in payload.items()}
    counts['total'] = sum(counts.values())

    return JsonResponse({'counts': counts, 'groups': payload})


@csrf_exempt
@require_http_methods(["POST"])
@worker_required
def worker_decide_approval(request, job_id):
    """POST /api/videos/worker/jobs/{job_id}/approval/ - approve or reject.

    Worker-token authenticated rather than Clerk: the desktop app holds the
    worker token, and whoever runs it is the operator making these decisions.
    That token can already claim jobs and write analysis results, so approving
    is not a wider grant -- but it does mean the token approves as well as
    processes. The Clerk-authenticated endpoint for owners still exists.
    """
    try:
        import json
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    approve = data.get('approve')
    if not isinstance(approve, bool):
        return JsonResponse({'error': 'approve must be provided as a boolean'}, status=400)

    result = decide_approval(job_id, approve=approve, decided_by=data.get('decided_by') or 'worker')
    if not result['success']:
        return JsonResponse(result, status=409)

    return JsonResponse(result)


@csrf_exempt
@require_http_methods(["POST"])
@worker_required
def reorder_queue_view(request):
    """POST /api/videos/worker/jobs/reorder/ - set the order the queue runs in."""
    try:
        import json
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    result = reorder_queue(data.get('video_ids'))
    if not result['success']:
        return JsonResponse(result, status=400)

    return JsonResponse(result)


@csrf_exempt
@require_http_methods(["POST"])
@worker_required
def claim_job_view(request, job_id):
    """POST /api/worker/jobs/{job_id}/claim/ - Claim a job for processing."""
    try:
        import json
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    
    serializer = JobClaimSerializer(data=data)
    if not serializer.is_valid():
        return JsonResponse({'error': 'Invalid claim data', 'details': serializer.errors}, status=400)
    
    validated_data = serializer.validated_data
    worker_id = validated_data['worker_id']
    worker_name = validated_data['worker_name']
    
    result = claim_job(job_id, worker_id, worker_name)
    
    if not result['success']:
        return JsonResponse(result, status=409)  # Conflict
    
    return JsonResponse(result)


@csrf_exempt
@require_http_methods(["POST"])
@worker_required
def update_job_progress_view(request, job_id):
    """POST /api/worker/jobs/{job_id}/progress/ - Update job progress."""
    try:
        import json
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    
    serializer = JobProgressSerializer(data=data)
    if not serializer.is_valid():
        return JsonResponse({'error': 'Invalid progress data', 'details': serializer.errors}, status=400)
    
    validated_data = serializer.validated_data
    worker_id = validated_data['worker_id']
    stage = validated_data['stage']
    progress = validated_data['progress']
    
    result = update_job_progress(job_id, worker_id, stage, progress)
    
    if not result['success']:
        return JsonResponse(result, status=400)
    
    return JsonResponse(result)


@csrf_exempt
@require_http_methods(["POST"])
@worker_required
def complete_job_view(request, job_id):
    """POST /api/worker/jobs/{job_id}/complete/ - Mark job as complete with analysis results."""
    try:
        import json
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    
    serializer = JobCompleteSerializer(data=data)
    if not serializer.is_valid():
        return JsonResponse({'error': 'Invalid complete data', 'details': serializer.errors}, status=400)
    
    validated_data = serializer.validated_data
    worker_id = validated_data['worker_id']
    summary = validated_data['summary']
    tags = validated_data.get('tags', [])
    events = validated_data.get('events', [])
    metadata = validated_data.get('metadata', {})
    
    result = complete_job(job_id, worker_id, summary, tags, events, metadata)
    
    if not result['success']:
        return JsonResponse(result, status=400)
    
    return JsonResponse(result)


@csrf_exempt
@require_http_methods(["POST"])
@worker_required
def fail_job_view(request, job_id):
    """POST /api/worker/jobs/{job_id}/fail/ - Mark job as failed."""
    try:
        import json
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    
    serializer = JobFailSerializer(data=data)
    if not serializer.is_valid():
        return JsonResponse({'error': 'Invalid fail data', 'details': serializer.errors}, status=400)
    
    validated_data = serializer.validated_data
    worker_id = validated_data['worker_id']
    error = validated_data['error']
    stage = validated_data.get('stage')
    
    result = fail_job(job_id, worker_id, error, stage)
    
    if not result['success']:
        return JsonResponse(result, status=400)
    
    return JsonResponse(result)


@csrf_exempt
@require_http_methods(["POST"])
@worker_required
def cancel_job_view(request, job_id):
    """POST /api/worker/jobs/{job_id}/cancel/ - Cancel/release a job."""
    try:
        import json
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    
    serializer = JobCancelSerializer(data=data)
    if not serializer.is_valid():
        return JsonResponse({'error': 'Invalid cancel data', 'details': serializer.errors}, status=400)
    
    validated_data = serializer.validated_data
    worker_id = validated_data['worker_id']
    reason = validated_data.get('reason', '')
    
    result = cancel_job(job_id, worker_id, reason)
    
    if not result['success']:
        return JsonResponse(result, status=400)
    
    return JsonResponse(result)


@csrf_exempt
@require_http_methods(["POST"])
@worker_required
def requeue_stale_version_view(request):
    """POST /api/worker/jobs/requeue-stale/ - Requeue every analyzed video not on
    the given analyzer_version, so an algorithm change can re-run the corpus.

    Worker-authenticated: the desktop worker knows its own analyzer version and
    passes it, keeping analyze.py the single source of truth for the version.
    """
    try:
        import json
        data = json.loads(request.body or b'{}')
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    analyzer_version = str(data.get('analyzer_version') or '').strip()
    if not analyzer_version:
        return JsonResponse({'error': 'analyzer_version is required'}, status=400)
    requested_by = str(data.get('worker_id') or 'worker')

    result = requeue_stale_version(analyzer_version, requested_by=requested_by)
    if not result['success']:
        return JsonResponse(result, status=400)
    return JsonResponse(result)


@csrf_exempt
@require_http_methods(["POST"])
@worker_required
def worker_reset_stale_view(request):
    """POST /api/videos/worker/jobs/reset-stale/ - free jobs whose worker died.

    A worker that crashes mid-job leaves the video claimed and 'processing'
    forever: requeue skips it deliberately, so it cannot be yanked from a worker
    that is genuinely running it, and nothing else ever clears it. The site then
    shows a progress bar that will never move. That happened here -- a UI crash
    orphaned a job, and clearing it needed a hand-authenticated admin call.

    Worker-authenticated, like requeue-stale and for the same reason: this is
    queue maintenance, and the worker is both what orphans jobs and what needs
    them back. The token can already claim, cancel and requeue the whole corpus,
    so this grants nothing new.
    """
    try:
        timeout_minutes = int(request.GET.get('timeout_minutes', STALE_PROCESSING_MINUTES))
    except (TypeError, ValueError):
        return JsonResponse({'error': 'timeout_minutes must be a whole number'}, status=400)

    # A zero or negative timeout would reset jobs a live worker is holding,
    # which is the one thing this must not do.
    if timeout_minutes < 1:
        return JsonResponse({'error': 'timeout_minutes must be at least 1'}, status=400)

    return JsonResponse(reset_stale_jobs(timeout_minutes))


@csrf_exempt
@require_http_methods(["POST"])
@admin_required
def admin_retry_job_view(request, job_id):
    """POST /api/admin/jobs/{job_id}/retry/ - Admin: Retry a failed/cancelled job."""
    result = admin_retry_job(job_id)
    
    if not result['success']:
        return JsonResponse(result, status=400)
    
    return JsonResponse(result)


@csrf_exempt
@require_http_methods(["POST"])
@admin_required
def reset_stale_jobs_view(request):
    """POST /api/admin/jobs/reset-stale/ - Admin: Reset stale processing jobs."""
    timeout_minutes = request.GET.get('timeout_minutes', 2)
    try:
        timeout_minutes = int(timeout_minutes)
    except (ValueError, TypeError):
        timeout_minutes = 2
    
    result = reset_stale_jobs(timeout_minutes)
    return JsonResponse(result)
