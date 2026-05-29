from django.http import JsonResponse
from django.db.models import Count

from apps.accounts.models import Profile
from apps.videos.models import Video, VideoLike
from apps.store import response_envelope


def feed_view(request):
    # GET /api/feed/ - return the paginated community feed.
    if request.method != 'GET':
        return JsonResponse({'detail': 'Method not allowed.', 'allowed': ['GET']}, status=405)

    # Fetch public, non-deleted videos ordered by creation date (most recent first)
    videos = Video.objects.filter(
        visibility='public',
        deleted_at__isnull=True,
    ).annotate(
        likes_count=Count('likes', distinct=True),
        comments_count=Count('comments', distinct=True),
    ).order_by('-created_at')[:100]  # Limit to 100 for now

    current_clerk_user_id = request.headers.get('X-Clerk-User-Id') or request.GET.get('clerk_user_id') or ''
    liked_video_ids = set()
    if current_clerk_user_id:
        liked_video_ids = set(
            VideoLike.objects.filter(
                user_clerk_user_id=current_clerk_user_id,
                video__in=videos,
            ).values_list('video_id', flat=True)
        )

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
        item['likes_count'] = getattr(video, 'likes_count', 0)
        item['comments_count'] = getattr(video, 'comments_count', 0)
        item['liked'] = video.id in liked_video_ids
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
