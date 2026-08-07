"""Business logic for worker operations."""

from datetime import datetime, timedelta
import uuid

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.videos.models import AnalysisRun, Video, Worker
from apps.videos.tagging import normalize_video_tags


def open_analysis_run(video: Video, requested_by: str = '') -> AnalysisRun:
    """Record a new attempt and put the video back in the review queue.

    Called whenever analysis is requested -- on upload and on re-analysis --
    rather than on completion, so an attempt that was never approved, or that
    failed, still appears in the history.
    """
    last_attempt = (
        AnalysisRun.objects.filter(video=video)
        .order_by('-attempt_number')
        .values_list('attempt_number', flat=True)
        .first()
    )

    return AnalysisRun.objects.create(
        video=video,
        attempt_number=(last_attempt or 0) + 1,
        status='awaiting_approval',
        requested_by=requested_by or '',
    )


def current_analysis_run(video_id: uuid.UUID) -> AnalysisRun | None:
    """The most recent attempt for a video, whatever state it is in."""
    return AnalysisRun.objects.filter(video_id=video_id).order_by('-attempt_number').first()


def _finish_run(video_id: uuid.UUID, status: str, **fields) -> None:
    """Close out the current run. Never raises: history must not fail a job."""
    run = current_analysis_run(video_id)
    if run is None:
        return

    run.status = status
    run.finished_at = timezone.now()
    for name, value in fields.items():
        setattr(run, name, value)
    run.save()


def merge_ai_tags(existing_tags, ai_tags) -> list[dict[str, str]]:
    """Replace the AI-sourced tags on a video, leaving human ones alone.

    Re-analysis must not delete tags a person added, so only entries sourced
    'ai' are cleared. Where a model tag duplicates one a human already applied,
    the human's is kept -- their source attribution is the more informative of
    the two, and duplicate pills would render twice.
    """
    kept = [tag for tag in normalize_video_tags(existing_tags) if tag['source'] != 'ai']
    seen = {tag['text'].casefold() for tag in kept}

    detected = [
        tag for tag in normalize_video_tags(ai_tags or [], default_source='ai')
        if tag['text'].casefold() not in seen
    ]

    return kept + detected


def _duration_from_metadata(metadata: dict | None) -> int | None:
    """Whole seconds from analyzer metadata, or None if it did not report any."""
    if not metadata:
        return None

    raw = metadata.get('duration_seconds')
    if raw is None:
        return None

    try:
        duration = int(round(float(raw)))
    except (TypeError, ValueError):
        return None

    # A zero or negative duration means the analyzer could not determine one;
    # keep whatever the record already had rather than overwriting with nothing.
    return duration if duration > 0 else None


def get_or_create_worker(worker_id: str, worker_name: str) -> Worker:
    """Get or create a worker record."""
    worker, _ = Worker.objects.get_or_create(
        id=worker_id,
        defaults={
            'name': worker_name,
            'token_hash': '',  # Will be validated in auth layer
        }
    )
    return worker


def update_worker_heartbeat(worker_id: str, status: str, current_job_id: uuid.UUID | None = None) -> Worker:
    """Update worker heartbeat timestamp and status."""
    worker = get_or_create_worker(worker_id, worker_id)
    worker.status = status
    worker.current_job = current_job_id
    worker.last_seen_at = timezone.now()
    worker.save()
    return worker


def get_next_pending_job() -> Video | None:
    """Get the next pending analysis job without claiming it."""
    # nulls_last matters: SQLite sorts NULLs first and Postgres sorts them last,
    # so a bare order_by would queue rows predating the explicit enqueue in a
    # different order locally than in production. Pinning it keeps dev and prod
    # in agreement, and puts legacy NULL rows behind explicitly-requested ones.
    job = Video.objects.filter(
        analysis_status='pending',
        status='ready',
        # Approval gate: analysis capacity is spent on videos someone chose,
        # not on everything that happens to be uploaded.
        approval_status='approved',
        deleted_at__isnull=True,
    ).order_by(F('analysis_requested_at').asc(nulls_last=True), 'created_at').first()
    return job


