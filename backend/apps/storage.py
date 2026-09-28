"""Supabase Storage helper functions.

This module uploads files to Supabase Storage using the service_role key
from the environment. It returns public playback URLs for uploaded objects.
"""
from __future__ import annotations

import os
from typing import Tuple
from urllib.parse import quote

import requests

SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_SERVICE_KEY = os.getenv('SUPABASE_SERVICE_KEY') or os.getenv('SUPABASE_SERVICE_ROLE_KEY')
SUPABASE_BUCKET = os.getenv('SUPABASE_BUCKET', 'videos')
SUPABASE_PROJECT_REF = SUPABASE_URL.rstrip('/').split('://', 1)[-1].split('.', 1)[0] if SUPABASE_URL else ''


def _storage_upload_endpoint(bucket: str, object_path: str) -> str:
    if not SUPABASE_URL:
        raise RuntimeError('SUPABASE_URL is not configured')
    encoded_path = '/'.join(quote(part, safe='') for part in object_path.split('/'))
    return f"{SUPABASE_URL.rstrip('/')}/storage/v1/object/{bucket}/{encoded_path}"


def public_object_url(bucket: str, object_path: str) -> str:
    """Return the public URL for an object in a public bucket."""
    if not SUPABASE_URL:
        raise RuntimeError('SUPABASE_URL is not configured')
    return f"{SUPABASE_URL.rstrip('/')}/storage/v1/object/public/{bucket}/{object_path}"


def upload_bytes_to_supabase(object_path: str, data: bytes, content_type: str | None = None) -> str:
    """Upload bytes to the configured Supabase bucket and return the public URL.

    object_path: path inside bucket, e.g. 'videos/<uuid>/file.mp4'
    """
    if SUPABASE_SERVICE_KEY is None:
        raise RuntimeError('SUPABASE_SERVICE_KEY is not configured')

    endpoint = _storage_upload_endpoint(SUPABASE_BUCKET, object_path)
    headers = {
        'Authorization': f'Bearer {SUPABASE_SERVICE_KEY}',
    }

    files = {
        'file': (object_path, data, content_type or 'application/octet-stream'),
    }

    resp = requests.post(endpoint, headers=headers, files=files)
    try:
        resp.raise_for_status()
    except Exception as exc:
        raise RuntimeError(
            f'Failed uploading to Supabase Storage bucket "{SUPABASE_BUCKET}" '
            f'for project "{SUPABASE_PROJECT_REF}": '
            f'{resp.status_code} {resp.text}'
        ) from exc

    return public_object_url(SUPABASE_BUCKET, object_path)


# Private evidence bucket. Separate from SUPABASE_BUCKET, which is public: frames
# and crops of other people's vehicles must not be one guessable URL away. The
# bucket has to be created as *private* in Supabase; objects in it are only
# reachable through the signed URLs below.
SUPABASE_EVIDENCE_BUCKET = os.getenv('SUPABASE_EVIDENCE_BUCKET', 'evidence')

# Long enough to view a report and open its images, short enough that a link
# copied out of the page stops working the same day.
SIGNED_URL_SECONDS = 15 * 60


def upload_private_bytes(object_path: str, data: bytes, content_type: str | None = None) -> str:
    """Upload to the private evidence bucket. Returns the object path, not a URL.

    There is no lasting URL to return: callers store the path and sign it at
    read time.
    """
    if SUPABASE_SERVICE_KEY is None:
        raise RuntimeError('SUPABASE_SERVICE_KEY is not configured')

    resp = requests.post(
        _storage_upload_endpoint(SUPABASE_EVIDENCE_BUCKET, object_path),
        headers={'Authorization': f'Bearer {SUPABASE_SERVICE_KEY}'},
        files={'file': (object_path, data, content_type or 'application/octet-stream')},
    )
    if not resp.ok:
        raise RuntimeError(
            f'Failed uploading to private bucket "{SUPABASE_EVIDENCE_BUCKET}": '
            f'{resp.status_code} {resp.text}'
        )
    return object_path


def signed_object_url(object_path: str, expires_in: int = SIGNED_URL_SECONDS) -> str:
    """A time-limited URL for one object in the private evidence bucket."""
    if not SUPABASE_URL:
        raise RuntimeError('SUPABASE_URL is not configured')
    if SUPABASE_SERVICE_KEY is None:
        raise RuntimeError('SUPABASE_SERVICE_KEY is not configured')

    encoded_path = '/'.join(quote(part, safe='') for part in object_path.split('/'))
    resp = requests.post(
        f"{SUPABASE_URL.rstrip('/')}/storage/v1/object/sign/{SUPABASE_EVIDENCE_BUCKET}/{encoded_path}",
        headers={'Authorization': f'Bearer {SUPABASE_SERVICE_KEY}'},
        json={'expiresIn': expires_in},
    )
    if not resp.ok:
        raise RuntimeError(f'Could not sign {object_path}: {resp.status_code} {resp.text}')

    # Supabase answers with a path relative to /storage/v1.
    return f"{SUPABASE_URL.rstrip('/')}/storage/v1{resp.json()['signedURL']}"


def download_private_bytes(object_path: str) -> bytes:
    """Fetch one object from the private evidence bucket, server-side."""
    if not SUPABASE_URL:
        raise RuntimeError('SUPABASE_URL is not configured')
    if SUPABASE_SERVICE_KEY is None:
        raise RuntimeError('SUPABASE_SERVICE_KEY is not configured')

    encoded_path = '/'.join(quote(part, safe='') for part in object_path.split('/'))
    resp = requests.get(
        f"{SUPABASE_URL.rstrip('/')}/storage/v1/object/{SUPABASE_EVIDENCE_BUCKET}/{encoded_path}",
        headers={'Authorization': f'Bearer {SUPABASE_SERVICE_KEY}'},
        timeout=60,
    )
    if not resp.ok:
        raise RuntimeError(f'Could not fetch {object_path}: {resp.status_code}')
    return resp.content
