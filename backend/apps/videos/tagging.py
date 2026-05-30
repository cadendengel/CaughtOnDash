"""Helpers for normalizing and serializing video tags."""

from __future__ import annotations

from typing import Any


VALID_TAG_SOURCES = {'user', 'admin', 'ai'}


def normalize_tag_text(value: Any) -> str:
    return str(value or '').strip()


def normalize_video_tags(tags: Any, default_source: str = 'user') -> list[dict[str, str]]:
    source = default_source if default_source in VALID_TAG_SOURCES else 'user'
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()

    if not isinstance(tags, list):
        return normalized

    for raw_tag in tags:
        if isinstance(raw_tag, dict):
            text = normalize_tag_text(raw_tag.get('text') or raw_tag.get('name') or raw_tag.get('label'))
            tag_source = str(raw_tag.get('source') or source).strip().lower()
        else:
            text = normalize_tag_text(raw_tag)
            tag_source = source

        if not text:
            continue

        if tag_source not in VALID_TAG_SOURCES:
            tag_source = source

        dedupe_key = text.lower()
        if dedupe_key in seen:
            continue

        seen.add(dedupe_key)
        normalized.append({'text': text, 'source': tag_source})

    return normalized


def serialize_video_tag(tag: Any, default_source: str = 'user') -> dict[str, str]:
    if isinstance(tag, dict):
        text = normalize_tag_text(tag.get('text') or tag.get('name') or tag.get('label'))
        source = str(tag.get('source') or default_source).strip().lower()
    else:
        text = normalize_tag_text(tag)
        source = default_source

    if source not in VALID_TAG_SOURCES:
        source = default_source if default_source in VALID_TAG_SOURCES else 'user'

    return {'text': text, 'source': source}


def serialize_video_tags(tags: Any) -> list[dict[str, str]]:
    return [serialize_video_tag(tag) for tag in normalize_video_tags(tags)]
