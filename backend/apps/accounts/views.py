from django.http import JsonResponse


def bootstrap_view(request):
    # TODO: POST /api/auth/bootstrap/ - create or sync the local user after Clerk login.
    return JsonResponse({'detail': 'TODO: implement auth bootstrap.'}, status=501)


def me_view(request):
    # TODO: GET /api/auth/me/ - return the current authenticated user and profile summary.
    return JsonResponse({'detail': 'TODO: implement current user lookup.'}, status=501)


def profile_me_view(request):
    # TODO: GET /api/auth/profile/me/ or PATCH /api/auth/profile/me/ - read/update my profile.
    return JsonResponse({'detail': 'TODO: implement my profile endpoint.'}, status=501)


def profile_detail_view(request, username):
    # TODO: GET /api/auth/profiles/<username>/ - load a public profile by username.
    return JsonResponse({'detail': f'TODO: implement profile lookup for {username}.'}, status=501)
