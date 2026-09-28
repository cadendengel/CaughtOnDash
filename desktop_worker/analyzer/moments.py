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

A moment needs both signals. Either one alone is capped below the bar, so it
can only ever be `possible` (listed, without images). That rule comes from
reviewing every moment the production corpus produced (detect-4.5, 19 videos):
of 17 flagged moments only the 2026-09-26 collision was an incident, and it was
the only one where sound and jolt agreed. The other 16, checked frame by frame
in their bursts, were:
- the first half-second of a clip (3) -- encoder start-up, not motion;
- edits: cuts between shots, a multi-camera mosaic, a TikTok end card (4);
- turns and kerb bumps (3) -- real jolts, but driving, not incidents;
- repeated or dropped frames (the rest) -- a duplicated frame reads as no
  motion then double motion, which is exactly the shape of a jolt.
So: events in the first second and last half-second are dropped; a repeated
frame is skipped and motion is measured per frame step, so a stutter is not a
jerk; a scene cut (the picture changes wholesale) is detected, reported, and
events beside it dropped; and a jerk is never measured across a gap.

The collision still scores 0.91 (sound 6.6 dB, jolt 15x, agreeing). A car
carrier passing close in the same drive stays `possible`, as does the jolt
alone when the audio is stripped. These judgements are one reviewer's reading
of the frames, not labels from the people who filmed them.

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
# The most either signal can contribute alone -- below MIN_SCORE, so a moment
# needs both. Two signals at this strength combine to 0.91.
SINGLE_SIGNAL_MAX = 0.7
MIN_SCORE = 0.75
MIN_POSSIBLE_SCORE = 0.5
MAX_POSSIBLE = 3
MIN_SEPARATION_SECONDS = 3.0
MAX_MOMENTS = 2

# Clip edges, where encoders start and stop and nothing is measured reliably.
EDGE_START_SECONDS = 1.0
EDGE_END_SECONDS = 0.5
# Two samples this similar (mean absolute grey difference, 0-255) are the same
# frame repeated -- a stutter, not a stop.
DUPLICATE_MAX_DIFF = 0.5
# Colour-histogram correlation below this between consecutive samples is a cut
# to a different shot, not camera motion.
CUT_MAX_CORRELATION = 0.5
CUT_GUARD_SECONDS = 0.5
# Consecutive shifts more than this many sample steps apart are not compared.
MAX_STEP_GAP = 1.5

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

    `shifts` holds (t_seconds, dx, dy, response) per sample step -- motion per
    step, so a pair that spans a skipped duplicate frame is not double. The
    jerk is how far that changed from one sample to the next, scored as robust
    z against the clip's own median, so a bumpy road raises its own baseline.
    It is only measured between samples that are actually adjacent: across a
    dropped pair (a duplicate, a cut, an unreliable correlation) there is no
    telling what happened in between.
    """
    import numpy as np

    usable = [s for s in shifts if s[3] >= JOLT_MIN_RESPONSE]
    if len(usable) < 3:
        return []

    times = np.array([s[0] for s in usable])
    gaps = np.diff(times)
    step = float(np.median(gaps)) if len(gaps) else 0.1
    adjacent = gaps <= step * MAX_STEP_GAP
    dx = np.array([s[1] for s in usable])
    dy = np.array([s[2] for s in usable])
    jerk = np.hypot(np.diff(dx), np.diff(dy))
    if not adjacent.any():
        return []
    median = float(np.median(jerk[adjacent]))
    spread = float(np.median(np.abs(jerk[adjacent] - median))) or 1e-6

    events = []
    for index, value in enumerate(jerk):
        if not adjacent[index]:
            continue
        z = (float(value) - median) / spread
        if z > JOLT_MIN_Z:
            events.append({
                't_seconds': round(float(times[index + 1]), 2),
                'jerk_px': round(float(value), 2),
                'z': round(z, 1),
                'strength': round(_ramp(z, JOLT_MIN_Z, JOLT_FULL_Z), 3),
            })
    return events


def usable_events(events: list[dict], duration: float, cuts: list[float]) -> list[dict]:
    """Events away from the clip's edges and from any scene cut."""
    kept = []
    for event in events:
        t = event['t_seconds']
        if t < EDGE_START_SECONDS or (duration and t > duration - EDGE_END_SECONDS):
            continue
        if any(abs(t - cut) <= CUT_GUARD_SECONDS for cut in cuts):
            continue
        kept.append(event)
    return kept