@transaction.atomic
def claim_job(job_id: uuid.UUID, worker_id: str, worker_name: str) -> dict:
    """
    Atomically claim a job. Only succeed if the job is in a claimable state.
    
    Returns:
        dict with 'success', 'job_id', 'analysis_status', and optional 'error'
    """
    now = timezone.now()
    
    try:
        job = Video.objects.select_for_update().get(id=job_id)
    except Video.DoesNotExist:
        return {
            'success': False,
            'error': 'Job not found'
        }
    
    # Check if job is in a claimable state
    if job.analysis_status not in ('pending', 'failed', 'cancelled'):
        return {
            'success': False,
            'error': f'Job already claimed or in state: {job.analysis_status}'
        }
    
    # If job is processing and stale, allow reclaim
    if job.analysis_status == 'processing' and job.worker_last_seen_at:
        stale_threshold = now - timedelta(minutes=2)
        if job.worker_last_seen_at > stale_threshold:
            # Job is still being processed by an active worker
            return {
                'success': False,
                'error': 'Job already claimed by an active worker'
            }
    
    # Claim the job
    job.analysis_status = 'processing'
    job.analysis_stage = 'claimed'
    job.analysis_progress = 0
    job.worker_id = worker_id
    job.worker_name = worker_name
    job.worker_claimed_at = now
    job.worker_last_seen_at = now
    job.analysis_started_at = now
    job.analysis_error = ''
    job.save()
    
    run = current_analysis_run(job_id)
    if run is not None:
        run.status = 'processing'
        run.started_at = now
        run.worker_id = worker_id
        run.worker_name = worker_name
        run.save()

    # Update worker status
    update_worker_heartbeat(worker_id, 'processing', job_id)
    
    return {
        'success': True,
        'job_id': str(job_id),
        'analysis_status': 'processing'
    }


@transaction.atomic
def update_job_progress(job_id: uuid.UUID, worker_id: str, stage: str, progress: int) -> dict:
    """Update job progress and stage."""
    try:
        job = Video.objects.select_for_update().get(id=job_id)
    except Video.DoesNotExist:
        return {'success': False, 'error': 'Job not found'}
    
    # Verify worker owns this job
    if job.worker_id != worker_id:
        return {'success': False, 'error': 'Worker does not own this job'}
    
    # Cannot update completed or cancelled jobs
    if job.analysis_status in ('complete', 'cancelled'):
        return {'success': False, 'error': f'Cannot update job in state: {job.analysis_status}'}
    
    # Update progress
    job.analysis_stage = stage
    job.analysis_progress = progress
    job.worker_last_seen_at = timezone.now()
    job.save()
    
    return {'success': True}


@transaction.atomic
def complete_job(job_id: uuid.UUID, worker_id: str, summary: str, tags: list, events: list, metadata: dict) -> dict:
    """Mark a job as complete with analysis results."""
    try:
        job = Video.objects.select_for_update().get(id=job_id)
    except Video.DoesNotExist:
        return {'success': False, 'error': 'Job not found'}
    
    # Verify worker owns this job
    if job.worker_id != worker_id:
        return {'success': False, 'error': 'Worker does not own this job'}
    
    now = timezone.now()
    
    # Mark complete
    job.analysis_status = 'complete'
    job.analysis_stage = 'complete'
    job.analysis_progress = 100
    job.analysis_completed_at = now
    job.worker_last_seen_at = now
    
    # Store analysis results
    job.ai_summary = summary
    job.ai_tags = tags or []
    job.ai_events = events or []
    job.ai_metadata = metadata or {}

    _finish_run(
        job_id, 'complete',
        summary=summary, tags=tags or [], events=events or [], metadata=metadata or {})

    # Publish the detections into `tags` as well, sourced 'ai'. That field is
    # what the feed renders and what search indexes, so tags left only in
    # ai_tags are invisible to both. The tag vocabulary already had an 'ai'
    # source and the frontend already styles it -- this connects them.
    job.tags = merge_ai_tags(job.tags, tags)

    # Prefer the analyzer's duration. The upload path sets one from the
    # browser's video element, which is absent for non-browser uploads and zero
    # whenever the browser could not read the metadata. This value comes from
    # the file itself.
    duration = _duration_from_metadata(metadata)
    if duration is not None:
        job.duration_seconds = duration

    job.save()
    
    # Update worker to idle
    update_worker_heartbeat(worker_id, 'idle')
    
    return {
        'success': True,
        'job_id': str(job_id),
        'analysis_status': 'complete'
    }


