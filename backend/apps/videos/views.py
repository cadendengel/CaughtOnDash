import os
from uuid import UUID

from django.views.decorators.csrf import csrf_exempt

from django.http import JsonResponse
from django.db.models import Count, F, Prefetch
from django.utils import timezone
from django.conf import settings
from django.db.models import TextField, Q
from django.db.models.functions import Cast

# Optional Postgres full-text search imports. We import inside a try/except
# so this file continues to work under sqlite (tests/local dev) without
# requiring Postgres-specific dependencies at import time.
try:
    from django.contrib.postgres.search import SearchVector, SearchQuery, SearchRank
    HAS_PG_SEARCH = True
except Exception:
    HAS_PG_SEARCH = False

from apps.accounts.models import Profile
from apps.accounts.models import AdminUser
from apps.accounts.auth import admin_required
from apps.videos.models import Video, VideoComment, VideoCommentLike, VideoLike
from apps.videos.models import AIAnalysis
from apps.videos.tagging import normalize_video_tags
from apps.store import get_identity, parse_json_request, response_envelope
from apps.storage import upload_bytes_to_supabase


def _method_not_allowed(*allowed_methods: str) -> JsonResponse:
    return JsonResponse({'detail': 'Method not allowed.', 'allowed': list(allowed_methods)}, status=405)


def _profile_for_clerk_user_id(clerk_user_id: str) -> Profile | None:
    try:
        return Profile.objects.get(clerk_user_id=clerk_user_id)
    except Profile.DoesNotExist:
        return None


def _serialize_comment(comment: VideoComment, liked_comment_ids: set[UUID] | None = None) -> dict:
    profile = _profile_for_clerk_user_id(comment.user_clerk_user_id)
    replies = []
    if comment.parent_comment_id is None:
        replies = [_serialize_comment(reply, liked_comment_ids) for reply in comment.replies.all()]

    return {
        'id': str(comment.id),
        'video_id': str(comment.video.id),
        'parent_comment_id': str(comment.parent_comment_id) if comment.parent_comment_id else None,
        'user_clerk_user_id': comment.user_clerk_user_id,
        'username': profile.username if profile else comment.user_clerk_user_id,
        'display_name': profile.display_name if profile else comment.user_clerk_user_id,
        'avatar_url': profile.avatar_url if profile else '',
        'text': comment.text,
        'likes_count': getattr(comment, 'likes_count', comment.likes.count()),
        'liked': bool(liked_comment_ids and comment.id in liked_comment_ids),
        'replies': replies,
        'created_at': comment.created_at.isoformat(),
        'updated_at': comment.updated_at.isoformat(),
    }


def _serialize_comment_row(row: dict) -> dict:
    profile = _profile_for_clerk_user_id(row['user_clerk_user_id'])
    return {
        'id': str(row['id']),
        'video_id': str(row['video_id']),
        'parent_comment_id': None,
        'user_clerk_user_id': row['user_clerk_user_id'],
        'username': profile.username if profile else row['user_clerk_user_id'],
        'display_name': profile.display_name if profile else row['user_clerk_user_id'],
        'avatar_url': profile.avatar_url if profile else '',
        'text': row['text'],
        'likes_count': 0,
        'liked': False,
        'replies': [],
        'created_at': row['created_at'].isoformat() if hasattr(row['created_at'], 'isoformat') else row['created_at'],
        'updated_at': row['updated_at'].isoformat() if hasattr(row['updated_at'], 'isoformat') else row['updated_at'],
    }


def _video_author_payload(video: Video) -> dict:
    profile = _profile_for_clerk_user_id(video.owner_clerk_user_id)
    return {
        'username': profile.username if profile else video.owner_clerk_user_id,
        'display_name': profile.display_name if profile else video.owner_clerk_user_id,
        'avatar_url': profile.avatar_url if profile else '',
    }


def _normalize_video_payload_tags(payload: dict, default_source: str = 'user') -> list[dict[str, str]]:
    return normalize_video_tags(payload.get('tags', []), default_source=default_source)


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
        tags=_normalize_video_payload_tags(payload, default_source='user'),
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

    if request.headers.get('X-Skip-View-Count') not in ('1', 'true', 'True'):
        # Increment view count on user-initiated opens only.
        video.views += 1
        video.save(update_fields=['views', 'updated_at'])

    current_clerk_user_id = request.headers.get('X-Clerk-User-Id') or request.GET.get('clerk_user_id') or ''
    video_data = video.to_dict()
    video_data.update(_video_author_payload(video))
    video_data['likes_count'] = VideoLike.objects.filter(video=video).count()
    video_data['comments_count'] = VideoComment.objects.filter(video=video).count()
    video_data['liked'] = bool(
        current_clerk_user_id
        and VideoLike.objects.filter(video=video, user_clerk_user_id=current_clerk_user_id).exists()
    )
    video_data['shares_count'] = 0

    return JsonResponse(response_envelope('video', {'video': video_data}), status=200)


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


