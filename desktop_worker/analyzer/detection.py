"""Object detection over sampled video frames.

Kept separate from analyze.py so the metadata path stays usable on a machine
without the ML dependencies installed, and so this module can be imported and
exercised on its own.

Model: YOLOv8n. COCO's vocabulary happens to suit dashcam footage well -- car,
truck, bus, motorcycle, person, bicycle, traffic light, stop sign -- and the
nano weights are small enough to run at a sensible speed on a CPU.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Callable, Iterable

# Default model. Pinned by name so every host runs the same weights; ultralytics
# caches the download after the first run.
DEFAULT_MODEL = 'yolov8n.pt'

# Below this, detections are more noise than signal on dashcam footage.
DEFAULT_CONFIDENCE = 0.5

# One frame per second is plenty: dashcam scenes change slowly, and sampling
# every frame would multiply cost ~25x for almost no additional information.
DEFAULT_SAMPLE_FPS = 1.0

# Hard ceiling regardless of duration. A 30 minute clip at 1 fps would be 1800
# inferences, which is minutes of work on a CPU-only host. Sampling is spread
# across the whole video rather than truncated, so a long clip is still covered
# end to end, just more coarsely.
DEFAULT_MAX_FRAMES = 300

# A class must appear in at least this share of sampled frames to become a tag.
# Filters out single-frame false positives without losing brief real events.
MIN_FRAME_SHARE = 0.05


def resolve_device() -> str:
    """Pick the best available accelerator. GPU is a bonus, never a requirement."""
    try:
        import torch
    except ImportError:
        return 'cpu'

    if torch.cuda.is_available():
        return 'cuda'

    # getattr because older torch builds have no mps attribute at all.
    mps = getattr(torch.backends, 'mps', None)
    if mps is not None and mps.is_available():
        return 'mps'

    return 'cpu'


def frame_indices(frame_count: int, fps: float, sample_fps: float, max_frames: int) -> list[int]:
    """Which frame numbers to sample, spread evenly across the video."""
    if frame_count <= 0:
        return []

    if fps <= 0:
        fps = 30.0  # a guess is better than sampling nothing

    wanted = max(1, int(math.ceil((frame_count / fps) * sample_fps)))
    wanted = min(wanted, max_frames, frame_count)

    if wanted == 1:
        return [0]

    step = (frame_count - 1) / (wanted - 1)
    return sorted({int(round(i * step)) for i in range(wanted)})


def detect(
    video_path: str,
    metadata: dict,
    *,
    model_name: str = DEFAULT_MODEL,
    confidence: float = DEFAULT_CONFIDENCE,
    sample_fps: float = DEFAULT_SAMPLE_FPS,
    max_frames: int = DEFAULT_MAX_FRAMES,
    on_progress: Callable[[int], None] | None = None,
) -> dict:
    """Run detection over sampled frames.

    Returns a dict with `tags`, `counts`, and diagnostic fields. Raises on a
    genuine failure; the caller turns that into an exit code.
    """
    import cv2
    from ultralytics import YOLO

    device = resolve_device()
    model = YOLO(model_name)

    indices = frame_indices(
        metadata.get('frame_count', 0), metadata.get('fps', 0.0), sample_fps, max_frames)
    if not indices:
        return _empty_result(model_name, device, confidence, 0)

    capture = cv2.VideoCapture(video_path)
    if not capture.isOpened():
        raise RuntimeError(f'Could not reopen the video for detection: {video_path}')

    # Highest confidence seen per class, and how many sampled frames it appeared in.
    frames_with_class: dict[str, int] = defaultdict(int)
    best_confidence: dict[str, float] = defaultdict(float)
    sampled = 0

    try:
        for position, index in enumerate(indices):
            capture.set(cv2.CAP_PROP_POS_FRAMES, index)
            ok, frame = capture.read()
            if not ok:
                # Seeking past a damaged region is normal; skip rather than abort.
                continue

            sampled += 1
            predictions = model.predict(
                frame, conf=confidence, device=device, verbose=False)

            seen_in_frame: set[str] = set()
            for prediction in predictions:
                for box in prediction.boxes:
                    label = prediction.names[int(box.cls)]
                    score = float(box.conf)
                    seen_in_frame.add(label)
                    best_confidence[label] = max(best_confidence[label], score)

            for label in seen_in_frame:
                frames_with_class[label] += 1

            if on_progress is not None:
                on_progress(int((position + 1) / len(indices) * 100))
    finally:
        capture.release()

    if sampled == 0:
        raise RuntimeError('Could not read any frames from the video for detection.')

    # Keep classes that show up often enough to be believable.
    threshold = max(1, math.ceil(sampled * MIN_FRAME_SHARE))
    kept = {label: count for label, count in frames_with_class.items() if count >= threshold}

    # Most frequent first: the tags a person would lead with.
    tags = sorted(kept, key=lambda label: (-kept[label], label))

    return {
        'tags': tags,
        'counts': {label: kept[label] for label in tags},
        'confidence': {label: round(best_confidence[label], 3) for label in tags},
        'discarded': sorted(set(frames_with_class) - set(kept)),
        'frames_sampled': sampled,
        'frames_requested': len(indices),
        'model': model_name,
        'device': device,
        'confidence_threshold': confidence,
    }


def _empty_result(model_name: str, device: str, confidence: float, sampled: int) -> dict:
    return {
        'tags': [],
        'counts': {},
        'confidence': {},
        'discarded': [],
        'frames_sampled': sampled,
        'frames_requested': 0,
        'model': model_name,
        'device': device,
        'confidence_threshold': confidence,
    }


def summarize(metadata: dict, detection: dict) -> str:
    """A factual sentence built only from what was actually detected."""
    size = f"{metadata['width']}x{metadata['height']}"
    duration = metadata.get('duration_seconds') or 0
    minutes, seconds = divmod(int(duration), 60)
    length = f'{minutes}m {seconds:02d}s' if minutes else f'{seconds}s'

    counts = detection.get('counts') or {}
    if not counts:
        return (f'{size} dashcam clip, {length}. No recognisable objects were '
                f'detected in the sampled frames.')

    def phrase(label: str) -> str:
        share = counts[label] / max(1, detection['frames_sampled'])
        qualifier = 'throughout' if share > 0.6 else 'briefly' if share < 0.2 else ''
        return f'{label} ({qualifier})' if qualifier else label

    leading = [phrase(label) for label in detection['tags'][:4]]
    detected = ', '.join(leading)
    return (f'{size} dashcam clip, {length}. Detected across '
            f"{detection['frames_sampled']} sampled frames: {detected}.")