@transaction.atomic
def fail_job(job_id: uuid.UUID, worker_id: str, error: str, stage: str | None = None) -> dict:
    """Mark a job as failed."""
    try:
        job = Video.objects.select_for_update().get(id=job_id)
    except Video.DoesNotExist:
        return {'success': False, 'error': 'Job not found'}
    
    # Verify worker owns this job
    if job.worker_id != worker_id:
        return {'success': False, 'error': 'Worker does not own this job'}
    
    now = timezone.now()
    
    # Mark failed
    job.analysis_status = 'failed'
    job.analysis_stage = stage or 'failed'
    job.analysis_failed_at = now
    job.analysis_error = error
    job.worker_last_seen_at = now
    
    job.save()
    
    _finish_run(job_id, 'failed', error=error)

    # Update worker to error state
    update_worker_heartbeat(worker_id, 'error')
    
    return {
        'success': True,
        'job_id': str(job_id),
        'analysis_status': 'failed'
    }


@transaction.atomic
def cancel_job(job_id: uuid.UUID, worker_id: str, reason: str = '') -> dict:
    """Release or cancel a job."""
    try:
        job = Video.objects.select_for_update().get(id=job_id)
    except Video.DoesNotExist:
        return {'success': False, 'error': 'Job not found'}
    
    # Allow cancellation if worker owns the job or any admin can cancel
    if job.worker_id and job.worker_id != worker_id:
        return {'success': False, 'error': 'Worker does not own this job'}
    
    # Return job to pending (not cancelled) so it can be retried
    job.analysis_status = 'pending'
    job.analysis_stage = 'queued'
    job.analysis_progress = 0
    job.worker_id = None
    job.worker_name = ''
    job.worker_claimed_at = None
    job.analysis_error = f'Cancelled: {reason}' if reason else 'Cancelled by worker'
    # A cancelled job goes back to the review queue: it was approved once, but
    # re-running it is a fresh decision.
    job.approval_status = 'pending_review'
    job.approval_decided_by = ''
    job.approval_decided_at = None
    
    job.save()
    
    # Update worker to idle
    update_worker_heartbeat(worker_id, 'idle')
    
    return {'success': True, 'job_id': str(job_id)}


@transaction.atomic
def reset_stale_jobs(timeout_minutes: int = 2) -> dict:
    """Reset stale processing jobs back to pending."""
    now = timezone.now()
    stale_threshold = now - timedelta(minutes=timeout_minutes)
    
    stale_jobs = Video.objects.filter(
        analysis_status='processing',
        worker_last_seen_at__lt=stale_threshold
    )
    
    count = 0
    for job in stale_jobs:
        job.analysis_status = 'pending'
        job.analysis_stage = 'queued'
        job.analysis_progress = 0
        job.analysis_error = f'Worker heartbeat timeout (lasted {timeout_minutes} minutes without update)'
        job.worker_id = None
        job.worker_name = ''
        job.worker_claimed_at = None
        job.save()
        count += 1
    
    return {'reset_count': count}


@transaction.atomic
def decide_approval(video_id: uuid.UUID, approve: bool, decided_by: str) -> dict:
    """Approve or reject a video for analysis.

    Approval only controls whether analysis may run. A rejected video stays
    visible in the feed -- moderating content is a separate decision from
    moderating compute, and conflating them would make this button do two
    things at once.
    """
    try:
        video = Video.objects.select_for_update().get(id=video_id, deleted_at__isnull=True)
    except Video.DoesNotExist:
        return {'success': False, 'error': 'Video not found'}

    if video.analysis_status == 'processing':
        return {'success': False, 'error': 'Analysis is already running for this video'}

    now = timezone.now()
    video.approval_status = 'approved' if approve else 'rejected'
    video.approval_decided_by = decided_by or ''
    video.approval_decided_at = now

    if approve:
        # Approving is what puts it in the queue, so stamp the request time here
        # -- that is the moment it started waiting for a worker.
        video.analysis_status = 'pending'
        video.analysis_stage = 'queued'
        video.analysis_progress = 0
        video.analysis_error = ''
        video.analysis_requested_at = now
    else:
        video.analysis_status = 'cancelled'
        video.analysis_stage = 'cancelled'

    video.save()

    run = current_analysis_run(video_id) or open_analysis_run(video, decided_by)
    run.status = 'approved' if approve else 'rejected'
    run.decided_by = decided_by or ''
    run.decided_at = now
    if not approve:
        run.finished_at = now
    run.save()

    return {
        'success': True,
        'video_id': str(video_id),
        'approval_status': video.approval_status,
        'attempt_number': run.attempt_number,
    }


