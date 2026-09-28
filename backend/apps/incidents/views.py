"""The incident endpoint: the only way in to a video's private evidence."""

from __future__ import annotations

import logging
import uuid

from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from apps.accounts.models import AdminUser
from apps.incidents.models import (
    EvidenceArtifact,
    EvidenceRecord,
    IncidentAccessLog,
    IncidentReport,
    OtherParty,
)
from apps.incidents import export, photos
from apps.incidents.services import sha256_hex
from apps.storage import delete_private_objects, download_private_bytes, signed_object_url, upload_private_bytes
from apps.store import MalformedJSON, current_clerk_user_id, parse_json_request_strict, response_envelope
from apps.videos.models import Video

logger = logging.getLogger(__name__)

ACCESS_LOG_ENTRIES = 20
NOTES_MAX_LENGTH = 20000


def _not_found() -> JsonResponse:
    # 404, not 403, for anyone but the owner or an admin: the endpoint must not
    # confirm that a video has incident data at all.
    return JsonResponse({'detail': 'Not found.'}, status=404)


def _sign(path: str, artifact_id) -> str | None:
    if not path:
        return None
    try:
        return signed_object_url(path)
    except Exception as exc:
        # One unsignable image must not take down the whole report.
        logger.warning('Could not sign artifact %s: %s', artifact_id, exc)
        return None


def _serialize_artifact(artifact: EvidenceArtifact) -> dict:
    original_url = _sign(artifact.storage_path, artifact.id)
    return {
        'id': str(artifact.id),
        'kind': artifact.kind,
        # What to display: the JPEG preview when there is one (a HEIC
        # original will not render), otherwise the file itself.
        'url': _sign(artifact.preview_path, artifact.id) if artifact.preview_path else original_url,
        'original_url': original_url,
        'original_filename': artifact.original_filename,
        'content_type': artifact.content_type,
        'metadata': artifact.metadata,
        'sha256': artifact.sha256,
        't_seconds': artifact.t_seconds,
        'bbox': artifact.bbox,
        'width': artifact.width,
        'height': artifact.height,
        'label': artifact.label,
        'analyzer_version': artifact.analyzer_version,
        # Which attempt made it, so images from a re-run can be told apart.
        'attempt_number': artifact.run.attempt_number if artifact.run else None,
        'created_at': artifact.created_at.isoformat(),
    }


def _report_payload(video: Video) -> dict:
    record = EvidenceRecord.objects.filter(video=video).first()
    report = IncidentReport.objects.filter(video=video).first()
    other_party = OtherParty.objects.filter(report=report).first() if report else None
    return {
        'video_id': str(video.id),
        'evidence': record.to_dict() if record else None,
        'report': report.to_dict() if report else None,
        'other_party': other_party.to_dict() if other_party else None,
        'artifacts': [
            _serialize_artifact(a)
            for a in EvidenceArtifact.objects.filter(video=video).select_related('run')
        ],
        'access_log': [
            entry.to_dict()
            for entry in IncidentAccessLog.objects.filter(video=video)[:ACCESS_LOG_ENTRIES]
        ],
    }


def _validate_update(payload: dict) -> tuple[dict, str | None]:
    """Check the body before touching anything. Returns (clean, error)."""
    clean = {}

    if 'notes' in payload:
        notes = payload['notes']
        if not isinstance(notes, str) or len(notes) > NOTES_MAX_LENGTH:
            return {}, f'notes must be a string of at most {NOTES_MAX_LENGTH} characters.'
        clean['notes'] = notes

    if 'other_party' in payload:
        other = payload['other_party']
        if other is None:
            clean['other_party'] = None
        elif not isinstance(other, dict):
            return {}, 'other_party must be an object or null.'
        else:
            unknown = sorted(set(other) - set(OtherParty.FIELDS))
            if unknown:
                return {}, f'Unknown other_party fields: {", ".join(unknown)}.'
            for field, value in other.items():
                if not isinstance(value, str) or len(value) > OtherParty.MAX_LENGTH:
                    return {}, f'other_party.{field} must be a string of at most {OtherParty.MAX_LENGTH} characters.'
            clean['other_party'] = {field: value.strip() for field, value in other.items()}

    if not clean:
        return {}, 'Nothing to update. Send notes and/or other_party.'
    return clean, None


@transaction.atomic
def _apply_update(video: Video, clean: dict) -> None:
    report, _ = IncidentReport.objects.get_or_create(video=video)

    if 'notes' in clean:
        report.notes = clean['notes']
        report.save()

    if 'other_party' in clean:
        fields = clean['other_party']
        if fields is None:
            OtherParty.objects.filter(report=report).delete()
        else:
            # A partial update: fields not sent keep their stored values.
            other, _ = OtherParty.objects.get_or_create(report=report)
            for field, value in fields.items():
                setattr(other, field, value)
            other.save()