def search_videos(request):
    # GET /api/videos/search/?q=...&page=1&limit=20
    if request.method != 'GET':
        return _method_not_allowed('GET')

    q = str(request.GET.get('q') or '').strip()
    try:
        page = max(1, int(request.GET.get('page', 1)))
    except Exception:
        page = 1
    try:
        limit = max(1, min(100, int(request.GET.get('limit', 20))))
    except Exception:
        limit = 20

    offset = (page - 1) * limit

    base_qs = Video.objects.filter(deleted_at__isnull=True, status='ready', visibility='public')

    if not q:
        return JsonResponse(response_envelope('video-search', {'query': q, 'count': 0, 'items': []}), status=200)

    items = []
    count = 0

    # Prefer Postgres full-text search when available and configured.
    if HAS_PG_SEARCH and 'postgres' in settings.DATABASES['default']['ENGINE']:
        vector = (
            SearchVector('title', weight='A') +
            SearchVector('description', weight='B') +
            SearchVector(Cast('tags', TextField()), weight='C')
        )
        query = SearchQuery(q)
        annotated = (
            base_qs
            .annotate(search=vector)
            .filter(search=query)
            .annotate(rank=SearchRank(vector, query))
            .order_by('-rank', '-created_at')
        )
        count = annotated.count()
        results = annotated[offset: offset + limit]
        items = [v.to_dict() for v in results]
    else:
        # SQLite or other DB fallback: use simple ILIKE/contains queries across
        # title, description and the JSON tags text.
        filtered = base_qs.filter(
            Q(title__icontains=q) | Q(description__icontains=q) | Q(tags__icontains=q)
        ).order_by('-created_at')
        count = filtered.count()
        results = filtered[offset: offset + limit]
        items = [v.to_dict() for v in results]

    return JsonResponse(response_envelope('video-search', {'query': q, 'count': count, 'items': items}), status=200)


@csrf_exempt
def video_view_count_view(request, video_id):
    # POST /api/videos/<video_id>/view/ - increment the view count once per play.
    if request.method != 'POST':
        return _method_not_allowed('POST')

    try:
        video_uuid = UUID(str(video_id))
    except ValueError:
        return JsonResponse({'detail': 'Invalid video_id format.'}, status=400)

    try:
        video = Video.objects.get(id=video_uuid, deleted_at__isnull=True)
    except Video.DoesNotExist:
        return JsonResponse({'detail': 'Video not found.'}, status=404)

    # Increment view count.
    video.views += 1
    video.save(update_fields=['views', 'updated_at'])

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


@csrf_exempt
def video_like_view(request, video_id):
    # POST /api/videos/<video_id>/like/ - toggle a like for the current user.
    if request.method != 'POST':
        return _method_not_allowed('POST')

    try:
        video_uuid = UUID(str(video_id))
    except ValueError:
        return JsonResponse({'detail': 'Invalid video_id format.'}, status=400)

    try:
        video = Video.objects.get(id=video_uuid, deleted_at__isnull=True)
    except Video.DoesNotExist:
        return JsonResponse({'detail': 'Video not found.'}, status=404)

    payload = parse_json_request(request)
    identity = get_identity(request, payload)

    like = VideoLike.objects.filter(video=video, user_clerk_user_id=identity['clerk_user_id']).first()
    liked = False

    if like is None:
        VideoLike.objects.create(video=video, user_clerk_user_id=identity['clerk_user_id'])
        liked = True
    else:
        like.delete()

    return JsonResponse(
        response_envelope(
            'video-like',
            {
                'video': {
                    'id': str(video.id),
                    'likes_count': VideoLike.objects.filter(video=video).count(),
                    'liked': liked,
                }
            },
        ),
        status=200,
    )