# States a video can be re-queued from. 'processing' is excluded so a request
# cannot yank a job out from under a worker that is actively running it, and
# 'pending' is excluded because it is already queued.
REQUEUEABLE_ANALYSIS_STATUSES = ('complete', 'failed', 'cancelled')

# A worker heartbeats every 10 seconds. Past this, treat it as gone and let the
# job be re-queued -- otherwise a worker that dies mid-job (or, as happened in
# production, one whose write-backs were all rejected) leaves the video wedged
# in 'processing' forever with no route back for its owner.
STALE_PROCESSING_MINUTES = 5


def _is_stale_processing(job, now) -> bool:
    """True when a job claims to be processing but its worker has gone quiet."""
    if job.analysis_status != 'processing':
        return False

    last_seen = job.worker_last_seen_at or job.worker_claimed_at or job.analysis_started_at
    if last_seen is None:
        # Claimed with no timestamps at all: nothing is coming back for it.
        return True

    return last_seen < now - timedelta(minutes=STALE_PROCESSING_MINUTES)


@transaction.atomic
def request_analysis(job_id: uuid.UUID, requested_by: str = '') -> dict:
    """Request re-analysis of a video.

    This asks for a re-run; it does not queue one. The video returns to
    pending_review and needs approving again, so a re-analysis costs the same
    deliberate decision the first one did.

    Previous AI results are left in place until the new run overwrites them, so
    the video keeps showing its old summary/tags while it waits rather than
    blanking out.
    """
    try:
        job = Video.objects.select_for_update().get(id=job_id)
    except Video.DoesNotExist:
        return {'success': False, 'error': 'Video not found'}

    if job.deleted_at is not None:
        return {'success': False, 'error': 'Video not found'}

    if job.status != 'ready':
        return {'success': False, 'error': 'Video is not ready for analysis yet'}

    if job.approval_status == 'pending_review':
        return {'success': False, 'error': 'This video is already waiting for review'}

    now = timezone.now()
    if job.analysis_status not in REQUEUEABLE_ANALYSIS_STATUSES and not _is_stale_processing(job, now):
        return {'success': False, 'error': f'Cannot re-analyze a video in state: {job.analysis_status}'}

    # Back to the review queue, not straight into the work queue.
    job.approval_status = 'pending_review'
    job.approval_decided_by = ''
    job.approval_decided_at = None

    job.analysis_status = 'cancelled'
    job.analysis_stage = 'queued'
    job.analysis_progress = 0
    job.analysis_error = ''
    job.analysis_requested_at = now
    job.analysis_started_at = None
    job.analysis_completed_at = None
    job.analysis_failed_at = None
    job.worker_id = None
    job.worker_name = ''
    job.worker_claimed_at = None
    job.worker_last_seen_at = None
    job.save()

    run = open_analysis_run(job, requested_by)

    return {
        'success': True,
        'job_id': str(job_id),
        'approval_status': job.approval_status,
        'attempt_number': run.attempt_number,
    }


@transaction.atomic
def admin_retry_job(job_id: uuid.UUID) -> dict:
    """Admin endpoint to retry a failed or cancelled job."""
    try:
        job = Video.objects.select_for_update().get(id=job_id)
    except Video.DoesNotExist:
        return {'success': False, 'error': 'Job not found'}
    
    if job.analysis_status not in ('failed', 'cancelled'):
        return {'success': False, 'error': f'Cannot retry job in state: {job.analysis_status}'}
    
    # Reset to pending. Re-stamping analysis_requested_at sends the retry to the
    # back of the queue rather than leaving it NULL, so one repeatedly-failing
    # video cannot starve newer uploads.
    job.analysis_status = 'pending'
    job.analysis_stage = 'queued'
    job.analysis_progress = 0
    job.analysis_error = ''
    job.analysis_requested_at = timezone.now()
    job.worker_id = None
    job.worker_name = ''
    job.save()
    
    return {'success': True, 'job_id': str(job_id)}
