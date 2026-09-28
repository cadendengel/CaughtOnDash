"""Candidate incident moments: when in the clip something probably happened.

Two signals, chosen because they fail differently:

- **Audio transients.** An impact, a horn, a shout: the loudest *peak* in a
  100 ms window, measured against the median of the surrounding three
  seconds. Peak rather than RMS, and local rather than global: road and wind
  noise are loud but steady, and an impact is brief. On the 2026-09-26
  collision clip the 26.1 s window peaks at -12.7 dBFS over a -19.3 median --
  the same numbers the manual investigation found -- where RMS put it in a
  near-tie with two unremarkable moments.
- **Camera jolt.** The frame-to-frame shift of the whole image, at 10 fps, and
  how suddenly it changes. A mount knocked by an impact or a swerve jerks the
  image; smooth driving shifts it gradually. Jolt alone is ambiguous -- a
  large vehicle filling the frame moves the image too -- so it corroborates.

Scores combine as a noisy-OR: two signals agreeing within a second make a
strong candidate, and one alone has to be strong on its own. These are
*candidates*, never "collisions": a person looks at the burst and decides.

The thresholds are set from one labelled clip, and it shows. The collision
scores 1.0; a car carrier passing close in the same drive scores 0.69 (a hard
jolt, a faint sound). The bar sits between them, and anything from 0.5 up to
it is still listed as `possible` -- without images -- because a near miss is
worth a glance too. Tune both against a labelled set before trusting them.

When the detection model is available, the closest vehicle around each
candidate is measured and cropped. That is reported as an observation and does
not change the score -- it has not been validated against labelled footage.
"""

from __future__ import annotations

import shutil
import subprocess

AUDIO_RATE = 16000
AUDIO_WINDOW_SECONDS = 0.1
AUDIO_LOCAL_SECONDS = 3.0
# Rise over the local median, in dB. Below the minimum is ordinary variation;
# at the full value the audio alone is conclusive enough to score 1.
AUDIO_MIN_RISE_DB = 3.0
AUDIO_FULL_RISE_DB = 8.0
# A clip whose loudest moment is this quiet has no usable audio -- a muted
# dashcam writes silence, not nothing.
AUDIO_SILENT_DBFS = -60.0

JOLT_FPS = 10.0
JOLT_WIDTH = 320
# Phase correlation reports how much of the image agreed on the shift. Below
# this the "shift" is noise -- typically a vehicle filling the frame.
JOLT_MIN_RESPONSE = 0.1
JOLT_MIN_Z = 4.0
JOLT_FULL_Z = 12.0

AGREE_SECONDS = 1.0
MIN_SCORE = 0.75
MIN_POSSIBLE_SCORE = 0.5
MAX_POSSIBLE = 3
MIN_SEPARATION_SECONDS = 3.0
MAX_MOMENTS = 2

BURST_FRAMES = 20
BURST_SPAN_SECONDS = 1.0     # either side of the moment
KEY_FRAME_OFFSETS = (-0.5, 0.0, 0.5)
APPROACH_SPAN_SECONDS = 2.0
APPROACH_FPS = 5.0
VEHICLE_CLASSES = frozenset({'car', 'truck', 'bus', 'motorcycle'})
# Below this share of the frame the "closest" vehicle is just the biggest
# thing in a distant scene -- 3% on the first production QA clip, which told a
# reader nothing. The collision's truck filled 40%.
MIN_CLOSEST_SHARE = 0.10


# --- Pure scoring -----------------------------------------------------------

def _ramp(value: float, low: float, high: float) -> float:
    if value <= low:
        return 0.0
    if value >= high:
        return 1.0
    return (value - low) / (high - low)


def audio_rises(samples, rate: int = AUDIO_RATE) -> list[dict]:
    """Per-window peak level and its rise over the local median.

    `samples` is mono float audio in [-1, 1]. Returns one dict per window with
    a positive rise, or [] for silence.
    """
    import numpy as np

    window = max(1, int(rate * AUDIO_WINDOW_SECONDS))
    count = len(samples) // window
    if count == 0:
        return []
    blocks = np.asarray(samples[:count * window], dtype=np.float32).reshape(count, window)
    peaks = 20 * np.log10(np.abs(blocks).max(axis=1) + 1e-9)
    if peaks.max() < AUDIO_SILENT_DBFS:
        return []

    half = max(1, int(AUDIO_LOCAL_SECONDS / 2 / AUDIO_WINDOW_SECONDS))
    events = []
    for index in range(count):
        local = float(np.median(peaks[max(0, index - half):index + half + 1]))
        rise = float(peaks[index]) - local
        if rise > AUDIO_MIN_RISE_DB:
            events.append({
                't_seconds': round(index * AUDIO_WINDOW_SECONDS, 2),
                'peak_dbfs': round(float(peaks[index]), 1),
                'local_median_dbfs': round(local, 1),
                'rise_db': round(rise, 1),
                'strength': round(_ramp(rise, AUDIO_MIN_RISE_DB, AUDIO_FULL_RISE_DB), 3),
            })
    return events