@csrf_exempt
def video_comments_view(request, video_id):
    # GET /api/videos/<video_id>/comments/ - list comments.
    # POST /api/videos/<video_id>/comments/ - add a comment.
    try:
        video_uuid = UUID(str(video_id))
    except ValueError:
        return JsonResponse({'detail': 'Invalid video_id format.'}, status=400)

    try:
        video = Video.objects.get(id=video_uuid, deleted_at__isnull=True)
    except Video.DoesNotExist:
        return JsonResponse({'detail': 'Video not found.'}, status=404)

    current_clerk_user_id = request.headers.get('X-Clerk-User-Id') or request.GET.get('clerk_user_id') or ''

    if request.method == 'GET':
        try:
            top_level_comments = list(
                VideoComment.objects.filter(video=video, parent_comment__isnull=True)
                .annotate(likes_count=Count('likes', distinct=True))
                .prefetch_related(
                    Prefetch(
                        'replies',
                        queryset=VideoComment.objects.annotate(likes_count=Count('likes', distinct=True)).order_by('created_at'),
                    )
                )
                .order_by('created_at')
            )

            comment_ids = {comment.id for comment in top_level_comments}
            for comment in top_level_comments:
                comment_ids.update(reply.id for reply in comment.replies.all())

            liked_comment_ids = set()
            if current_clerk_user_id and comment_ids:
                liked_comment_ids = set(
                    VideoCommentLike.objects.filter(
                        comment_id__in=comment_ids,
                        user_clerk_user_id=current_clerk_user_id,
                    ).values_list('comment_id', flat=True)
                )

            comments = [_serialize_comment(comment, liked_comment_ids) for comment in top_level_comments]
            count = len(comment_ids)
        except Exception:
            # Production fallback for deployments that have the older comment schema.
            rows = list(
                VideoComment.objects.filter(video=video)
                .order_by('created_at')
                .values('id', 'video_id', 'user_clerk_user_id', 'text', 'created_at', 'updated_at')
            )
            comments = [_serialize_comment_row(row) for row in rows]
            count = len(comments)

        return JsonResponse(
            response_envelope(
                'video-comments',
                {
                    'video_id': str(video.id),
                    'count': count,
                    'items': comments,
                },
            ),
            status=200,
        )

    if request.method == 'POST':
        payload = parse_json_request(request)
        text = str(payload.get('text') or '').strip()
        if not text:
            return JsonResponse({'detail': 'text is required.'}, status=400)

        parent_comment = None
        parent_comment_id_raw = payload.get('parent_comment_id')
        if parent_comment_id_raw:
            try:
                parent_comment_uuid = UUID(str(parent_comment_id_raw))
            except ValueError:
                return JsonResponse({'detail': 'parent_comment_id must be a valid UUID.'}, status=400)

            try:
                parent_comment = VideoComment.objects.get(
                    id=parent_comment_uuid,
                    video=video,
                    parent_comment__isnull=True,
                )
            except VideoComment.DoesNotExist:
                return JsonResponse({'detail': 'Parent comment not found.'}, status=404)

        identity = get_identity(request, payload)
        comment = VideoComment.objects.create(
            video=video,
            parent_comment=parent_comment,
            user_clerk_user_id=identity['clerk_user_id'],
            text=text,
        )

        return JsonResponse(
            response_envelope(
                'video-comment',
                {
                    'comment': _serialize_comment(comment, set()),
                },
            ),
            status=201,
        )


@csrf_exempt
def video_comment_like_view(request, comment_id):
    # POST /api/videos/comments/<comment_id>/like/ - toggle a like for a comment or reply.
    if request.method != 'POST':
        return _method_not_allowed('POST')

    try:
        comment_uuid = UUID(str(comment_id))
    except ValueError:
        return JsonResponse({'detail': 'Invalid comment_id format.'}, status=400)

    try:
        comment = VideoComment.objects.select_related('video').get(
            id=comment_uuid,
            video__deleted_at__isnull=True,
        )
    except VideoComment.DoesNotExist:
        return JsonResponse({'detail': 'Comment not found.'}, status=404)

    payload = parse_json_request(request)
    identity = get_identity(request, payload)

    like = VideoCommentLike.objects.filter(comment=comment, user_clerk_user_id=identity['clerk_user_id']).first()
    liked = False

    if like is None:
        VideoCommentLike.objects.create(comment=comment, user_clerk_user_id=identity['clerk_user_id'])
        liked = True
    else:
        like.delete()

    likes_count = VideoCommentLike.objects.filter(comment=comment).count()

    return JsonResponse(
        response_envelope(
            'video-comment-like',
            {
                'comment': {
                    'id': str(comment.id),
                    'video_id': str(comment.video.id),
                    'parent_comment_id': str(comment.parent_comment_id) if comment.parent_comment_id else None,
                    'likes_count': likes_count,
                    'liked': liked,
                }
            },
        ),
        status=200,
    )

    return _method_not_allowed('GET', 'POST')


