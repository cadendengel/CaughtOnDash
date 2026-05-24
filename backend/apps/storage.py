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
        raise RuntimeError(f'Failed uploading to Supabase Storage: {resp.status_code} {resp.text}') from exc

    return public_object_url(SUPABASE_BUCKET, object_path)
