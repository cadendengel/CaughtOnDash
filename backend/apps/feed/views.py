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
    videos = list(Video.objects.filter(
        visibility='public',
        deleted_at__isnull=True,
    ).values(
        'id',
        'owner_clerk_user_id',
        'title',
        'description',
        'visibility',
        'status',
        'original_filename',
        'upload_url',
        'playback_url',
        'thumbnail_url',
        'duration_seconds',
        'tags',
        'created_at',
        'updated_at',
        'deleted_at',
    ).annotate(
        likes_count=Count('likes', distinct=True),
        comments_count=Count('comments', distinct=True),
    ).order_by('-created_at')[:100])  # Limit to 100 for now

    current_clerk_user_id = request.headers.get('X-Clerk-User-Id') or request.GET.get('clerk_user_id') or ''
    liked_video_ids = set()
    if current_clerk_user_id:
        liked_video_ids = set(
            VideoLike.objects.filter(
                user_clerk_user_id=current_clerk_user_id,
                video_id__in=[video['id'] for video in videos],
            ).values_list('video_id', flat=True)
        )

    profiles_by_clerk_id = {
        profile.clerk_user_id: profile
        for profile in Profile.objects.filter(clerk_user_id__in={video['owner_clerk_user_id'] for video in videos})
    }

    items = []
    for video in videos:
        profile = profiles_by_clerk_id.get(video['owner_clerk_user_id'])
        item = {
            'id': str(video['id']),
            'owner_clerk_user_id': video['owner_clerk_user_id'],
            'title': video['title'],
            'description': video['description'],
            'visibility': video['visibility'],
            'status': video['status'],
            'original_filename': video['original_filename'],
            'upload_url': video['upload_url'],
            'playback_url': video['playback_url'],
            'thumbnail_url': video['thumbnail_url'],
            'duration_seconds': video['duration_seconds'],
            'tags': video['tags'],
            'created_at': video['created_at'].isoformat() if hasattr(video['created_at'], 'isoformat') else video['created_at'],
            'updated_at': video['updated_at'].isoformat() if hasattr(video['updated_at'], 'isoformat') else video['updated_at'],
            'deleted_at': video['deleted_at'].isoformat() if hasattr(video['deleted_at'], 'isoformat') else video['deleted_at'],
            'username': profile.username if profile else video['owner_clerk_user_id'],
            'display_name': profile.display_name if profile else video['owner_clerk_user_id'],
            'likes_count': video.get('likes_count', 0),
            'comments_count': video.get('comments_count', 0),
            'liked': video['id'] in liked_video_ids,
        }
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