@csrf_exempt
@admin_required
def admin_video_delete_view(request, video_id):
    # DELETE /api/admin/videos/<video_id>/ - permanently remove a video and cascaded comments/likes.
    if request.method != 'DELETE':
        return _method_not_allowed('DELETE')

    try:
        video_uuid = UUID(str(video_id))
    except ValueError:
        return JsonResponse({'detail': 'Invalid video_id format.'}, status=400)

    try:
        video = Video.objects.get(id=video_uuid)
    except Video.DoesNotExist:
        return JsonResponse({'detail': 'Video not found.'}, status=404)

    video.delete()
    return JsonResponse({'detail': 'Video deleted.', 'video_id': str(video_uuid)}, status=200)


@csrf_exempt
@admin_required
def admin_comment_delete_view(request, comment_id):
    # DELETE /api/admin/comments/<comment_id>/ - permanently remove a comment or reply.
    if request.method != 'DELETE':
        return _method_not_allowed('DELETE')

    try:
        comment_uuid = UUID(str(comment_id))
    except ValueError:
        return JsonResponse({'detail': 'Invalid comment_id format.'}, status=400)

    try:
        comment = VideoComment.objects.select_related('video').get(id=comment_uuid)
    except VideoComment.DoesNotExist:
        return JsonResponse({'detail': 'Comment not found.'}, status=404)

    comment.delete()
    return JsonResponse(
        {
            'detail': 'Comment deleted.',
            'comment_id': str(comment_uuid),
            'video_id': str(comment.video.id),
        },
        status=200,
    )


@csrf_exempt
@admin_required
def admin_video_tags_view(request, video_id):
    # PATCH /api/videos/admin/videos/<video_id>/tags/ - update a video's tags.
    if request.method != 'PATCH':
        return _method_not_allowed('PATCH')

    try:
        video_uuid = UUID(str(video_id))
    except ValueError:
        return JsonResponse({'detail': 'Invalid video_id format.'}, status=400)

    try:
        video = Video.objects.get(id=video_uuid)
    except Video.DoesNotExist:
        return JsonResponse({'detail': 'Video not found.'}, status=404)

    payload = parse_json_request(request)
    if 'tags' not in payload or not isinstance(payload.get('tags'), list):
        return JsonResponse({'detail': 'tags must be provided as a list.'}, status=400)

    video.tags = _normalize_video_payload_tags(payload, default_source='admin')
    video.save(update_fields=['tags', 'updated_at'])

    return JsonResponse(response_envelope('video-tags', {'video': video.to_dict()}), status=200)


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
            video.tags = _normalize_video_payload_tags(payload, default_source='admin')
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


def video_analysis_view(request, video_id):
    # GET /api/videos/<video_id>/analysis/ - return AIAnalysis objects for a video (latest first)
    if request.method != 'GET':
        return _method_not_allowed('GET')

    try:
        video_uuid = UUID(str(video_id))
    except ValueError:
        return JsonResponse({'detail': 'Invalid video_id format.'}, status=400)

    try:
        video = Video.objects.get(id=video_uuid, deleted_at__isnull=True)
    except Video.DoesNotExist:
        return JsonResponse({'detail': 'Video not found.'}, status=404)

    analyses = list(AIAnalysis.objects.filter(video=video).order_by('-created_at').values('id', 'schema_version', 'generated_by', 'analysis', 'created_at'))

    return JsonResponse(response_envelope('video-analysis', {'video_id': str(video.id), 'analyses': analyses}), status=200)


@csrf_exempt
def video_reanalyze_view(request, video_id):
    # POST /api/videos/<video_id>/analysis/reanalyze/ - request re-analysis (owner or admin)
    if request.method != 'POST':
        return _method_not_allowed('POST')

    clerk_user_id = request.headers.get('X-Clerk-User-Id') or ''

    try:
        video_uuid = UUID(str(video_id))
    except ValueError:
        return JsonResponse({'detail': 'Invalid video_id format.'}, status=400)

    try:
        video = Video.objects.get(id=video_uuid, deleted_at__isnull=True)
    except Video.DoesNotExist:
        return JsonResponse({'detail': 'Video not found.'}, status=404)

    # Only the owner or an admin may request re-analysis
    is_admin = AdminUser.is_admin_for(clerk_user_id)
    is_owner = clerk_user_id and clerk_user_id == (video.owner_clerk_user_id or '')
    if not (is_admin or is_owner):
        return JsonResponse({'detail': 'Forbidden.'}, status=403)

    # Remove all AI-sourced tags so the worker will pick it up again
    tags = normalize_video_tags(video.tags or [], default_source='user')
    tags_without_ai = [t for t in tags if t.get('source') != 'ai']
    video.tags = tags_without_ai
    # Mark as ready for reprocessing
    video.status = 'ready'
    video.save(update_fields=['tags', 'status', 'updated_at'])

    return JsonResponse(response_envelope('video-reanalyze', {'video_id': str(video.id), 'message': 'Re-analysis requested.'}), status=200)
