"""Shared in-memory state for the backend starter.

This keeps the starter endpoints functional before the database models are
introduced. Replace this module with real models once the project is ready.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_json_request(request) -> dict[str, Any]:
    if not request.body:
        return {}

    try:
        data = json.loads(request.body.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError, ValueError):
        return {}

    return data if isinstance(data, dict) else {}


def response_envelope(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {'kind': kind, **payload}


def _header_value(request, *names: str) -> str:
    for name in names:
        value = request.headers.get(name)
        if value:
            return value.strip()
    return ''


def get_identity(request, payload: dict[str, Any] | None = None) -> dict[str, str]:
    payload = payload or {}
    clerk_user_id = (
        payload.get('clerk_user_id')
        or request.GET.get('clerk_user_id')
        or _header_value(request, 'X-Clerk-User-Id', 'Clerk-User-Id')
        or 'demo-user'
    )
    email = (
        payload.get('email')
        or request.GET.get('email')
        or _header_value(request, 'X-Clerk-Email', 'Clerk-Email')
        or f'{clerk_user_id}@example.com'
    )
    email_local_part = email.split('@', 1)[0].strip().lower().replace(' ', '_')
    display_name = (
        payload.get('display_name')
        or request.GET.get('display_name')
        or _header_value(request, 'X-Clerk-Name', 'Clerk-Name')
        or email_local_part
        or 'Dash User'
    )
    username = (
        payload.get('username')
        or request.GET.get('username')
        or email_local_part
        or f"{display_name.lower().replace(' ', '_')}_{clerk_user_id.replace('-', '_')[:8]}"
    )
    avatar_url = payload.get('avatar_url') or request.GET.get('avatar_url') or ''

    return {
        'clerk_user_id': clerk_user_id,
        'email': email,
        'display_name': display_name,
        'username': username,
        'avatar_url': avatar_url,
    }


@dataclass
class Profile:
    clerk_user_id: str
    email: str
    username: str
    display_name: str
    avatar_url: str = ''
    bio: str = ''
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            'clerk_user_id': self.clerk_user_id,
            'email': self.email,
            'username': self.username,
            'display_name': self.display_name,
            'avatar_url': self.avatar_url,
            'bio': self.bio,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
        }


@dataclass
class Video:
    id: UUID
    owner_clerk_user_id: str
    title: str
    description: str
    visibility: str = 'public'
    status: str = 'pending'
    original_filename: str = ''
    upload_url: str = ''
    playback_url: str = ''
    thumbnail_url: str = ''
    duration_seconds: int = 0
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    deleted_at: str | None = None
    tags: list[str] = field(default_factory=list)
    views: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            'id': str(self.id),
            'owner_clerk_user_id': self.owner_clerk_user_id,
            'title': self.title,
            'description': self.description,
            'visibility': self.visibility,
            'status': self.status,
            'original_filename': self.original_filename,
            'upload_url': self.upload_url,
            'playback_url': self.playback_url,
            'thumbnail_url': self.thumbnail_url,
            'duration_seconds': self.duration_seconds,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'deleted_at': self.deleted_at,
            'tags': self.tags,
            'views': self.views,
        }


PROFILES: dict[str, Profile] = {}
VIDEOS: dict[UUID, Video] = {}


def upsert_profile(identity: dict[str, str], payload: dict[str, Any] | None = None) -> Profile:
    payload = payload or {}
    profile = PROFILES.get(identity['clerk_user_id'])
    if profile is None:
        profile = Profile(
            clerk_user_id=identity['clerk_user_id'],
            email=identity['email'],
            username=identity['username'],
            display_name=identity['display_name'],
            avatar_url=identity['avatar_url'],
            bio=str(payload.get('bio', '')).strip(),
        )
        PROFILES[profile.clerk_user_id] = profile
        return profile

    profile.email = identity['email'] or profile.email
    profile.username = identity['username'] or profile.username
    profile.display_name = identity['display_name'] or profile.display_name
    profile.avatar_url = identity['avatar_url'] or profile.avatar_url
    if 'bio' in payload:
        profile.bio = str(payload.get('bio', '')).strip()
    profile.updated_at = now_iso()
    return profile


def get_profile_by_username(username: str) -> Profile | None:
    for profile in PROFILES.values():
        if profile.username == username:
            return profile
    return None


def get_or_create_demo_profile() -> Profile:
    identity = {
        'clerk_user_id': 'demo-user',
        'email': 'demo-user@example.com',
        'display_name': 'Dash User',
        'username': 'dash_user',
        'avatar_url': '',
    }
    return upsert_profile(identity)


def create_video(identity: dict[str, str], payload: dict[str, Any]) -> Video:
    video = Video(
        id=uuid4(),
        owner_clerk_user_id=identity['clerk_user_id'],
        title=str(payload.get('title') or 'Untitled dashcam clip').strip(),
        description=str(payload.get('description') or '').strip(),
        visibility=str(payload.get('visibility') or 'public').strip() or 'public',
        status='pending',
        original_filename=str(payload.get('original_filename') or payload.get('filename') or ''),
        tags=[str(tag).strip() for tag in payload.get('tags', []) if str(tag).strip()] if isinstance(payload.get('tags', []), list) else [],
    )
    video.upload_url = f'https://upload.local/{video.id}'
    video.playback_url = f'https://stream.local/{video.id}.m3u8'
    video.thumbnail_url = f'https://thumb.local/{video.id}.jpg'
    video.updated_at = now_iso()
    VIDEOS[video.id] = video
    return video


def get_video(video_id: UUID) -> Video | None:
    return VIDEOS.get(video_id)


def active_videos_for_feed() -> list[Video]:
    return [video for video in VIDEOS.values() if video.deleted_at is None and video.visibility != 'private']
