"""Video analyzer invoked by the C# desktop worker.

Usage:
    python analyze.py <video_path>

Protocol -- one JSON object per line on stdout:

    {"type": "progress", "stage": "analyzing", "progress": 45}
    {"type": "result", "summary": "...", "tags": [...], "events": [], "metadata": {...}}

Exactly one `result` line, last. Anything human-readable goes to stderr, which
the worker surfaces in its activity log. A non-zero exit fails the job and the
worker reports stderr as the error.

Milestone 2 reports real container metadata only. Object detection arrives in
milestone 3; `events` stays empty until there is something honest to put in it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

ANALYZER_VERSION = 'metadata-1.0'


def emit(payload: dict) -> None:
    """Write one protocol line to stdout and flush.

    Flushing matters: the worker reads these as they arrive to drive its
    progress bar, and a buffered stream would deliver them all at the end.
    """
    sys.stdout.write(json.dumps(payload, separators=(',', ':')) + '\n')
    sys.stdout.flush()


def progress(stage: str, value: int) -> None:
    emit({'type': 'progress', 'stage': stage, 'progress': max(0, min(100, int(value)))})


def log(message: str) -> None:
    sys.stderr.write(message + '\n')
    sys.stderr.flush()


def probe_video(path: str) -> dict:
    """Read container metadata with OpenCV.

    Duration is derived from frame count over fps rather than trusted directly,
    because a surprising number of files report one and not the other.
    """
    import cv2  # imported late so a missing dependency is reported clearly

    capture = cv2.VideoCapture(path)
    if not capture.isOpened():
        raise RuntimeError(
            f'OpenCV could not open the video. It may be corrupt, truncated, or in an '
            f'unsupported container: {path}'
        )

    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    finally:
        capture.release()

    # A file that opens but reports nothing usable is not analysable.
    if width <= 0 or height <= 0:
        raise RuntimeError(f'Video reports no usable frame size ({width}x{height}): {path}')

    duration_seconds = round(frame_count / fps, 2) if fps > 0 and frame_count > 0 else 0.0

    return {
        'width': width,
        'height': height,
        'fps': round(fps, 3) if fps > 0 else 0.0,
        'frame_count': frame_count,
        'duration_seconds': duration_seconds,
        'file_size_bytes': os.path.getsize(path),
    }


def describe(metadata: dict) -> str:
    """A factual one-liner. No claims about content -- nothing has looked at it."""
    parts = [f"{metadata['width']}x{metadata['height']} video"]

    duration = metadata['duration_seconds']
    if duration:
        minutes, seconds = divmod(int(duration), 60)
        parts.append(f'{minutes}m {seconds:02d}s' if minutes else f'{seconds}s')

    if metadata['fps']:
        parts.append(f"{metadata['fps']:g} fps")

    return ', '.join(parts) + '. Content analysis not yet available.'


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description='Analyze a dashcam video.')
    parser.add_argument('video_path', help='Path to the video file')
    args = parser.parse_args(argv)

    started = time.monotonic()

    if not os.path.isfile(args.video_path):
        log(f'No such file: {args.video_path}')
        return 2

    progress('initializing', 5)
    log(f'Analyzing {args.video_path}')

    try:
        progress('analyzing', 30)
        metadata = probe_video(args.video_path)
    except ImportError as exc:
        log(f'Missing Python dependency: {exc}. Install the analyzer requirements.')
        return 3
    except Exception as exc:
        log(f'Failed to read the video: {exc}')
        return 4

    progress('analyzing', 80)

    metadata['analyzer_version'] = ANALYZER_VERSION
    metadata['analysis_seconds'] = round(time.monotonic() - started, 3)

    progress('uploading_results', 90)
    emit({
        'type': 'result',
        'summary': describe(metadata),
        # Tags and events stay empty until milestone 3 can derive them from the
        # actual frames. Emitting plausible guesses is what the placeholder did.
        'tags': [],
        'events': [],
        'metadata': metadata,
    })
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
