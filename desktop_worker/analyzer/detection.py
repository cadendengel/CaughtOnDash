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
# 0.35, down from 0.5.
#
# Measured on a real night-time dashcam clip: at 0.5 the model saw one car in
# four sampled frames and the footage was classified as "not dashcam". The
# cars were there and scoring 0.3-0.5 -- dark, wet road, headlight glare -- so
# a 0.5 gate discarded them. At 0.35 the same clip reports cars, a person and
# a traffic light, and classifies correctly.
#
# Checked against the negative case too: a portrait video of a teddy bear
# stays at zero road objects at 0.35, 0.25 and every density tried, so this
# buys sensitivity without inventing road scenes.
DEFAULT_CONFIDENCE = 0.35

# One frame per second is plenty: dashcam scenes change slowly, and sampling
# every frame would multiply cost ~25x for almost no additional information.
DEFAULT_SAMPLE_FPS = 1.0

# One frame a second leaves a 5-second clip judged on four frames, where a
# single detection moves the road-object share by 25 points and the dashcam
# verdict can turn on one frame. Short clips get sampled densely enough for
# the share to mean something; long ones are unaffected, since they already
# exceed this from duration alone.
MIN_SAMPLED_FRAMES = 8

# Hard ceiling regardless of duration. A 30 minute clip at 1 fps would be 1800
# inferences, which is minutes of work on a CPU-only host. Sampling is spread
# across the whole video rather than truncated, so a long clip is still covered
# end to end, just more coarsely.
DEFAULT_MAX_FRAMES = 300

# A class must appear in at least this share of sampled frames to become a tag.
# Filters out single-frame false positives without losing brief real events.
MIN_FRAME_SHARE = 0.05

# Classes that indicate a road scene. 'person' is deliberately excluded: people
# appear in road footage and in everything else, so it carries no signal here.
ROAD_CLASSES = frozenset({
    'car', 'truck', 'bus', 'motorcycle', 'bicycle', 'traffic light', 'stop sign',
})

# A road object must be present in at least this share of sampled frames. A car
# glimpsed once is a parked car in the background; a car in most frames is the
# road ahead.
DASHCAM_ROAD_SHARE = 0.5


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
    # Floor for short clips; never more frames than the video has.
    wanted = max(wanted, min(MIN_SAMPLED_FRAMES, frame_count))
    wanted = min(wanted, max_frames, frame_count)

    if wanted == 1:
        return [0]

    step = (frame_count - 1) / (wanted - 1)
    return sorted({int(round(i * step)) for i in range(wanted)})


def _frame_seconds(index: int, metadata: dict) -> float:
    """Where a frame sits in the video, in seconds."""
    fps = metadata.get('fps') or 0.0
    if fps <= 0:
        return 0.0
    return round(index / fps, 2)


def build_events(
    kept: dict[str, int],
    appearances: dict[str, list[float]],
    best_confidence: dict[str, float],
    tags: list[str],
) -> list[dict]:
    """One event per detected label, at the moment it was first seen.

    Deliberately first-appearance rather than every sighting. Sampling is
    sparse -- roughly one frame a second -- so a run of appearances is a series
    of guesses about what happened between samples, while "a car was on screen
    at 0:14" is something that was actually observed. One honest marker per
    label beats a dense timeline that implies continuous tracking.

    This is not incident detection. It says what was visible and when, which is
    enough to jump to the relevant part of a clip, and claims nothing about
    whether anything happened.
    """
    events = []
    for label in tags:
        times = appearances.get(label) or []
        if not times:
            continue

        first = min(times)
        events.append({
            'timestamp_seconds': first,
            'label': label,
            'description': f'{label} first seen',
            'confidence': round(best_confidence.get(label, 0.0), 3),
            # How many sampled frames it appeared in, so a viewer can tell a
            # passing glimpse from a constant presence.
            'frames_seen': kept.get(label, len(times)),
            'last_seen_seconds': max(times),
        })

    return sorted(events, key=lambda event: (event['timestamp_seconds'], event['label']))


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
    # When each label was actually on screen, in seconds. Recorded per sampled
    # frame so events can carry real timestamps rather than invented ones.
    appearances: dict[str, list[float]] = defaultdict(list)
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
                appearances[label].append(_frame_seconds(index, metadata))

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
        'events': build_events(kept, appearances, best_confidence, tags),
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
        'events': [],
        'counts': {},
        'confidence': {},
        'discarded': [],
        'frames_sampled': sampled,
        'frames_requested': 0,
        'model': model_name,
        'device': device,
        'confidence_threshold': confidence,
    }


