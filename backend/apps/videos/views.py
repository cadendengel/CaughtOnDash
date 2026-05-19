from uuid import UUID

from django.http import JsonResponse

from apps.store import create_video, get_identity, get_video, now_iso, parse_json_request, response_envelope, upsert_profile


def _method_not_allowed(*allowed_methods: str) -> JsonResponse:
    return JsonResponse({'detail': 'Method not allowed.', 'allowed': list(allowed_methods)}, status=405)


def upload_url_view(request):
    # POST /api/videos/upload-url/ - create a presigned upload target for a new video.
    if request.method != 'POST':
        return _method_not_allowed('POST')

    payload = parse_json_request(request)
    identity = get_identity(request, payload)
    upsert_profile(identity, payload)
    video = create_video(identity, payload)
    return JsonResponse(
        response_envelope(
            'video-upload-url',
            {
                'video': video.to_dict(),
                'upload': {
                    'method': 'PUT',
                    'url': video.upload_url,
                    'headers': {'Content-Type': payload.get('content_type', 'video/mp4')},
                },
            },
        ),
        status=201,
    )


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

    video = get_video(video_id)
    if video is None:
        return JsonResponse({'detail': 'Video not found.'}, status=404)

    video.status = 'processing'
    video.updated_at = now_iso()
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

    video = get_video(video_id)
    if video is None or video.deleted_at is not None:
        return JsonResponse({'detail': 'Video not found.'}, status=404)

    video.views += 1
    return JsonResponse(response_envelope('video', {'video': video.to_dict()}), status=200)


def video_status_view(request, video_id):
    # GET /api/videos/<video_id>/status/ - return processing/upload status.
    if request.method != 'GET':
        return _method_not_allowed('GET')

    video = get_video(video_id)
    if video is None:
        return JsonResponse({'detail': 'Video not found.'}, status=404)

    return JsonResponse(
        response_envelope(
            'video-status',
            {
                'video_id': str(video.id),
                'status': video.status,
                'upload_status': 'complete' if video.status != 'pending' else 'pending',
                'processing_message': 'Processing not yet started.' if video.status == 'pending' else 'Ready for downstream workers.',
            },
        ),
        status=200,
    )


def video_update_delete_view(request, video_id):
    # PATCH /api/videos/<video_id>/manage/ and DELETE /api/videos/<video_id>/manage/ - update or remove a video.
    video = get_video(video_id)
    if video is None or video.deleted_at is not None:
        return JsonResponse({'detail': 'Video not found.'}, status=404)

    if request.method == 'PATCH':
        payload = parse_json_request(request)
        if 'title' in payload:
            video.title = str(payload.get('title', video.title)).strip() or video.title
        if 'description' in payload:
            video.description = str(payload.get('description', video.description)).strip()
        if 'visibility' in payload:
            video.visibility = str(payload.get('visibility', video.visibility)).strip() or video.visibility
        if 'tags' in payload and isinstance(payload.get('tags'), list):
            video.tags = [str(tag).strip() for tag in payload.get('tags', []) if str(tag).strip()]
        video.updated_at = now_iso()
        return JsonResponse(response_envelope('video', {'video': video.to_dict()}), status=200)

    if request.method == 'DELETE':
        video.deleted_at = now_iso()
        video.status = 'deleted'
        return JsonResponse({'detail': 'Video deleted.', 'video_id': str(video.id)}, status=200)

    return _method_not_allowed('PATCH', 'DELETE')