def burst_window(t: float, duration: float, span: float = BURST_SPAN_SECONDS) -> tuple[float, float]:
    """(start, end) of the burst around t, shifted -- not clipped -- at the
    clip's edges, so a moment near the start does not repeat frame zero."""
    width = 2 * span
    if duration and duration <= width:
        return 0.0, duration
    start = t - span
    if duration:
        start = min(start, duration - width)
    start = max(0.0, start)
    return start, start + width


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
        # Each capped below the bar: a moment needs the two to agree.
        a_strength = min(a['strength'], SINGLE_SIGNAL_MAX) if a else 0.0
        j_strength = min(j['strength'], SINGLE_SIGNAL_MAX) if j else 0.0
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


def measure_shifts(capture, fps: float, box: dict | None) -> tuple[list[tuple[float, float, float, float]], list[float]]:
    """Whole-image shift between 10 fps samples, and the times of scene cuts.

    A sample identical to the last is a repeated frame and is skipped; the
    shift to the next real frame is divided by the steps it spans. A sample
    whose colours share little with the last is a cut: no shift is recorded
    across it, and its time is returned.
    """
    import cv2
    import numpy as np

    import detection

    fps = fps or 30.0
    step = max(1, round(fps / JOLT_FPS))
    capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
    shifts, cuts = [], []
    previous = previous_hist = None
    previous_index = 0
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
                hist = cv2.calcHist([cv2.cvtColor(small, cv2.COLOR_BGR2HSV)], [0, 1], None, [16, 16], [0, 180, 0, 256])
                cv2.normalize(hist, hist)
                if previous is not None and previous.shape == grey.shape:
                    if float(np.mean(np.abs(grey - previous))) <= DUPLICATE_MAX_DIFF:
                        index += 1
                        continue   # a repeated frame: wait for the next real one
                    if cv2.compareHist(previous_hist, hist, cv2.HISTCMP_CORREL) < CUT_MAX_CORRELATION:
                        cuts.append(round(index / fps, 2))
                    else:
                        steps = max(1.0, (index - previous_index) / step)
                        (dx, dy), response = cv2.phaseCorrelate(previous, grey)
                        shifts.append((index / fps, float(dx) / steps, float(dy) / steps, float(response)))
                previous, previous_hist, previous_index = grey, hist, index
        index += 1
    return shifts, cuts


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
        duration = metadata.get('duration_seconds') or (frame_count / fps if fps else 0)
        shifts, cuts = measure_shifts(capture, fps, box)
        audio = usable_events(audio, duration, cuts)
        jolt = usable_events(jolt_scores(shifts), duration, cuts)
        moments = find_moments(audio, jolt)
        possible = [m for m in find_moments(audio, jolt, MAX_POSSIBLE, MIN_POSSIBLE_SCORE, exclude=moments)
                    if m['score'] < MIN_SCORE]

        for number, moment in enumerate(moments, start=1):
            t = moment['t_seconds']
            if out_dir:
                os.makedirs(out_dir, exist_ok=True)
                frames = []
                start, end = burst_window(t, duration)
                for step in range(BURST_FRAMES):
                    seconds = start + step * (end - start) / (BURST_FRAMES - 1)
                    frame, at = _read_at(capture, seconds, fps, frame_count)
                    if frame is not None:
                        frames.append((detection.crop_to_content(frame, box), evidence.format_timestamp(at)))
                sheet = evidence.write_sheet(frames, os.path.join(out_dir, f'moment{number}_burst.jpg'), max_columns=5)
                if sheet:
                    sheet.pop('grid')
                    sheet.pop('tiles')
                    artifacts.append({**sheet, 'kind': 'burst', 't_seconds': t,
                                      'label': f'Moment {number}: {len(frames)} frames, {start:.1f}-{end:.1f}s'})

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
            # An edited clip -- cuts between shots -- is not continuous footage,
            # and a reader should know the moments were found around its cuts.
            'scene_cuts': len(cuts),
        },
        'moments': moments,
        'possible': possible,
    }
    return summary, artifacts
