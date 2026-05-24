from django.views.decorators.csrf import csrf_exempt
from uuid import UUID
import os

from django.views.decorators.csrf import csrf_exempt

from django.http import JsonResponse
from django.db.models import F
from django.utils import timezone

from apps.accounts.models import Profile
from apps.videos.models import Video, VideoComment, VideoLike
from apps.store import get_identity, parse_json_request, response_envelope
from apps.storage import upload_bytes_to_supabase


def _method_not_allowed(*allowed_methods: str) -> JsonResponse:
    return JsonResponse({'detail': 'Method not allowed.', 'allowed': list(allowed_methods)}, status=405)


@csrf_exempt
def upload_url_view(request):
    # POST /api/videos/upload-url/ - create a presigned upload target for a new video.
    if request.method != 'POST':
        return _method_not_allowed('POST')

    payload = parse_json_request(request)
    identity = get_identity(request, payload)
    
    # Ensure profile exists
    Profile.objects.update_or_create(
        clerk_user_id=identity['clerk_user_id'],
        defaults={
            'email': identity['email'],
            'username': identity['username'],
            'display_name': identity['display_name'],
            'avatar_url': identity['avatar_url'],
        }
    )
    
    # Create video record; storage upload will be handled by server-side upload endpoint
    video = Video.objects.create(
        owner_clerk_user_id=identity['clerk_user_id'],
        title=str(payload.get('title') or 'Untitled dashcam clip').strip(),
        description=str(payload.get('description') or '').strip(),
        visibility=str(payload.get('visibility') or 'public').strip() or 'public',
        status='pending',
        original_filename=str(payload.get('original_filename') or payload.get('filename') or ''),
        duration_seconds=max(0, int(float(payload.get('duration_seconds') or 0))),
        tags=[str(tag).strip() for tag in payload.get('tags', []) if str(tag).strip()] if isinstance(payload.get('tags', []), list) else [],
    )

    # For simplicity we accept uploads via the backend at /api/videos/upload/.
    # Client should POST multipart/form-data with 'file' and 'video_id'.
    upload_endpoint = os.getenv('API_BASE', '') + '/api/videos/upload/' if os.getenv('API_BASE') else '/api/videos/upload/'

    return JsonResponse(
        response_envelope(
            'video-upload-url',
            {
                'video': video.to_dict(),
                'upload': {
                    'method': 'POST',
                    'url': upload_endpoint,
                    'headers': {},
                },
            },
        ),
        status=200,
    )



@csrf_exempt
def upload_file_view(request):
    # POST /api/videos/upload/ - accept multipart file upload and store to Supabase
    if request.method != 'POST':
        return _method_not_allowed('POST')

    # Accept either form data or JSON metadata
    video_id = request.POST.get('video_id') or request.headers.get('X-Video-Id')
    if not video_id:
        return JsonResponse({'detail': 'video_id is required (form field or X-Video-Id header).'}, status=400)

    try:
        video_uuid = UUID(str(video_id))
    except ValueError:
        return JsonResponse({'detail': 'video_id must be a valid UUID.'}, status=400)

    try:
        video = Video.objects.get(id=video_uuid)
    except Video.DoesNotExist:
        return JsonResponse({'detail': 'Video not found.'}, status=404)

    upload_file = request.FILES.get('file')
    if upload_file is None:
        return JsonResponse({'detail': 'file field is required.'}, status=400)

    object_path = f"{video.id}/{upload_file.name}"
    content_type = upload_file.content_type if hasattr(upload_file, 'content_type') else None

    try:
        public_url = upload_bytes_to_supabase(object_path, upload_file.read(), content_type)
    except Exception as exc:
        return JsonResponse({'detail': f'Upload failed: {exc}'}, status=500)

    # Update video record with playback_url and mark ready
    video.playback_url = public_url
    video.status = 'ready'
    video.save(update_fields=['playback_url', 'status', 'updated_at'])

    return JsonResponse(response_envelope('video-uploaded', {'video': video.to_dict()}), status=200)
    
def complete_upload_view(request):
    # POST /api/videos/complete/ - mark an upload complete and enqueue processing.
    if request.method != 'POST':
        return _method_not_allowed('POST')

    payload = parse_json_request(request)
    video_id_raw = payload.get('video_id')
    if not video_id_raw:
        return JsonResponse({'detail': 'video_id is required.'}, status=400)

    try:
        video_id = UUID(str(video_id_raw))
    except ValueError:
        return JsonResponse({'detail': 'video_id must be a valid UUID.'}, status=400)

    # Lookup video
    try:
        video_uuid = UUID(str(video_id_raw))
    except ValueError:
        return JsonResponse({'detail': 'video_id must be a valid UUID.'}, status=400)

    try:
        video = Video.objects.get(id=video_uuid)
    except Video.DoesNotExist:
        return JsonResponse({'detail': 'Video not found.'}, status=404)

    video.status = 'processing'
    video.save(update_fields=['status', 'updated_at'])
    return JsonResponse(
        response_envelope(
            'video-complete',
            {
                'video': video.to_dict(),
                'message': 'Upload marked complete. Processing would be queued here.',
            },
        ),
        status=200,
    )


