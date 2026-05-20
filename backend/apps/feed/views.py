from django.http import JsonResponse

from apps.videos.models import Video
from apps.store import response_envelope


def feed_view(request):
    # GET /api/feed/ - return the paginated community feed.
    if request.method != 'GET':
        return JsonResponse({'detail': 'Method not allowed.', 'allowed': ['GET']}, status=405)

    # Fetch public, non-deleted videos ordered by creation date (most recent first)
    videos = Video.objects.filter(
        visibility='public',
        deleted_at__isnull=True,
    ).order_by('-created_at')[:100]  # Limit to 100 for now

    items = [video.to_dict() for video in videos]
    
    return JsonResponse(
        response_envelope(
            'feed',
            {
                'items': items,
                'count': len(items),
                'next_cursor': None,
            },
        ),
        status=200,
    )
