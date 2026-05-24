from django.http import JsonResponse

from apps.accounts.models import Profile
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

    profiles_by_clerk_id = {
        profile.clerk_user_id: profile
        for profile in Profile.objects.filter(clerk_user_id__in={video.owner_clerk_user_id for video in videos})
    }

    items = []
    for video in videos:
        item = video.to_dict()
        profile = profiles_by_clerk_id.get(video.owner_clerk_user_id)
        item['username'] = profile.username if profile else video.owner_clerk_user_id
        item['display_name'] = profile.display_name if profile else video.owner_clerk_user_id
        items.append(item)
    
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
