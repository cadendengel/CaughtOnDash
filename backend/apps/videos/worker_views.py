"""Worker API views for desktop worker communication."""

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from apps.videos.models import Video
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
)
from apps.videos.worker_services import (
    get_or_create_worker,
    update_worker_heartbeat,
    get_next_pending_job,
    claim_job,
    update_job_progress,
    complete_job,
    fail_job,
    cancel_job,
    reset_stale_jobs,
    admin_retry_job,
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
    worker_name = validated_data['worker_name']
    status = validated_data['status']
    current_job_id = validated_data.get('current_job_id')
    stage = validated_data.get('stage', '')
    progress = validated_data.get('progress', 0)
    
    # Update worker
    worker = update_worker_heartbeat(worker_id, status, current_job_id)
    
    # If processing a job, update its progress and last_seen_at
    if current_job_id:
        try:
            job = Video.objects.get(id=current_job_id)
            job.analysis_stage = stage or job.analysis_stage
            job.analysis_progress = progress
            job.worker_last_seen_at = worker.last_seen_at
            job.save()
        except Video.DoesNotExist:
            pass
    
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


@require_http_methods(["POST"])
def admin_retry_job_view(request, job_id):
    """POST /api/admin/jobs/{job_id}/retry/ - Admin: Retry a failed/cancelled job."""
    # TODO: Add admin auth check here
    result = admin_retry_job(job_id)
    
    if not result['success']:
        return JsonResponse(result, status=400)
    
    return JsonResponse(result)


@require_http_methods(["POST"])
def reset_stale_jobs_view(request):
    """POST /api/admin/jobs/reset-stale/ - Admin: Reset stale processing jobs."""
    # TODO: Add admin auth check here
    timeout_minutes = request.GET.get('timeout_minutes', 2)
    try:
        timeout_minutes = int(timeout_minutes)
    except (ValueError, TypeError):
        timeout_minutes = 2
    
    result = reset_stale_jobs(timeout_minutes)
    return JsonResponse(result)