@csrf_exempt
def incident_view(request, video_id):
    """GET or PATCH /api/videos/<video_id>/incident/ -- owner and admins only.

    csrf_exempt like the other write views: identity is a bearer token, not a
    session cookie.
    """
    if request.method not in ('GET', 'PATCH'):
        return JsonResponse({'detail': 'Method not allowed.', 'allowed': ['GET', 'PATCH']}, status=405)

    caller = current_clerk_user_id(request)
    if not caller:
        return JsonResponse({'detail': 'Authentication required.'}, status=401)

    video = Video.objects.filter(id=video_id, deleted_at__isnull=True).first()
    if video is None:
        return _not_found()

    is_owner = caller == video.owner_clerk_user_id
    is_admin = AdminUser.is_admin_for(caller)
    if not (is_owner or is_admin):
        return _not_found()

    if request.method == 'PATCH':
        try:
            payload = parse_json_request_strict(request)
        except MalformedJSON as exc:
            return JsonResponse({'detail': str(exc)}, status=400)

        clean, error = _validate_update(payload)
        if error:
            return JsonResponse({'detail': error}, status=400)

        try:
            _apply_update(video, clean)
        except ImproperlyConfigured as exc:
            logger.error('Incident update refused for %s: %s', video.id, exc)
            return JsonResponse({'detail': 'Incident storage is not configured on this server.'}, status=503)
        action = 'update'
    else:
        action = 'view'

    # Logged before the data is assembled, so a read that fails part-way is
    # still on record as attempted.
    IncidentAccessLog.objects.create(
        video=video, clerk_user_id=caller, action=action, as_admin=is_admin and not is_owner)

    try:
        payload = _report_payload(video)
    except ImproperlyConfigured as exc:
        logger.error('Incident read refused for %s: %s', video.id, exc)
        return JsonResponse({'detail': 'Incident storage is not configured on this server.'}, status=503)

    return JsonResponse(response_envelope('incident-report', payload))


@csrf_exempt
def incident_photos_view(request, video_id):
    """POST /api/videos/<video_id>/incident/photos/ -- add a photo to the report.

    Multipart, field `file`: JPEG, PNG or HEIC, judged by content. The
    original is stored untouched and fingerprinted; a JPEG preview goes
    beside it; EXIF is read now. Text on the photo is read by the next worker
    to analyze this video.
    """
    if request.method != 'POST':
        return JsonResponse({'detail': 'Method not allowed.', 'allowed': ['POST']}, status=405)

    caller = current_clerk_user_id(request)
    if not caller:
        return JsonResponse({'detail': 'Authentication required.'}, status=401)
    video = Video.objects.filter(id=video_id, deleted_at__isnull=True).first()
    if video is None:
        return _not_found()
    is_owner = caller == video.owner_clerk_user_id
    is_admin = AdminUser.is_admin_for(caller)
    if not (is_owner or is_admin):
        return _not_found()

    upload = request.FILES.get('file')
    if upload is None:
        return JsonResponse({'detail': 'file is required.'}, status=400)
    if upload.size > photos.MAX_PHOTO_BYTES:
        return JsonResponse({'detail': f'Photos are limited to {photos.MAX_PHOTO_BYTES // (1024 * 1024)} MB.'}, status=400)

    data = upload.read()
    sniffed = photos.sniff(data)
    if sniffed is None:
        return JsonResponse({'detail': 'Only JPEG, PNG and HEIC photos are accepted.'}, status=400)
    content_type, extension = sniffed

    # The browser normally sends the viewable preview alongside the original,
    # made from pixels it already decoded to show the photo. Then the server
    # reads only the metadata and never decodes the image: a 24 MP HEIC peaks
    # at +165 MB here even shrunk first, on a 512 MB instance. A client that
    # sends no preview -- or one that fails the check -- gets the old path.
    try:
        preview_upload = request.FILES.get('preview')
        preview = photos.check_preview(preview_upload.read()) if preview_upload is not None else None
    except ValueError as exc:
        logger.info('Ignoring the preview sent for %s: %s', video.id, exc)
        preview = None
    try:
        if preview is not None:
            metadata = photos.read_metadata(data)
            preview_bytes = preview
        else:
            processed = photos.process(data)
            metadata, preview_bytes = processed['metadata'], processed['preview']
    except Exception as exc:
        logger.warning('Photo for %s could not be read: %s', video.id, exc)
        return JsonResponse({'detail': 'The photo could not be read. It may be damaged.'}, status=400)

    artifact_id = uuid.uuid4()
    original_path = f'{video.id}/photos/{artifact_id}.{extension}'
    preview_path = f'{video.id}/photos/{artifact_id}.preview.jpg'
    try:
        upload_private_bytes(original_path, data, content_type)
        upload_private_bytes(preview_path, preview_bytes, 'image/jpeg')
    except Exception as exc:
        logger.error('Photo upload failed for %s: %s', video.id, exc)
        return JsonResponse({'detail': 'Storage rejected the photo.'}, status=502)
    taken = metadata.get('taken_at', '')
    artifact = EvidenceArtifact.objects.create(
        id=artifact_id,
        video=video,
        kind='photo',
        storage_path=original_path,
        preview_path=preview_path,
        content_type=content_type,
        sha256=sha256_hex(data),
        width=metadata.get('width', 0),
        height=metadata.get('height', 0),
        original_filename=(upload.name or '')[:255],
        label=f'Photo {taken}'.strip()[:255],
        metadata={'exif': {k: v for k, v in metadata.items() if k not in ('width', 'height')}},
    )
    IncidentAccessLog.objects.create(
        video=video, clerk_user_id=caller, action='update', as_admin=is_admin and not is_owner)
    return JsonResponse(response_envelope('incident-photo', {'artifact': _serialize_artifact(artifact)}), status=201)


