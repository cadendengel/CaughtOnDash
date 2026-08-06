"""Worker API authentication and authorization."""

import hashlib
import os
from functools import wraps

from django.http import JsonResponse


def hash_worker_token(token: str) -> str:
    """Hash a worker token using SHA256."""
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def get_worker_token_from_env() -> str:
    """Get the worker API token from environment variable."""
    token = os.getenv('WORKER_API_TOKEN')
    if not token:
        raise ValueError("WORKER_API_TOKEN environment variable not set")
    return token


def get_worker_token_hash() -> str:
    """Get the hash of the expected worker API token."""
    token = get_worker_token_from_env()
    return hash_worker_token(token)


def extract_bearer_token(request) -> str | None:
    """Extract bearer token from Authorization header."""
    auth_header = request.META.get('HTTP_AUTHORIZATION', '')
    if auth_header.startswith('Bearer '):
        return auth_header[7:]  # Remove 'Bearer ' prefix
    return None


def is_valid_worker_token(token: str) -> bool:
    """Check if the provided token matches the expected worker token."""
    if not token:
        return False
    token_hash = hash_worker_token(token)
    expected_hash = get_worker_token_hash()
    return token_hash == expected_hash


def worker_required(view_func):
    """Decorator to require valid worker API token on a view."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        token = extract_bearer_token(request)
        
        if not is_valid_worker_token(token):
            return JsonResponse(
                {'error': 'Unauthorized', 'detail': 'Invalid or missing worker API token.'},
                status=401
            )
        
        return view_func(request, *args, **kwargs)
    
    return wrapper
