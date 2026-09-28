"""Private evidence: where the file came from, and pictures of what is in it.

Everything here is reported under the result's `private` and `artifacts` keys,
never in `metadata`. Metadata is served publicly with the video; provenance can
carry the uploader's GPS and device, and the pictures show other people's
vehicles. The worker sends both to the backend's private store.

Nothing here can fail a job. Evidence is a bonus on top of the analysis the
video was queued for, so a missing ffprobe or an unwritable directory is
reported and skipped.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
from datetime import datetime

# Container tags worth keeping. Most are Apple's: phones write these, dashcams
# mostly do not, which is itself the signal.
PROVENANCE_TAGS = (
    'major_brand', 'compatible_brands', 'creation_time', 'encoder',
    'com.apple.quicktime.creationdate', 'com.apple.quicktime.make',
    'com.apple.quicktime.model', 'com.apple.quicktime.software',
    'com.apple.quicktime.location.ISO6709', 'location',
)

# A file written more than this long after its recording started was exported,
# trimmed or re-saved rather than copied off the card as-is.
EXPORT_GAP_SECONDS = 120

# ISO 6709 as Apple writes it: +30.2290-097.6200+150.000/
ISO6709 = re.compile(r'^([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)')

CONTACT_SHEET_MAX_TILES = 24
CONTACT_SHEET_TILE_WIDTH = 320
CONTACT_SHEET_JPEG_QUALITY = 85
SHEET_BACKGROUND = 238


# --- Provenance -------------------------------------------------------------

def run_ffprobe(path: str) -> dict | None:
    """ffprobe's JSON for the file, or None when ffprobe is not installed."""
    executable = shutil.which('ffprobe')
    if executable is None:
        return None
    completed = subprocess.run(
        [executable, '-v', 'quiet', '-print_format', 'json', '-show_format', '-show_streams', path],
        capture_output=True, text=True, timeout=60, check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f'ffprobe exited {completed.returncode}')
    return json.loads(completed.stdout or '{}')


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None


def parse_iso6709(value: str | None) -> dict | None:
    match = ISO6709.match(value or '')
    if not match:
        return None
    return {'latitude': float(match.group(1)), 'longitude': float(match.group(2))}


def summarize_provenance(probe: dict) -> dict:
    """The parts of an ffprobe report that say where a file came from.

    Pure, so it is tested against captured ffprobe output rather than videos.
    """
    fmt = probe.get('format') or {}
    format_tags = fmt.get('tags') or {}

    streams = []
    handlers = set()
    for stream in probe.get('streams') or []:
        tags = stream.get('tags') or {}
        handler = tags.get('handler_name') or ''
        if handler:
            handlers.add(handler)
        streams.append({
            key: value for key, value in {
                'type': stream.get('codec_type'),
                'codec': stream.get('codec_name'),
                'width': stream.get('width'),
                'height': stream.get('height'),
                'frame_rate': stream.get('avg_frame_rate'),
                'bit_rate': int(stream['bit_rate']) if str(stream.get('bit_rate', '')).isdigit() else None,
                'sample_rate': stream.get('sample_rate'),
                'handler_name': handler or None,
                'encoder': tags.get('encoder'),
                'creation_time': tags.get('creation_time'),
            }.items() if value not in (None, '', '0/0')  # 0/0: ffprobe's "no frame rate"
        })

    container = {tag: format_tags[tag] for tag in PROVENANCE_TAGS if format_tags.get(tag)}
    location = parse_iso6709(format_tags.get('com.apple.quicktime.location.ISO6709')
                             or format_tags.get('location'))

    return {
        'available': True,
        'format': fmt.get('format_long_name') or fmt.get('format_name'),
        'size_bytes': int(fmt['size']) if str(fmt.get('size', '')).isdigit() else None,
        'duration_seconds': float(fmt['duration']) if fmt.get('duration') else None,
        'container_tags': container,
        'streams': streams,
        'location': location,
        'hints': provenance_hints(container, sorted(handlers)),
    }


def provenance_hints(container: dict, handlers: list[str]) -> list[dict]:
    """Plain-language observations a person should act on.

    Each says what was seen and why it matters, because the useful response --
    go and preserve the original on the SD card -- depends on believing it.
    """
    hints = []

    if any('Core Media' in handler for handler in handlers):
        hints.append({
            'code': 'apple_export',
            'message': ("Written by Apple's Core Media framework, so this is a copy exported "
                        "through an iPhone or Mac, not the dashcam's own file. Preserve the "
                        "original on the dashcam's card before loop recording overwrites it."),
        })

    recorded = _parse_time(container.get('com.apple.quicktime.creationdate'))
    written = _parse_time(container.get('creation_time'))
    if recorded and written and recorded.tzinfo and written.tzinfo:
        gap = (written - recorded).total_seconds()
        if gap > EXPORT_GAP_SECONDS:
            hints.append({
                'code': 'written_after_recording',
                'message': (f'The file was written {round(gap / 60)} minutes after recording '
                            f'began ({recorded.isoformat()} -> {written.isoformat()}): '
                            f'it was saved again later, not copied as recorded.'),
                'gap_seconds': round(gap),
            })

    if container.get('com.apple.quicktime.location.ISO6709') or container.get('location'):
        hints.append({
            'code': 'embedded_location',
            'message': 'The file carries GPS coordinates. Kept private to this report.',
        })

    return hints