def jolt_scores(shifts: list[tuple[float, float, float, float]]) -> list[dict]:
    """Sudden changes in whole-image shift.

    `shifts` holds (t_seconds, dx, dy, response) between consecutive samples.
    The jerk is how far the shift changed from one pair to the next, scored
    as robust z against the clip's own median, so a bumpy road raises its own
    baseline instead of producing a candidate every second.
    """
    import numpy as np

    usable = [s for s in shifts if s[3] >= JOLT_MIN_RESPONSE]
    if len(usable) < 3:
        return []

    times = np.array([s[0] for s in usable])
    dx = np.array([s[1] for s in usable])
    dy = np.array([s[2] for s in usable])
    jerk = np.hypot(np.diff(dx), np.diff(dy))
    median = float(np.median(jerk))
    spread = float(np.median(np.abs(jerk - median))) or 1e-6

    events = []
    for index, value in enumerate(jerk):
        z = (float(value) - median) / spread
        if z > JOLT_MIN_Z:
            events.append({
                't_seconds': round(float(times[index + 1]), 2),
                'jerk_px': round(float(value), 2),
                'z': round(z, 1),
                'strength': round(_ramp(z, JOLT_MIN_Z, JOLT_FULL_Z), 3),
            })
    return events


def _strongest_near(events: list[dict], t: float) -> dict | None:
    near = [event for event in events if abs(event['t_seconds'] - t) <= AGREE_SECONDS]
    return max(near, key=lambda event: event['strength'], default=None)


def find_moments(audio: list[dict], jolt: list[dict], max_moments: int = MAX_MOMENTS,
                 min_score: float = MIN_SCORE, exclude: list[dict] = ()) -> list[dict]:
    """Fuse the two signals into ranked, well-separated candidates.

    `exclude` holds moments already reported, so the weaker `possible` list
    does not repeat them.
    """
    candidates = []
    for event in [*audio, *jolt]:
        a = _strongest_near(audio, event['t_seconds'])
        j = _strongest_near(jolt, event['t_seconds'])
        a_strength = a['strength'] if a else 0.0
        j_strength = j['strength'] if j else 0.0
        score = 1 - (1 - a_strength) * (1 - j_strength)
        if score < min_score:
            continue
        # The audio peak locates an impact more precisely than a 10 fps jolt.
        t = a['t_seconds'] if a else j['t_seconds']
        candidates.append({'t_seconds': t, 'score': round(score, 3), 'audio': a, 'jolt': j})

    moments = []
    for candidate in sorted(candidates, key=lambda c: (-c['score'], c['t_seconds'])):
        if any(abs(candidate['t_seconds'] - kept['t_seconds']) < MIN_SEPARATION_SECONDS
               for kept in [*moments, *exclude]):
            continue
        candidate['reasons'] = describe(candidate)
        moments.append(candidate)
        if len(moments) >= max_moments:
            break
    return sorted(moments, key=lambda m: m['t_seconds'])


def describe(moment: dict) -> list[str]:
    reasons = []
    if moment.get('audio'):
        a = moment['audio']
        reasons.append(f"sharp sound at {a['t_seconds']:.1f}s: {a['peak_dbfs']} dBFS, "
                       f"{a['rise_db']} dB above the surrounding audio")
    if moment.get('jolt'):
        j = moment['jolt']
        reasons.append(f"camera jolt at {j['t_seconds']:.1f}s: image shift changed by "
                       f"{j['jerk_px']} px, {j['z']}x the clip's usual variation")
    if moment.get('audio') and moment.get('jolt'):
        reasons.append('both signals agree within a second')
    return reasons


# --- Measurement ------------------------------------------------------------

