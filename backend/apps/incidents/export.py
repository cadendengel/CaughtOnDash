"""The evidence package: one zip an insurer or the police can be handed.

Modelled on the 2026-09-26 case folder that worked by hand: a readable
summary, the images, and SHA-256 sums for every file, so each can be checked
against what the site recorded. The video itself is not included -- the
owner uploaded it and holds it -- but its fingerprint is, so it can be
matched, and the report says where the original should be preserved.

Every automated reading is labelled as one. The summary is a starting point
for a person, and says so.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import datetime, timezone

DISCLAIMER = (
    'Automated readings -- moments, overlay values, text and plate candidates -- '
    'are produced by software and can be wrong. Each is listed with how it was '
    'obtained; check them against the images before relying on them.'
)


def _fmt(value, suffix='') -> str:
    return f'{value}{suffix}' if value not in (None, '') else '--'


def _place(lat, lon) -> str:
    if lat is None or lon is None:
        return '--'
    return f'{abs(lat):.6f}{"N" if lat >= 0 else "S"} {abs(lon):.6f}{"E" if lon >= 0 else "W"}'


def _moment_rows(moments: list[dict]) -> list[str]:
    rows = ['| Video time | Dashcam clock | Speed | Position | Score | Why |', '|---|---|---|---|---|---|']
    for moment in moments:
        seen = moment.get('overlay') or {}
        speed = f"{seen['speed']} {seen.get('speed_unit', '')}".strip() if 'speed' in seen else '--'
        rows.append('| {t:.1f}s | {clock} | {speed} | {place} | {score} | {why} |'.format(
            t=moment.get('t_seconds', 0), clock=_fmt(seen.get('clock')), speed=speed,
            place=_place(seen.get('lat'), seen.get('lon')), score=_fmt(moment.get('score')),
            why='; '.join(moment.get('reasons') or []) or '--'))
    return rows


def _plate_line(plate: dict) -> str:
    doubts = ', '.join(f"position {u['index'] + 1} read {u['read']!r}, could be {'/'.join(u['could_be'])}"
                       for u in plate.get('uncertain') or [])
    return f"- Plate candidate `{plate.get('text')}` ({plate.get('reads')} reads)" + (f' -- {doubts}' if doubts else '')


def build_markdown(video, payload: dict, files: list[tuple[str, str]], generated_at: datetime) -> str:
    """The summary. `payload` is the incident report as the endpoint serves
    it; `files` is (zip path, sha256) for everything else in the package."""
    evidence = payload.get('evidence') or {}
    lines = [
        f'# Incident evidence -- {video.title}',
        '',
        f'Generated {generated_at.isoformat(timespec="seconds")} by CaughtOnDash.',
        '',
        f'> {DISCLAIMER}',
        '',
        '## Video',
        f'- Title: {video.title}',
        f'- Uploaded file: {_fmt(evidence.get("original_filename"))}',
        f'- SHA-256 at upload: `{_fmt(evidence.get("sha256"))}`',
        f'- Analysis custody check: {_fmt(evidence.get("custody_status"))}'
        + (f' (worker read `{evidence["worker_sha256"]}`)' if evidence.get('worker_sha256') else ''),
    ]
    for hint in (evidence.get('provenance') or {}).get('hints') or []:
        lines.append(f'- Note: {hint.get("message")}')

    moments = evidence.get('moments') or {}
    overlay = evidence.get('overlay') or {}
    lines += ['', '## Timeline']
    if overlay.get('clock'):
        lines.append(f"Dashcam clock at video start: {overlay['clock']['start']} "
                     f"(read from the overlay, time zone as the dashcam was set, +/-1 s).")
    if moments.get('moments'):
        lines += ['', '### Candidate moments', *_moment_rows(moments['moments'])]
    if moments.get('possible'):
        lines += ['', '### Weaker candidates', *_moment_rows(moments['possible'])]
    if not moments.get('moments') and not moments.get('possible'):
        lines.append('No candidate moments were found.' if moments.get('available') else
                     'Moments have not been analyzed for this video.')

    photos = [a for a in payload.get('artifacts') or [] if a.get('kind') == 'photo']
    if photos:
        lines += ['', '## Photos']
    for photo in photos:
        exif = (photo.get('metadata') or {}).get('exif') or {}
        gps = exif.get('gps') or {}
        device = exif.get('device') or {}
        lines += ['', f"### {photo.get('original_filename') or photo.get('id')}",
                  f"- SHA-256: `{photo.get('sha256')}`",
                  f"- Taken: {_fmt(exif.get('taken_at'))}",
                  f"- Position: {_place(gps.get('lat'), gps.get('lon'))}"
                  + (f" (+/-{gps['accuracy_m']} m)" if 'accuracy_m' in gps else ''),
                  f"- Facing: {_fmt(gps.get('heading_deg'), ' deg')}; moving {_fmt(gps.get('speed_kmh'), ' km/h')}",
                  f"- Camera: {' '.join(filter(None, [device.get('make'), device.get('model')])) or '--'}"
                  + (f", {exif['focal_length_35mm']} mm equivalent" if exif.get('focal_length_35mm') else '')]
        text = (photo.get('metadata') or {}).get('text') or {}
        for plate in text.get('plates') or []:
            lines.append(_plate_line(plate))
        for found in text.get('identifiers') or []:
            note = ' (may be truncated)' if found.get('may_be_truncated') else ''
            lines.append(f"- Read {found['kind']}: {found['value']}{note}")
        if text.get('legible'):
            lines.append('- Other text read: ' + ', '.join(text['legible']))

    other = payload.get('other_party') or {}
    filled = {k: v for k, v in other.items() if v and k != 'updated_at'}
    if filled:
        lines += ['', '## Other party (as entered by the owner)']
        lines += [f"- {key.replace('_', ' ').capitalize()}: {value}" for key, value in filled.items()]

    notes = (payload.get('report') or {}).get('notes')
    if notes:
        lines += ['', '## Owner notes', '', notes]

    lines += ['', '## Files', '', 'SHA-256 of every file in this package is in SHA256SUMS.txt.', '']
    lines += [f'- `{path}` -- `{digest}`' for path, digest in files]
    return '\n'.join(lines) + '\n'


def _strip_urls(payload: dict) -> dict:
    """The payload without signed URLs: they expire, and would only mislead."""
    clean = json.loads(json.dumps(payload))
    for artifact in clean.get('artifacts') or []:
        artifact.pop('url', None)
        artifact.pop('original_url', None)
        artifact.pop('storage_path', None)
    clean.pop('access_log', None)
    return clean


def build_zip(video, payload: dict, fetch, now: datetime | None = None) -> tuple[bytes, list[str]]:
    """The package as bytes, and any files that could not be included.

    `fetch(storage_path) -> bytes` reads from the private bucket; it is a
    parameter so the package can be built and tested without storage.
    """
    now = now or datetime.now(timezone.utc)
    entries: list[tuple[str, bytes]] = []
    missing: list[str] = []

    for artifact in payload.get('artifacts') or []:
        path = artifact.get('storage_path')
        if not path:
            continue
        extension = path.rsplit('.', 1)[-1]
        folder = 'photos' if artifact.get('kind') == 'photo' else 'images'
        name = f"{folder}/{artifact.get('kind')}_{artifact.get('id')}.{extension}"
        try:
            data = fetch(path)
        except Exception:
            missing.append(name)
            continue
        if artifact.get('sha256') and hashlib.sha256(data).hexdigest() != artifact['sha256']:
            # Not the file that was recorded: leave it out rather than ship it
            # under a fingerprint it does not match.
            missing.append(name)
            continue
        entries.append((name, data))

    entries.append(('evidence.json', json.dumps(_strip_urls(payload), indent=2, sort_keys=True).encode()))
    digests = [(name, hashlib.sha256(data).hexdigest()) for name, data in entries]
    report = build_markdown(video, payload, digests, now)
    if missing:
        report += '\n## Not included\n\n' + '\n'.join(
            f'- `{name}` -- could not be fetched or no longer matched its recorded SHA-256' for name in missing) + '\n'
    entries.insert(0, ('report.md', report.encode()))

    sums = ''.join(f'{hashlib.sha256(data).hexdigest()}  {name}\n' for name, data in entries)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries:
            archive.writestr(name, data)
        archive.writestr('SHA256SUMS.txt', sums)
    return buffer.getvalue(), missing
