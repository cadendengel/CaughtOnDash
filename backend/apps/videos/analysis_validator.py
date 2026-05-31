from __future__ import annotations

from typing import Any


def _is_iso_datetime(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    return 'T' in value and ('+' in value or value.endswith('Z'))


def validate_analysis(payload: Any) -> None:
    if not isinstance(payload, dict):
        raise ValueError('Analysis payload must be a JSON object.')

    schema_version = payload.get('schema_version')
    if not isinstance(schema_version, str) or '.' not in schema_version:
        raise ValueError('schema_version must be a version string such as 1.0.')

    events = payload.get('events')
    if not isinstance(events, list):
        raise ValueError('events must be a list.')

    for event in events:
        if not isinstance(event, dict):
            raise ValueError('Each event must be an object.')

        label = event.get('label')
        start_time_seconds = event.get('start_time_seconds')
        if not isinstance(label, str) or not label.strip():
            raise ValueError('Each event must include a non-empty label.')
        if not isinstance(start_time_seconds, (int, float)) or start_time_seconds < 0:
            raise ValueError('Each event must include a non-negative start_time_seconds value.')

        if 'end_time_seconds' in event:
            end_time_seconds = event['end_time_seconds']
            if not isinstance(end_time_seconds, (int, float)) or end_time_seconds < 0:
                raise ValueError('end_time_seconds must be non-negative when provided.')

        if 'confidence' in event:
            confidence = event['confidence']
            if not isinstance(confidence, (int, float)) or confidence < 0 or confidence > 1:
                raise ValueError('confidence must be between 0 and 1 when provided.')

    generated_at = payload.get('generated_at')
    if generated_at is not None and not _is_iso_datetime(generated_at):
        raise ValueError('generated_at must be an ISO timestamp when provided.')

    metadata = payload.get('metadata')
    if metadata is not None and not isinstance(metadata, dict):
        raise ValueError('metadata must be an object when provided.')
