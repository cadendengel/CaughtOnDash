"""Clerk session-token verification.

Clerk signs session tokens with RS256 and publishes the matching public keys at
``<issuer>/.well-known/jwks.json``. We fetch that document, cache it, and verify
the signature, issuer and expiry locally -- no call to Clerk on the request path
once the keys are cached.

Only the ``sub`` claim is treated as authoritative identity. Clerk's default
session token does not carry email or name (those require a custom JWT
template), so profile fields continue to come from the request body/headers.
Those are display data, not authorization input -- see get_identity().
"""

from __future__ import annotations

import threading
import time

import jwt
import requests
from django.conf import settings
from jwt import PyJWKClient

# Clerk keys rotate rarely; an hour keeps us fresh without hammering the JWKS
# endpoint. A cache miss on an unknown kid forces an immediate refetch.
JWKS_CACHE_SECONDS = 3600
JWKS_REQUEST_TIMEOUT_SECONDS = 5

_jwks_lock = threading.Lock()
_jwks_client: PyJWKClient | None = None
_jwks_client_issuer: str = ''
_jwks_fetched_at: float = 0.0


class ClerkTokenError(Exception):
    """Raised when a token is missing, malformed, or fails verification."""


def get_issuer() -> str:
    issuer = (getattr(settings, 'CLERK_ISSUER', '') or '').strip().rstrip('/')
    if not issuer:
        raise ClerkTokenError('CLERK_ISSUER is not configured.')
    return issuer


def get_jwks_url() -> str:
    return f'{get_issuer()}/.well-known/jwks.json'


def reset_jwks_cache() -> None:
    """Drop the cached signing keys. Used by tests and after a rotation."""
    global _jwks_client, _jwks_client_issuer, _jwks_fetched_at
    with _jwks_lock:
        _jwks_client = None
        _jwks_client_issuer = ''
        _jwks_fetched_at = 0.0


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client, _jwks_client_issuer, _jwks_fetched_at

    issuer = get_issuer()
    now = time.monotonic()

    with _jwks_lock:
        is_stale = (now - _jwks_fetched_at) > JWKS_CACHE_SECONDS
        issuer_changed = _jwks_client_issuer != issuer

        if _jwks_client is None or is_stale or issuer_changed:
            _jwks_client = PyJWKClient(
                get_jwks_url(),
                cache_keys=True,
                timeout=JWKS_REQUEST_TIMEOUT_SECONDS,
            )
            _jwks_client_issuer = issuer
            _jwks_fetched_at = now

        return _jwks_client


def extract_bearer_token(request) -> str | None:
    """Pull the token out of an Authorization: Bearer <token> header."""
    header = request.META.get('HTTP_AUTHORIZATION', '')
    if not header.startswith('Bearer '):
        return None

    token = header[len('Bearer '):].strip()
    return token or None


def verify_token(token: str) -> dict:
    """Verify a Clerk session token and return its claims.

    Raises ClerkTokenError on any failure. Callers must not fall back to
    unverified data when this raises.
    """
    if not token:
        raise ClerkTokenError('No token provided.')

    issuer = get_issuer()

    try:
        signing_key = _get_jwks_client().get_signing_key_from_jwt(token)
    except requests.RequestException as exc:
        raise ClerkTokenError(f'Could not reach the Clerk JWKS endpoint: {exc}') from exc
    except Exception as exc:
        raise ClerkTokenError(f'No usable signing key for this token: {exc}') from exc

    try:
        claims = jwt.decode(
            token,
            signing_key.key,
            # Pinning RS256 is what blocks 'alg: none' and HS256-signed-with-the
            # -public-key confusion attacks.
            algorithms=['RS256'],
            issuer=issuer,
            options={
                'require': ['exp', 'iat', 'sub'],
                'verify_signature': True,
                'verify_exp': True,
                'verify_iss': True,
                # Clerk session tokens carry no 'aud' by default.
                'verify_aud': False,
            },
        )
    except jwt.ExpiredSignatureError as exc:
        raise ClerkTokenError('Token has expired.') from exc
    except jwt.InvalidIssuerError as exc:
        raise ClerkTokenError('Token issuer does not match CLERK_ISSUER.') from exc
    except jwt.InvalidTokenError as exc:
        raise ClerkTokenError(f'Token failed verification: {exc}') from exc

    subject = str(claims.get('sub') or '').strip()
    if not subject:
        raise ClerkTokenError('Token has no subject.')

    return claims


def verified_clerk_user_id(request) -> str | None:
    """Return the Clerk user id from a verified token, or None if absent/invalid.

    Never raises -- callers decide what an unverified request means, based on
    REQUIRE_CLERK_JWT.
    """
    token = extract_bearer_token(request)
    if not token:
        return None

    try:
        return str(verify_token(token)['sub'])
    except ClerkTokenError:
        return None
