from functools import wraps
from django.http import JsonResponse
from apps.store import get_identity
from apps.accounts.models import AdminUser


def admin_required(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        identity = get_identity(request)
        clerk_user_id = identity.get('clerk_user_id')
        if not AdminUser.is_admin_for(clerk_user_id):
            return JsonResponse({'error': 'admin_required'}, status=403)
        return view_func(request, *args, **kwargs)

    return _wrapped