def orientation_of(metadata: dict) -> str:
    width = metadata.get('width') or 0
    height = metadata.get('height') or 0

    if width <= 0 or height <= 0:
        return 'unknown'
    if width > height:
        return 'landscape'
    if height > width:
        return 'portrait'
    return 'square'


def classify_footage(metadata: dict, detection: dict) -> dict:
    """Does this look like dashcam footage?

    A heuristic, and reported as one: the signals it was derived from are
    returned alongside the verdict so a human can disagree with it. Two
    independent pieces of evidence, both cheap and already computed:

    - road objects present across most of the video. A mounted camera pointed
      at a road sees vehicles and traffic furniture continuously.
    - landscape orientation. Dashcams are fixed and wide; portrait video is
      almost always a phone held by a person.

    Both must hold. Either alone is too easy to trip: a passenger filming out
    of a window is landscape with cars in it, and a phone in a car mount is
    portrait footage of a road. Requiring both keeps false positives low at
    the cost of missing unusual-but-real dashcam setups, which is the right
    trade for something that might drive moderation.
    """
    counts = detection.get('counts') or {}
    sampled = max(1, detection.get('frames_sampled') or 0)

    road_shares = {
        label: counts[label] / sampled
        for label in counts
        if label in ROAD_CLASSES
    }
    strongest = max(road_shares.values(), default=0.0)
    orientation = orientation_of(metadata)

    has_road_scene = strongest >= DASHCAM_ROAD_SHARE
    is_landscape = orientation == 'landscape'

    if has_road_scene and is_landscape:
        reason = 'road objects across most frames, landscape orientation'
    elif not road_shares:
        reason = 'no road objects detected'
    elif not has_road_scene:
        reason = 'road objects present but only intermittently'
    else:
        reason = f'road objects present but orientation is {orientation}'

    return {
        'looks_like_dashcam': has_road_scene and is_landscape,
        'reason': reason,
        'orientation': orientation,
        'road_classes_detected': sorted(road_shares),
        'strongest_road_class_share': round(strongest, 3),
    }


def summarize(metadata: dict, detection: dict) -> str:
    """A factual sentence built only from what was actually detected.

    Deliberately says "clip", not "dashcam clip". Whether the footage is from a
    dashcam is a conclusion, not an observation -- and calling a portrait video
    of a teddy bear a dashcam clip is exactly the kind of unfounded claim this
    analyzer exists to avoid. See classify_footage for the actual signal.
    """
    size = f"{metadata['width']}x{metadata['height']}"
    duration = metadata.get('duration_seconds') or 0
    minutes, seconds = divmod(int(duration), 60)
    length = f'{minutes}m {seconds:02d}s' if minutes else f'{seconds}s'

    counts = detection.get('counts') or {}
    if not counts:
        return (f'{size} clip, {length}. No recognisable objects were '
                f'detected in the sampled frames.')

    def phrase(label: str) -> str:
        share = counts[label] / max(1, detection['frames_sampled'])
        qualifier = 'throughout' if share > 0.6 else 'briefly' if share < 0.2 else ''
        return f'{label} ({qualifier})' if qualifier else label

    leading = [phrase(label) for label in detection['tags'][:4]]
    detected = ', '.join(leading)
    return (f'{size} clip, {length}. Detected across '
            f"{detection['frames_sampled']} sampled frames: {detected}.")
