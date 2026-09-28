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

    # A record with no upload fingerprint was made by a worker check on a
    # video uploaded before fingerprinting: there is no earlier upload to
    # keep, only this first one to record.
    if record.sha256 and record.sha256 != digest:
        record.history = [*record.history, {
            'sha256': record.sha256,
            'size_bytes': record.size_bytes,
            'original_filename': record.original_filename,
            'uploaded_by': record.uploaded_by,
            'uploaded_at': record.uploaded_at.isoformat() if record.uploaded_at else None,
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
    # Provenance, moments and overlay described the old file too.
    for key in ('provenance', 'moments', 'overlay'):
        setattr(record, key, {})
        setattr(record, f'{key}_at', None)
    record.save()
    return record


def record_worker_check(video_id, worker_id: str, sha256: str) -> str | None:
    """Store the worker's fingerprint of what it downloaded and analyzed.

    Returns the resulting custody status, or None when the worker sent no
    usable hash (workers built before this existed). A mismatch
    does not fail the job: the analysis still describes a real file, and the
    report surfaces the mismatch for a person to judge.
    """
    sha256 = (sha256 or '').strip().lower()
    if not SHA256_PATTERN.match(sha256):
        return None

    # A video uploaded before fingerprinting has no record yet. One is made so
    # the worker's hash is kept -- it still says what was analyzed -- though
    # with nothing to compare against it stays unverified.
    record, _ = EvidenceRecord.objects.get_or_create(video_id=video_id)
    record.worker_sha256 = sha256
    record.worker_id = worker_id
    record.worker_checked_at = timezone.now()
    record.save(update_fields=['worker_sha256', 'worker_id', 'worker_checked_at'])

    if record.custody_status == EvidenceRecord.CUSTODY_MISMATCH:
        logger.warning(
            'Video %s: worker %s analyzed %s but %s was uploaded',
            video_id, worker_id, sha256, record.sha256)
    return record.custody_status


def record_private_evidence(video_id, private: dict | None) -> None:
    """Store the analyzer's private findings on the evidence record.

    Only known keys are kept, so an analyzer bug cannot fill this table with
    whatever it happened to emit.
    """
    if not isinstance(private, dict):
        return

    now = timezone.now()
    fields = {}
    for key in ('provenance', 'moments', 'overlay'):
        value = private.get(key)
        if isinstance(value, dict) and value:
            fields[key] = value
            fields[f'{key}_at'] = now
    if not fields:
        return

    record, _ = EvidenceRecord.objects.get_or_create(video_id=video_id)
    for name, value in fields.items():
        setattr(record, name, value)
    record.save(update_fields=list(fields))
