"""The worker's way in: evidence images, uploaded while a job is running."""

from __future__ import annotations

import logging
import re
import uuid

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.incidents.models import EvidenceArtifact
from apps.incidents.services import sha256_hex
from apps.storage import upload_private_bytes
from apps.videos.models import Video
from apps.videos.worker_auth import worker_required
from apps.videos.worker_services import current_analysis_run

logger = logging.getLogger(__name__)

MAX_ARTIFACT_BYTES = 10 * 1024 * 1024
KINDS = {kind for kind, _ in EvidenceArtifact.KIND_CHOICES}
SHA256_PATTERN = re.compile(r'^[0-9a-fA-F]{64}$')

# Decided by the bytes, not by the filename or the declared type: the file is
# served back to a browser, and only images belong in this bucket.
SIGNATURES = (
    (b'\xff\xd8\xff', 'image/jpeg', 'jpg'),
    (b'\x89PNG\r\n\x1a\n', 'image/png', 'png'),
)


def _sniff(data: bytes) -> tuple[str, str] | None:
    for signature, content_type, extension in SIGNATURES:
        if data.startswith(signature):
            return content_type, extension
    return None


def _optional_float(value) -> float | None:
    try:
        return float(value) if value not in (None, '') else None
    except (TypeError, ValueError):
        return None


def _optional_int(value) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _bad_request(detail: str) -> JsonResponse:
    return JsonResponse({'success': False, 'error': detail}, status=400)


@csrf_exempt
@require_http_methods(['POST'])
@worker_required
def upload_artifact_view(request, job_id):
    """POST /api/videos/worker/jobs/<job_id>/artifacts/ -- multipart, one image.

    Accepted only from the worker that holds the job, while it is processing:
    a finished or reassigned job cannot gain evidence after the fact.
    """
    worker_id = (request.POST.get('worker_id') or '').strip()
    video = Video.objects.filter(id=job_id).first()
    if video is None:
        return JsonResponse({'success': False, 'error': 'Job not found'}, status=404)
    if not worker_id or video.worker_id != worker_id:
        return _bad_request('Worker does not own this job')
    if video.analysis_status != 'processing':
        return _bad_request(f'Cannot add evidence to a job in state: {video.analysis_status}')

    kind = request.POST.get('kind') or ''
    if kind not in KINDS:
        return _bad_request(f'Unknown artifact kind: {kind!r}')

    upload = request.FILES.get('file')
    if upload is None:
        return _bad_request('file is required')
    if upload.size > MAX_ARTIFACT_BYTES:
        return _bad_request(f'Artifact is larger than {MAX_ARTIFACT_BYTES} bytes')

    data = upload.read()
    sniffed = _sniff(data)
    if sniffed is None:
        return _bad_request('Artifact must be a JPEG or PNG image')
    content_type, extension = sniffed

    digest = sha256_hex(data)
    claimed = (request.POST.get('sha256') or '').strip().lower()
    if claimed and (not SHA256_PATTERN.match(claimed) or claimed != digest):
        # Damaged in transit, or the worker hashed something else. Either way
        # this is not the file the worker vouched for.
        return _bad_request('sha256 does not match the uploaded bytes')

    run = current_analysis_run(video.id)
    artifact_id = uuid.uuid4()
    path = f'{video.id}/{run.id if run else "unattributed"}/{artifact_id}.{extension}'
    try:
        upload_private_bytes(path, data, content_type)
    except Exception as exc:
        logger.error('Evidence upload failed for %s: %s', video.id, exc)
        return JsonResponse({'success': False, 'error': 'Storage rejected the artifact'}, status=502)

    EvidenceArtifact.objects.create(
        id=artifact_id,
        video=video,
        run=run,
        kind=kind,
        storage_path=path,
        content_type=content_type,
        sha256=digest,
        t_seconds=_optional_float(request.POST.get('t_seconds')),
        width=_optional_int(request.POST.get('width')),
        height=_optional_int(request.POST.get('height')),
        label=(request.POST.get('label') or '')[:255],
        analyzer_version=(request.POST.get('analyzer_version') or '')[:50],
    )
    return JsonResponse({'success': True, 'artifact_id': str(artifact_id), 'sha256': digest})