def video_detail_view(request, video_id):
    # GET /api/videos/<video_id>/ - return the full video object and metadata.
    if request.method != 'GET':
        return _method_not_allowed('GET')

    try:
        video_uuid = UUID(str(video_id))
    except ValueError:
        return JsonResponse({'detail': 'Invalid video_id format.'}, status=400)

    try:
        video = Video.objects.get(id=video_uuid)
    except Video.DoesNotExist:
        return JsonResponse({'detail': 'Video not found.'}, status=404)

    if video.deleted_at is not None:
        return JsonResponse({'detail': 'Video not found.'}, status=404)

    # Increment view count
    video.views += 1
    video.save(update_fields=['views', 'updated_at'])

    return JsonResponse(response_envelope('video', {'video': video.to_dict()}), status=200)


def video_status_view(request, video_id):
    # GET /api/videos/<video_id>/status/ - return processing/upload status.
    if request.method != 'GET':
        return _method_not_allowed('GET')
    try:
        video_uuid = UUID(str(video_id))
    except ValueError:
        return JsonResponse({'detail': 'Invalid video_id format.'}, status=400)

    try:
        video = Video.objects.get(id=video_uuid)
    except Video.DoesNotExist:
        return JsonResponse({'detail': 'Video not found.'}, status=404)

    if video.deleted_at is not None:
        return JsonResponse({'detail': 'Video not found.'}, status=404)

    return JsonResponse(response_envelope('video-status', {'video': {'id': str(video.id), 'status': video.status}}), status=200)


@csrf_exempt
def video_view_count_view(request, video_id):
    # POST /api/videos/<video_id>/view/ - increment the view count once per play.
    if request.method != 'POST':
        return _method_not_allowed('POST')

    try:
        video_uuid = UUID(str(video_id))
    except ValueError:
        return JsonResponse({'detail': 'Invalid video_id format.'}, status=400)

    updated = Video.objects.filter(id=video_uuid, deleted_at__isnull=True).update(
        views=F('views') + 1,
        updated_at=timezone.now(),
    )
    if updated == 0:
        return JsonResponse({'detail': 'Video not found.'}, status=404)

    video = Video.objects.get(id=video_uuid)
    return JsonResponse(
        response_envelope(
            'video-view',
            {
                'video': {
                    'id': str(video.id),
                    'views': video.views,
                }
            },
        ),
        status=200,
    )


def video_update_delete_view(request, video_id):
    # PATCH / DELETE for video metadata or soft-delete
    if request.method not in ('PATCH', 'DELETE'):
        return _method_not_allowed('PATCH', 'DELETE')

    try:
        video_uuid = UUID(str(video_id))
    except ValueError:
        return JsonResponse({'detail': 'Invalid video_id format.'}, status=400)

    try:
        video = Video.objects.get(id=video_uuid)
    except Video.DoesNotExist:
        return JsonResponse({'detail': 'Video not found.'}, status=404)

    if video.deleted_at is not None:
        return JsonResponse({'detail': 'Video not found.'}, status=404)

    if request.method == 'PATCH':
        payload = parse_json_request(request)
        updated_fields = []

        if 'title' in payload:
            video.title = str(payload.get('title', video.title)).strip() or video.title
            updated_fields.append('title')
        if 'description' in payload:
            video.description = str(payload.get('description', video.description)).strip()
            updated_fields.append('description')
        if 'visibility' in payload:
            video.visibility = str(payload.get('visibility', video.visibility)).strip() or video.visibility
            updated_fields.append('visibility')
        if 'tags' in payload and isinstance(payload.get('tags'), list):
            video.tags = [str(tag).strip() for tag in payload.get('tags', []) if str(tag).strip()]
            updated_fields.append('tags')

        if updated_fields:
            updated_fields.append('updated_at')
            video.save(update_fields=updated_fields)

        return JsonResponse(response_envelope('video', {'video': video.to_dict()}), status=200)

    # DELETE - soft delete
    video.deleted_at = timezone.now()
    video.status = 'deleted'
    video.save(update_fields=['deleted_at', 'status', 'updated_at'])
    return JsonResponse({'detail': 'Video deleted.', 'video_id': str(video.id)}, status=200)
