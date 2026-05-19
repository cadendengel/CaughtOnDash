from django.http import JsonResponse

from apps.store import get_identity, get_profile_by_username, parse_json_request, response_envelope, upsert_profile


def _method_not_allowed(*allowed_methods: str) -> JsonResponse:
    return JsonResponse({'detail': 'Method not allowed.', 'allowed': list(allowed_methods)}, status=405)


def bootstrap_view(request):
    # POST /api/auth/bootstrap/ - create or sync the local user after Clerk login.
    if request.method != 'POST':
        return _method_not_allowed('POST')

    payload = parse_json_request(request)
    identity = get_identity(request, payload)
    profile = upsert_profile(identity, payload)
    return JsonResponse(response_envelope('bootstrap', {'profile': profile.to_dict()}), status=200)


def me_view(request):
    # GET /api/auth/me/ - return the current authenticated user and profile summary.
    if request.method != 'GET':
        return _method_not_allowed('GET')

    identity = get_identity(request)
    profile = upsert_profile(identity)
    return JsonResponse(
        response_envelope(
            'me',
            {
                'profile': profile.to_dict(),
                'is_authenticated': True,
            },
        ),
        status=200,
    )


def profile_me_view(request):
    # GET /api/auth/profile/me/ or PATCH /api/auth/profile/me/ - read/update my profile.
    if request.method == 'GET':
        identity = get_identity(request)
        profile = upsert_profile(identity)
        return JsonResponse(response_envelope('profile', {'profile': profile.to_dict()}), status=200)

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