def provenance(path: str) -> dict:
    try:
        probe = run_ffprobe(path)
    except Exception as exc:  # a broken probe must not fail the analysis
        return {'available': False, 'reason': f'ffprobe failed: {exc}'}
    if probe is None:
        return {'available': False, 'reason': 'ffprobe is not installed on this worker'}
    return summarize_provenance(probe)


# --- Contact sheet ----------------------------------------------------------

def contact_sheet_indices(frame_count: int, fps: float, max_tiles: int = CONTACT_SHEET_MAX_TILES) -> list[int]:
    """Frames for the sheet: one a second, thinned evenly to fit the grid."""
    if frame_count <= 0:
        return []
    fps = fps if fps > 0 else 30.0
    wanted = max(1, min(max_tiles, int(math.ceil(frame_count / fps)), frame_count))
    if wanted == 1:
        return [0]
    step = (frame_count - 1) / (wanted - 1)
    return sorted({int(round(i * step)) for i in range(wanted)})


def grid_shape(tiles: int, max_columns: int = 6) -> tuple[int, int]:
    """(columns, rows), at most `max_columns` across, as square as that allows."""
    if tiles <= 0:
        return (0, 0)
    columns = min(max_columns, max(1, math.ceil(math.sqrt(tiles * 1.5))))
    return columns, math.ceil(tiles / columns)


def format_timestamp(seconds: float) -> str:
    minutes, secs = divmod(seconds, 60)
    return f'{int(minutes)}:{secs:04.1f}'


def write_sheet(frames: list, target: str, max_columns: int = 6,
                tile_width: int = CONTACT_SHEET_TILE_WIDTH) -> dict | None:
    """Tile (frame, label) pairs into one JPEG, each stamped with its label.

    Shared by the contact sheet and the burst around each candidate moment.
    Returns the artifact fields other than kind and label, or None.
    """
    import cv2
    import numpy as np

    tiles = []
    for frame, label in frames:
        height, width = frame.shape[:2]
        tile_height = max(1, round(height * tile_width / width))
        tile = cv2.resize(frame, (tile_width, tile_height), interpolation=cv2.INTER_AREA)
        # On a dark box: dashcams burn their own clock into the same corner,
        # and an outline alone left "0:00.0" printed over "16:36:21".
        (text_width, text_height), baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 1)
        top = tile_height - 10 - text_height - 4
        cv2.rectangle(tile, (4, top), (12 + text_width, tile_height - 10 + baseline), (20, 20, 20), -1)
        cv2.putText(tile, label, (8, tile_height - 10), cv2.FONT_HERSHEY_SIMPLEX,
                    0.6, (255, 255, 255), 1, cv2.LINE_AA)
        tiles.append(tile)

    if not tiles:
        return None

    columns, rows = grid_shape(len(tiles), max_columns)
    tile_height = tiles[0].shape[0]
    # Unused cells in the last row are a light neutral grey rather than black,
    # which read as missing frames on the report page.
    sheet = np.full((rows * tile_height, columns * tile_width, 3), SHEET_BACKGROUND, dtype=np.uint8)
    for position, tile in enumerate(tiles):
        row, column = divmod(position, columns)
        tile = tile[:tile_height]
        sheet[row * tile_height:row * tile_height + tile.shape[0],
              column * tile_width:(column + 1) * tile_width] = tile

    os.makedirs(os.path.dirname(target) or '.', exist_ok=True)
    if not cv2.imwrite(target, sheet, [cv2.IMWRITE_JPEG_QUALITY, CONTACT_SHEET_JPEG_QUALITY]):
        return None
    return {'path': target, 'width': int(sheet.shape[1]), 'height': int(sheet.shape[0]),
            'tiles': len(tiles), 'grid': (columns, rows)}


def contact_sheet(path: str, metadata: dict, out_dir: str) -> dict | None:
    """Evenly spaced frames across the whole clip, each stamped with its time.

    The first thing anyone reviewing an incident wants: the whole clip at a
    glance, and the second to jump to.
    """
    import cv2

    import detection

    capture = cv2.VideoCapture(path)
    if not capture.isOpened():
        return None
    try:
        indices = contact_sheet_indices(metadata.get('frame_count') or 0, metadata.get('fps') or 0.0)
        box = detection.content_box(capture, indices)
        frames = []
        for index in indices:
            capture.set(cv2.CAP_PROP_POS_FRAMES, index)
            ok, frame = capture.read()
            if ok:
                frames.append((detection.crop_to_content(frame, box),
                               format_timestamp(detection._frame_seconds(index, metadata))))
    finally:
        capture.release()

    sheet = write_sheet(frames, os.path.join(out_dir, 'contact_sheet.jpg'))
    if sheet is None:
        return None
    columns, rows = sheet.pop('grid')
    tiles = sheet.pop('tiles')
    return {**sheet, 'kind': 'contact_sheet', 'label': f'{tiles} frames, {columns}x{rows}'}


def collect(path: str, metadata: dict, out_dir: str | None, log=lambda message: None) -> tuple[dict, list[dict]]:
    """Everything private about one video: (private, artifacts)."""
    private = {'provenance': provenance(path)}
    artifacts = []

    if out_dir:
        try:
            sheet = contact_sheet(path, metadata, out_dir)
            if sheet:
                artifacts.append(sheet)
        except Exception as exc:
            log(f'Contact sheet skipped: {exc}')

    return private, artifacts
