from django.http import JsonResponse
from django.db.models import Count

from apps.accounts.models import Profile
from apps.videos.models import Video, VideoLike
from apps.videos.tagging import serialize_video_tags
from apps.store import current_clerk_user_id as resolve_current_clerk_user_id, response_envelope


def _fallback_identity_display(clerk_user_id: str) -> tuple[str, str]:
    normalized = str(clerk_user_id or '').strip()
    if not normalized:
        return ('dash_user', 'Dash User')

    safe_tail = normalized.split('_')[-1][-6:].lower()
    if not safe_tail:
        return ('dash_user', 'Dash User')

    return (f'dash_{safe_tail}', f'Dash User {safe_tail.upper()}')


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
        'views',
        'tags',
        'created_at',
        'updated_at',
        'deleted_at',
        # The feed omitted analysis state entirely, so cards could not show an
        # analysis badge and the owner Re-analyze button never rendered.
        'analysis_status',
        'analysis_stage',
        'analysis_progress',
        'worker_last_seen_at',
        'ai_summary',
        'ai_tags',
    ).annotate(
        likes_count=Count('likes', distinct=True),
        comments_count=Count('comments', distinct=True),
    ).order_by('-created_at')[:100])  # Limit to 100 for now

    current_clerk_user_id = resolve_current_clerk_user_id(request)
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
        fallback_username, fallback_display_name = _fallback_identity_display(video['owner_clerk_user_id'])
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
            'views': video.get('views', 0),
            # Tags go through the shared serializer so the feed emits the same
            # {text, source} shape as search and detail.
            'tags': serialize_video_tags(video['tags']),
            'analysis_status': video['analysis_status'],
            'analysis_stage': video['analysis_stage'],
            'analysis_progress': video['analysis_progress'],
            'worker_last_seen_at': video['worker_last_seen_at'].isoformat() if video['worker_last_seen_at'] else None,
            'ai_summary': video['ai_summary'],
            'ai_tags': video['ai_tags'],
            'created_at': video['created_at'].isoformat() if hasattr(video['created_at'], 'isoformat') else video['created_at'],
            'updated_at': video['updated_at'].isoformat() if hasattr(video['updated_at'], 'isoformat') else video['updated_at'],
            'deleted_at': video['deleted_at'].isoformat() if hasattr(video['deleted_at'], 'isoformat') else video['deleted_at'],
            'username': profile.username if profile else fallback_username,
            'display_name': profile.display_name if profile else fallback_display_name,
            'avatar_url': profile.avatar_url if profile else '',
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
