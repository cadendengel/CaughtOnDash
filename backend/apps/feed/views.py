from django.http import JsonResponse


def feed_view(request):
    # TODO: GET /api/feed/ - return the paginated community feed.
    return JsonResponse({'detail': 'TODO: implement feed endpoint.'}, status=501)