def read_audio(path: str):
    """Mono 16 kHz samples via ffmpeg, or None when there is no audio to read."""
    import numpy as np

    executable = shutil.which('ffmpeg')
    if executable is None:
        return None
    completed = subprocess.run(
        [executable, '-v', 'quiet', '-i', path, '-vn', '-ac', '1', '-ar', str(AUDIO_RATE),
         '-f', 'f32le', '-'],
        capture_output=True, timeout=300, check=False,
    )
    if completed.returncode != 0 or not completed.stdout:
        return None
    return np.frombuffer(completed.stdout, dtype=np.float32)


def measure_shifts(capture, fps: float, box: dict | None) -> list[tuple[float, float, float, float]]:
    """Whole-image shift between consecutive 10 fps samples."""
    import cv2
    import numpy as np

    import detection

    step = max(1, round((fps or 30.0) / JOLT_FPS))
    capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
    shifts = []
    previous = None
    index = 0
    while True:
        # grab() skips the decode-to-image for frames that are not sampled.
        if not capture.grab():
            break
        if index % step == 0:
            ok, frame = capture.retrieve()
            if ok:
                frame = detection.crop_to_content(frame, box)
                height, width = frame.shape[:2]
                small = cv2.resize(frame, (JOLT_WIDTH, max(1, round(height * JOLT_WIDTH / width))))
                grey = np.float32(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
                if previous is not None and previous.shape == grey.shape:
                    (dx, dy), response = cv2.phaseCorrelate(previous, grey)
                    shifts.append((index / (fps or 30.0), float(dx), float(dy), float(response)))
                previous = grey
        index += 1
    return shifts


def _read_at(capture, seconds: float, fps: float, frame_count: int):
    import cv2
    index = min(max(0, round(seconds * fps)), max(0, frame_count - 1))
    capture.set(cv2.CAP_PROP_POS_FRAMES, index)
    ok, frame = capture.read()
    return (frame, index / fps) if ok else (None, None)


def closest_approach(capture, model, device: str, moment: dict, metadata: dict, box: dict | None) -> dict | None:
    """The largest vehicle in frame around a moment, and a crop of it.

    Largest by share of the frame: a vehicle cutting across the camera fills
    it. Returns the observation and the crop image, or None if no vehicle was
    seen. The frame kept is the sharpest one showing that vehicle at close to
    its largest, since the largest frame is often the most motion-blurred.
    """
    import cv2

    import detection

    fps = metadata.get('fps') or 30.0
    frame_count = metadata.get('frame_count') or 0
    t = moment['t_seconds']
    seen = []
    steps = int(APPROACH_SPAN_SECONDS * 2 * APPROACH_FPS) + 1
    for step in range(steps):
        seconds = t - APPROACH_SPAN_SECONDS + step / APPROACH_FPS
        if seconds < 0:
            continue
        frame, at = _read_at(capture, seconds, fps, frame_count)
        if frame is None:
            continue
        frame = detection.crop_to_content(frame, box)
        height, width = frame.shape[:2]
        for prediction in model.predict(frame, conf=detection.DEFAULT_CONFIDENCE, device=device, verbose=False):
            for found in prediction.boxes:
                label = detection.CLASS_ALIASES.get(prediction.names[int(found.cls)], prediction.names[int(found.cls)])
                if label not in VEHICLE_CLASSES:
                    continue
                x1, y1, x2, y2 = (int(v) for v in found.xyxy[0].tolist())
                share = (x2 - x1) * (y2 - y1) / float(width * height)
                sharpness = float(cv2.Laplacian(cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()) if x2 > x1 and y2 > y1 else 0.0
                seen.append((share, sharpness, label, float(found.conf), at, (x1, y1, x2, y2), frame))

    if not seen:
        return None
    largest = max(share for share, *_ in seen)
    if largest < MIN_CLOSEST_SHARE:
        return None
    # Among detections at least 80% as large as the largest, the sharpest.
    share, sharpness, label, confidence, at, (x1, y1, x2, y2), frame = max(
        (s for s in seen if s[0] >= largest * 0.8), key=lambda s: s[1])
    offset_x = box['x'] if box else 0
    offset_y = box['y'] if box else 0
    best = {
        'label': label,
        'confidence': round(confidence, 3),
        't_seconds': round(at, 2),
        'frame_share': round(share, 3),
        'largest_frame_share': round(largest, 3),
        # In the file's own pixels, so the box can be found in the original.
        'bbox': [x1 + offset_x, y1 + offset_y, x2 - x1, y2 - y1],
    }
    return {'observation': best, 'crop': frame[y1:y2, x1:x2]}


# --- Orchestration ----------------------------------------------------------

def find(path: str, metadata: dict, out_dir: str | None, model=None, device: str = 'cpu',
         log=lambda message: None) -> tuple[dict, list[dict]]:
    """Measure, score, and write images for each candidate moment.

    Returns (summary, artifacts). The summary goes in `private`; it says which
    signals were available, so "no moments" from a silent clip is not read as
    "nothing happened".
    """
    import os

    import cv2

    import detection
    import evidence

    audio_samples = read_audio(path)
    audio = audio_rises(audio_samples) if audio_samples is not None and len(audio_samples) else []

    capture = cv2.VideoCapture(path)
    if not capture.isOpened():
        return {'available': False, 'reason': 'video could not be reopened'}, []

    artifacts = []
    try:
        fps = metadata.get('fps') or 30.0
        frame_count = metadata.get('frame_count') or 0
        box = detection.content_box(
            capture, detection.frame_indices(frame_count, fps, 1.0, 60))
        jolt = jolt_scores(measure_shifts(capture, fps, box))
        moments = find_moments(audio, jolt)
        possible = [m for m in find_moments(audio, jolt, MAX_POSSIBLE, MIN_POSSIBLE_SCORE, exclude=moments)
                    if m['score'] < MIN_SCORE]

        for number, moment in enumerate(moments, start=1):
            t = moment['t_seconds']
            if out_dir:
                os.makedirs(out_dir, exist_ok=True)
                frames = []
                for step in range(BURST_FRAMES):
                    seconds = t - BURST_SPAN_SECONDS + step * (2 * BURST_SPAN_SECONDS) / (BURST_FRAMES - 1)
                    frame, at = _read_at(capture, max(0.0, seconds), fps, frame_count)
                    if frame is not None:
                        frames.append((detection.crop_to_content(frame, box), evidence.format_timestamp(at)))
                sheet = evidence.write_sheet(frames, os.path.join(out_dir, f'moment{number}_burst.jpg'), max_columns=5)
                if sheet:
                    sheet.pop('grid')
                    sheet.pop('tiles')
                    artifacts.append({**sheet, 'kind': 'burst', 't_seconds': t,
                                      'label': f'Moment {number}: {len(frames)} frames, {t - BURST_SPAN_SECONDS:.1f}-{t + BURST_SPAN_SECONDS:.1f}s'})

                for offset in KEY_FRAME_OFFSETS:
                    frame, at = _read_at(capture, max(0.0, t + offset), fps, frame_count)
                    if frame is None:
                        continue
                    target = os.path.join(out_dir, f'moment{number}_frame_{at:.2f}s.jpg')
                    if cv2.imwrite(target, frame, [cv2.IMWRITE_JPEG_QUALITY, 92]):
                        artifacts.append({'path': target, 'kind': 'frame', 't_seconds': round(at, 2),
                                          'label': f'Moment {number}, full frame at {evidence.format_timestamp(at)}',
                                          'width': int(frame.shape[1]), 'height': int(frame.shape[0])})

            if model is not None:
                try:
                    approach = closest_approach(capture, model, device, moment, metadata, box)
                except Exception as exc:
                    log(f'Closest approach skipped for moment {number}: {exc}')
                    approach = None
                if approach:
                    moment['closest_vehicle'] = approach['observation']
                    crop = approach['crop']
                    if out_dir and crop.size:
                        target = os.path.join(out_dir, f'moment{number}_vehicle.jpg')
                        if cv2.imwrite(target, crop, [cv2.IMWRITE_JPEG_QUALITY, 95]):
                            obs = approach['observation']
                            artifacts.append({'path': target, 'kind': 'vehicle_crop', 't_seconds': obs['t_seconds'],
                                              'bbox': obs['bbox'], 'label': f"Moment {number}: closest {obs['label']}",
                                              'width': int(crop.shape[1]), 'height': int(crop.shape[0])})
    finally:
        capture.release()

    summary = {
        'available': True,
        'signals': {
            'audio': 'measured' if audio_samples is not None and len(audio_samples) else 'unavailable',
            'jolt': 'measured',
            'closest_vehicle': 'measured' if model is not None else 'unavailable',
        },
        'moments': moments,
        'possible': possible,
    }
    return summary, artifacts
