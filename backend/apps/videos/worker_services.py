"""Business logic for worker operations."""

from datetime import datetime, timedelta
import uuid

from django.db import transaction
from django.utils import timezone

from apps.videos.models import Video, Worker


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
    job = Video.objects.filter(
        analysis_status='pending'
    ).order_by('analysis_requested_at', 'created_at').first()
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
def admin_retry_job(job_id: uuid.UUID) -> dict:
    """Admin endpoint to retry a failed or cancelled job."""
    try:
        job = Video.objects.select_for_update().get(id=job_id)
    except Video.DoesNotExist:
        return {'success': False, 'error': 'Job not found'}
    
    if job.analysis_status not in ('failed', 'cancelled'):
        return {'success': False, 'error': f'Cannot retry job in state: {job.analysis_status}'}
    
    # Reset to pending
    job.analysis_status = 'pending'
    job.analysis_stage = 'queued'
    job.analysis_progress = 0
    job.analysis_error = ''
    job.worker_id = None
    job.worker_name = ''
    job.save()
    
    return {'success': True, 'job_id': str(job_id)}