@csrf_exempt
def incident_photo_delete_view(request, video_id, artifact_id):
    """DELETE /api/videos/<video_id>/incident/photos/<artifact_id>/

    Removes a photo the owner added -- a wrong upload, a duplicate -- with the
    plate crops read from it. Only photos: what the worker produced from the
    video is evidence of the analysis and is not deletable here. Storage goes
    first, so a failure leaves the report as it was rather than rows pointing
    at nothing; the access log records who removed it.
    """
    if request.method != 'DELETE':
        return JsonResponse({'detail': 'Method not allowed.', 'allowed': ['DELETE']}, status=405)

    caller = current_clerk_user_id(request)
    if not caller:
        return JsonResponse({'detail': 'Authentication required.'}, status=401)
    video = Video.objects.filter(id=video_id, deleted_at__isnull=True).first()
    if video is None:
        return _not_found()
    is_owner = caller == video.owner_clerk_user_id
    is_admin = AdminUser.is_admin_for(caller)
    if not (is_owner or is_admin):
        return _not_found()

    photo = EvidenceArtifact.objects.filter(id=artifact_id, video=video, kind='photo').first()
    if photo is None:
        return _not_found()
    crops = list(EvidenceArtifact.objects.filter(
        video=video, kind='plate_crop', metadata__source_artifact_id=str(photo.id)))

    paths = [photo.storage_path, photo.preview_path, *[c.storage_path for c in crops]]
    try:
        delete_private_objects(paths)
    except Exception as exc:
        logger.error('Could not delete photo %s from storage: %s', photo.id, exc)
        return JsonResponse({'detail': 'Storage would not delete the photo; nothing was removed.'}, status=502)

    with transaction.atomic():
        EvidenceArtifact.objects.filter(id__in=[photo.id, *[c.id for c in crops]]).delete()
        IncidentAccessLog.objects.create(
            video=video, clerk_user_id=caller, action='delete', as_admin=is_admin and not is_owner)
    logger.info('Photo %s (sha256 %s) and %d crop(s) deleted from %s by %s',
                photo.id, photo.sha256, len(crops), video.id, caller)
    return JsonResponse(response_envelope('incident-photo-deleted', {'artifact_id': str(photo.id), 'crops_removed': len(crops)}))


def incident_export_view(request, video_id):
    """GET /api/videos/<video_id>/incident/export/ -- the evidence package.

    Owner and admins only, and logged as its own action: it carries the other
    party's details off the site in one file.
    """
    if request.method != 'GET':
        return JsonResponse({'detail': 'Method not allowed.', 'allowed': ['GET']}, status=405)

    caller = current_clerk_user_id(request)
    if not caller:
        return JsonResponse({'detail': 'Authentication required.'}, status=401)
    video = Video.objects.filter(id=video_id, deleted_at__isnull=True).first()
    if video is None:
        return _not_found()
    is_owner = caller == video.owner_clerk_user_id
    is_admin = AdminUser.is_admin_for(caller)
    if not (is_owner or is_admin):
        return _not_found()

    IncidentAccessLog.objects.create(
        video=video, clerk_user_id=caller, action='export', as_admin=is_admin and not is_owner)

    try:
        payload = _report_payload(video)
    except ImproperlyConfigured as exc:
        logger.error('Incident export refused for %s: %s', video.id, exc)
        return JsonResponse({'detail': 'Incident storage is not configured on this server.'}, status=503)

    paths = dict(EvidenceArtifact.objects.filter(video=video).values_list('id', 'storage_path'))
    for artifact in payload['artifacts']:
        artifact['storage_path'] = paths.get(uuid.UUID(artifact['id']), '')

    data, missing = export.build_zip(video, payload, download_private_bytes)
    if missing:
        logger.warning('Export for %s left out %d file(s)', video.id, len(missing))

    response = HttpResponse(data, content_type='application/zip')
    stamp = timezone.now().strftime('%Y%m%d-%H%M%S')
    response['Content-Disposition'] = f'attachment; filename="incident-{video.id}-{stamp}.zip"'
    return response
