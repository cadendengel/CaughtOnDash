"""Chain of custody: fingerprint the upload, then check the worker saw the same bytes."""

from __future__ import annotations

import hashlib
import logging
import re

from django.utils import timezone

from apps.incidents.models import EvidenceRecord

logger = logging.getLogger(__name__)

SHA256_PATTERN = re.compile(r'^[0-9a-f]{64}$')


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def record_upload(video, data: bytes, filename: str, content_type: str, uploaded_by: str) -> EvidenceRecord:
    """Fingerprint the bytes as they arrive.

    A second upload for the same video replaces the stored file, so the record
    keeps the fingerprint it replaces in `history` instead of losing it. The
    worker check is reset too: it vouched for the old bytes, not these.
    """
    digest = sha256_hex(data)
    now = timezone.now()

    record = EvidenceRecord.objects.filter(video=video).first()
    if record is None:
        return EvidenceRecord.objects.create(
            video=video,
            sha256=digest,
            size_bytes=len(data),
            original_filename=filename,
            content_type=content_type,
            uploaded_by=uploaded_by,
            uploaded_at=now,
        )

    if record.sha256 != digest:
        record.history = [*record.history, {
            'sha256': record.sha256,
            'size_bytes': record.size_bytes,
            'original_filename': record.original_filename,
            'uploaded_by': record.uploaded_by,
            'uploaded_at': record.uploaded_at.isoformat(),
            'replaced_at': now.isoformat(),
        }]
        logger.warning('Video %s file replaced: %s -> %s', video.id, record.sha256, digest)

    record.sha256 = digest
    record.size_bytes = len(data)
    record.original_filename = filename
    record.content_type = content_type
    record.uploaded_by = uploaded_by
    record.uploaded_at = now
    record.worker_sha256 = ''
    record.worker_id = ''
    record.worker_checked_at = None
    record.save()
    return record


def record_worker_check(video_id, worker_id: str, sha256: str) -> str | None:
    """Store the worker's fingerprint of what it downloaded and analyzed.

    Returns the resulting custody status, or None when there is nothing to
    check against -- videos uploaded before fingerprinting existed. A mismatch
    does not fail the job: the analysis still describes a real file, and the
    report surfaces the mismatch for a person to judge.
    """
    sha256 = (sha256 or '').strip().lower()
    if not SHA256_PATTERN.match(sha256):
        return None

    record = EvidenceRecord.objects.filter(video_id=video_id).first()
    if record is None:
        return None

    record.worker_sha256 = sha256
    record.worker_id = worker_id
    record.worker_checked_at = timezone.now()
    record.save(update_fields=['worker_sha256', 'worker_id', 'worker_checked_at'])

    if record.custody_status == EvidenceRecord.CUSTODY_MISMATCH:
        logger.warning(
            'Video %s: worker %s analyzed %s but %s was uploaded',
            video_id, worker_id, sha256, record.sha256)
    return record.custody_status
