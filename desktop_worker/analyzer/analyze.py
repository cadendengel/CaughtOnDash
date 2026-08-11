"""Video analyzer invoked by the C# desktop worker.

Usage:
    python analyze.py <video_path>

Protocol -- one JSON object per line on stdout:

    {"type": "progress", "stage": "analyzing", "progress": 45}
    {"type": "result", "summary": "...", "tags": [...], "events": [...], "metadata": {...}}

Exactly one `result` line, last. Anything human-readable goes to stderr, which
the worker surfaces in its activity log. A non-zero exit fails the job and the
worker reports stderr as the error.

Milestone 2 reports real container metadata only. Object detection arrives in
milestone 3. `events` carries one entry per detected label, at the moment it
was first seen -- an observation, not an incident.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

ANALYZER_VERSION = 'detect-2.0'


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
    """Metadata-only fallback, used when detection is unavailable."""
    parts = [f"{metadata['width']}x{metadata['height']} video"]

    duration = metadata['duration_seconds']
    if duration:
        minutes, seconds = divmod(int(duration), 60)
        parts.append(f'{minutes}m {seconds:02d}s' if minutes else f'{seconds}s')

    if metadata['fps']:
        parts.append(f"{metadata['fps']:g} fps")

    return ', '.join(parts) + '. Object detection was not run.'


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description='Analyze a dashcam video.')
    # Prints the analyzer version and exits. The worker calls this to learn the
    # current version so its "requeue outdated" button can ask the backend to
    # re-run everything not on it -- analyze.py stays the single source of truth.
    parser.add_argument('--version', action='version', version=ANALYZER_VERSION)
    parser.add_argument('video_path', help='Path to the video file')
    parser.add_argument('--no-detect', action='store_true',
                        help='Skip object detection and report metadata only')
    parser.add_argument('--sample-fps', type=float, default=None,
                        help='Frames sampled per second of video (default 1)')
    parser.add_argument('--max-frames', type=int, default=None,
                        help='Ceiling on sampled frames regardless of duration')
    parser.add_argument('--confidence', type=float, default=None,
                        help='Minimum detection confidence (default 0.5)')
    args = parser.parse_args(argv)

    started = time.monotonic()

    if not os.path.isfile(args.video_path):
        log(f'No such file: {args.video_path}')
        return 2

    progress('initializing', 5)
    log(f'Analyzing {args.video_path}')

    try:
        progress('analyzing', 10)
        metadata = probe_video(args.video_path)
    except ImportError as exc:
        log(f'Missing Python dependency: {exc}. Install the analyzer requirements.')
        return 3
    except Exception as exc:
        log(f'Failed to read the video: {exc}')
        return 4

    tags: list[str] = []
    events: list[dict] = []
    summary = describe(metadata)

    if not args.no_detect:
        try:
            import detection

            options = {}
            if args.sample_fps is not None:
                options['sample_fps'] = args.sample_fps
            if args.max_frames is not None:
                options['max_frames'] = args.max_frames
            if args.confidence is not None:
                options['confidence'] = args.confidence

            log('Loading detection model...')
            progress('analyzing', 15)

            # Detection dominates the runtime, so map it across most of the bar.
            result = detection.detect(
                args.video_path,
                metadata,
                on_progress=lambda pct: progress('detecting_events', 15 + int(pct * 0.7)),
                **options,
            )

            tags = result['tags']
            events = result.get('events') or []
            summary = detection.summarize(metadata, result)
            # tags and events are emitted as their own fields; copying them in
            # here as well would store each one twice.
            metadata['detection'] = {
                key: value for key, value in result.items()
                if key not in ('tags', 'events')
            }
            # Reported as a signal with its reasoning, not folded into the tags:
            # it is a judgement about the footage, and a moderator should be able
            # to see why before acting on it.
            metadata['classification'] = detection.classify_footage(metadata, result)
            log(f"Detected {len(tags)} tag(s) on {result['device']} "
                f"from {result['frames_sampled']} frames")
        except ImportError as exc:
            log(f'Missing Python dependency: {exc}. Install the analyzer requirements.')
            return 3
        except Exception as exc:
            log(f'Object detection failed: {exc}')
            return 5

    progress('uploading_results', 90)

    metadata['analyzer_version'] = ANALYZER_VERSION
    metadata['analysis_seconds'] = round(time.monotonic() - started, 3)

    emit({
        'type': 'result',
        'summary': summary,
        'tags': tags,
        # One event per detected label, at the moment it was first seen. These
        # are observations, not incidents: locating a real incident in time is
        # a different problem, and nothing here claims to have solved it.
        'events': events,
        'metadata': metadata,
    })
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
