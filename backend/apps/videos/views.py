from django.http import JsonResponse


def upload_url_view(request):
    # TODO: POST /api/videos/upload-url/ - create a presigned upload target for a new video.
    return JsonResponse({'detail': 'TODO: implement upload URL endpoint.'}, status=501)


def complete_upload_view(request):
    # TODO: POST /api/videos/complete/ - mark an upload complete and enqueue processing.
    return JsonResponse({'detail': 'TODO: implement upload completion endpoint.'}, status=501)


def video_detail_view(request, video_id):
    # TODO: GET /api/videos/<video_id>/ - return the full video object and metadata.
    return JsonResponse({'detail': f'TODO: implement video detail for {video_id}.'}, status=501)


def video_status_view(request, video_id):
    # TODO: GET /api/videos/<video_id>/status/ - return processing/upload status.
    return JsonResponse({'detail': f'TODO: implement status for {video_id}.'}, status=501)


def video_update_delete_view(request, video_id):
    # TODO: PATCH /api/videos/<video_id>/manage/ and DELETE /api/videos/<video_id>/manage/ - update or remove a video.
    return JsonResponse({'detail': f'TODO: implement update/delete for {video_id}.'}, status=501)
