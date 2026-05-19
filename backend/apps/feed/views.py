from django.http import JsonResponse

from apps.store import active_videos_for_feed, response_envelope


def feed_view(request):
    # GET /api/feed/ - return the paginated community feed.
    if request.method != 'GET':
        return JsonResponse({'detail': 'Method not allowed.', 'allowed': ['GET']}, status=405)

    videos = [video.to_dict() for video in sorted(active_videos_for_feed(), key=lambda item: item.created_at, reverse=True)]
    return JsonResponse(
        response_envelope(
            'feed',
            {
                'items': videos,
                'count': len(videos),
                'next_cursor': None,
            },
        ),
        status=200,
    )
