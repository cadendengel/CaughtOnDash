import re

from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse

from apps.accounts.models import AdminUser, Profile
from apps.store import (
    MalformedJSON,
    _looks_like_clerk_identifier,
    get_identity,
    is_authenticated,
    parse_json_request,
    parse_json_request_strict,
    response_envelope,
)


def _method_not_allowed(*allowed_methods: str) -> JsonResponse:
    return JsonResponse({'detail': 'Method not allowed.', 'allowed': list(allowed_methods)}, status=405)


def _is_id_derived(value: str | None, clerk_user_id: str) -> bool:
    """True when `value` is an id-derived placeholder rather than a real name.

    get_identity falls back to placeholders built from the Clerk id when the
    caller sends no display fields: the display_name becomes the id verbatim
    (e.g. 'user_3Hf...'), and the username becomes the id with separators
    stripped ('user3hf...'). Both must be recognised so an upsert never
    overwrites a good profile with one (QA ISSUE-3).
    """
    if not value:
        return True
    v = str(value).strip().lower()
    cid = str(clerk_user_id).strip().lower()
    if _looks_like_clerk_identifier(v):
        return True
    return v == cid or v == re.sub(r'[^a-z0-9]', '', cid)


def upsert_profile(identity: dict, payload: dict | None = None) -> Profile:
    """Create or update a Profile from Clerk identity and optional payload.

    On create, seed every field from the identity. On update, only accept a new
    username/display_name when it is a real value -- never one derived from the
    Clerk id. get_identity falls back to id-derived placeholders when the caller
    supplies no display fields, and a content endpoint (e.g. posting a video)
    that re-upserts must not use those placeholders to overwrite the good name
    the user already has. Overwriting it downgraded every poster's handle to
    their raw Clerk id (QA ISSUE-3).
    """
    profile, created = Profile.objects.get_or_create(
        clerk_user_id=identity['clerk_user_id'],
        defaults={
            'email': identity.get('email') or '',
            'username': identity.get('username') or '',
            'display_name': identity.get('display_name') or '',
            'avatar_url': identity.get('avatar_url') or '',
            'bio': str((payload or {}).get('bio', '')).strip() if payload is not None else '',
        },
    )
    if created:
        return profile

    cid = identity['clerk_user_id']
    dirty = False
    # Real values only: skip anything derived from the Clerk id.
    username = identity.get('username')
    if username and not _is_id_derived(username, cid) and username != profile.username:
        profile.username = username
        dirty = True
    display_name = identity.get('display_name')
    if display_name and not _is_id_derived(display_name, cid) and display_name != profile.display_name:
        profile.display_name = display_name
        dirty = True
    email = identity.get('email')
    if email and email != profile.email:
        profile.email = email
        dirty = True
    avatar_url = identity.get('avatar_url')
    if avatar_url and avatar_url != profile.avatar_url:
        profile.avatar_url = avatar_url
        dirty = True
    if payload is not None and 'bio' in payload:
        profile.bio = str(payload.get('bio', '')).strip()
        dirty = True
    if dirty:
        profile.save()
    return profile


def get_profile_by_username(username: str) -> Profile | None:
    try:
        return Profile.objects.get(username=username)
    except Profile.DoesNotExist:
        return None


def _profile_summary_from_identity(identity: dict) -> dict:
    profile = Profile.objects.filter(clerk_user_id=identity['clerk_user_id']).first()
    if profile is not None:
        return profile.to_dict()

    return {
        'clerk_user_id': identity['clerk_user_id'],
        'email': identity.get('email', ''),
        'username': identity.get('username', ''),
        'display_name': identity.get('display_name', ''),
        'avatar_url': identity.get('avatar_url', ''),
        'bio': '',
        'created_at': '',
        'updated_at': '',
    }


@csrf_exempt
def bootstrap_view(request):
    # POST /api/auth/bootstrap/ - create or sync the local user after Clerk login.
    if request.method != 'POST':
        return _method_not_allowed('POST')

    try:
        payload = parse_json_request_strict(request)
    except MalformedJSON as exc:
        return JsonResponse({'detail': str(exc)}, status=400)

    identity = get_identity(request, payload)
    if not is_authenticated(identity):
        # Without this, an anonymous caller creates/overwrites one shared
        # blank-id profile that every other anonymous /me reads back (QA ISSUE-1).
        return JsonResponse({'detail': 'Authentication required.'}, status=401)

    profile = upsert_profile(identity, payload)
    return JsonResponse(response_envelope('bootstrap', {'profile': profile.to_dict()}), status=200)


def me_view(request):
    # GET /api/auth/me/ - return the current authenticated user and profile summary.
    if request.method != 'GET':
        return _method_not_allowed('GET')

    identity = get_identity(request)
    return JsonResponse(
        response_envelope(
            'me',
            {
                'profile': _profile_summary_from_identity(identity),
                'is_admin': AdminUser.is_admin_for(identity['clerk_user_id']),
            },
        ),
        status=200,
    )


def profile_me_view(request):
    # GET /api/auth/profile/me/ or PATCH /api/auth/profile/me/ - read/update my profile.
    if request.method == 'GET':
        identity = get_identity(request)
        return JsonResponse(response_envelope('profile', {'profile': _profile_summary_from_identity(identity)}), status=200)

    if request.method == 'PATCH':
        payload = parse_json_request(request)
        identity = get_identity(request, payload)
        profile = upsert_profile(identity, payload)
        return JsonResponse(response_envelope('profile', {'profile': profile.to_dict()}), status=200)

    return _method_not_allowed('GET', 'PATCH')


def profile_detail_view(request, username):
    # GET /api/auth/profiles/<username>/ - load a public profile by username.
    if request.method != 'GET':
        return _method_not_allowed('GET')

    profile = get_profile_by_username(username)
    if profile is None:
        return JsonResponse({'detail': f'Profile {username} not found.'}, status=404)

    return JsonResponse(response_envelope('profile', {'profile': profile.to_dict()}), status=200)
