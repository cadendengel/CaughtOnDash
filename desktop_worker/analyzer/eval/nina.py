"""Measure the moment detector's audio signal against NINA's labelled car sounds.

eval/nexar.py tested the camera-jolt half of a moment and found it fires
every few seconds on ordinary driving, so the sound is what makes a moment.
NINA (Naturalistic IN-vehicle Audio, github.com/axa-rev-research/NINA-Dataset)
labels crash sounds in 212 YouTube dashcam and phone recordings, alongside the
sounds most likely to be mistaken for one: horns, potholes, doors, screams,
tyre skids, music, talking, sirens, rain and hail, and plain driving.

The full audio of each video is measured rather than NINA's trimmed clips:
audio_rises compares each 100 ms peak to the surrounding 3 s, so it needs the
continuous track to behave as it does on real footage.

Three steps, each resumable:

    python eval/nina.py fetch     # audio only, via yt-dlp, throttled
    python eval/nina.py measure   # audio_rises per video, cached
    python eval/nina.py score     # per-class rates; with Nexar measured, a combined estimate

Labels come from a clone of the NINA repository (default ~/datasets/nina).
The audio is YouTube content: it stays local and is never redistributed.
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

ANALYZER = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ANALYZER))
sys.path.insert(0, str(Path(__file__).resolve().parent))

DEFAULT_DATA = Path.home() / 'datasets' / 'nina'
DEFAULT_NEXAR = Path.home() / 'datasets' / 'nexar'
# Rise over the local median, in dB. 3 is where moments.py starts counting a
# sound; 8 is full strength.
SWEEP_DB = (3, 4, 5, 6, 8, 10, 12)
# A labelled segment's edges are hand-placed; allow this much either side.
SEGMENT_SLACK_SECONDS = 0.5
AUDIO_SUFFIXES = ('.m4a', '.webm', '.opus', '.mp3', '.wav', '.ogg', '.aac')


# --- Labels -----------------------------------------------------------------

def read_labels(labels_dir: Path) -> dict[str, list[dict]]:
    """video id -> [{start, end, cls}], from Audacity label files.

    Lines starting with a backslash are Audacity's frequency ranges for the
    label above, not labels.
    """
    videos = {}
    for path in sorted(labels_dir.glob('*.txt')):
        segments = []
        for line in path.read_text(encoding='utf-8', errors='replace').replace('\r', '\n').splitlines():
            if not line.strip() or line.startswith('\\'):
                continue
            parts = line.split()
            if len(parts) < 3:
                continue
            try:
                start, end = float(parts[0]), float(parts[1])
            except ValueError:
                continue
            segments.append({'start': start, 'end': end, 'cls': parts[2].lower()})
        if segments:
            videos[path.stem] = segments
    return videos


# --- Fetch ------------------------------------------------------------------

def _audio_file(audio_dir: Path, video_id: str) -> Path | None:
    for suffix in AUDIO_SUFFIXES:
        candidate = audio_dir / f'{video_id}{suffix}'
        if candidate.exists():
            return candidate
    return None


def fetch(data: Path, pause: float) -> None:
    import yt_dlp

    videos = read_labels(data / 'labels')
    audio_dir = data / 'audio'
    audio_dir.mkdir(parents=True, exist_ok=True)
    unavailable_path = data / 'unavailable.json'
    unavailable = json.loads(unavailable_path.read_text()) if unavailable_path.exists() else {}

    todo = [v for v in videos if not _audio_file(audio_dir, v) and v not in unavailable]
    print(f'{len(videos)} labelled videos; {len(todo)} to fetch, {len(unavailable)} known unavailable.')
    options = {
        'format': 'bestaudio/best',
        'outtmpl': str(audio_dir / '%(id)s.%(ext)s'),
        'quiet': True, 'no_warnings': True, 'noprogress': True,
        'retries': 3,
        # YouTube now gates downloads behind a JavaScript challenge; without a
        # runtime yt-dlp is refused with 403s. Node is already installed for
        # the frontend.
        'js_runtimes': {'node': {}},
    }
    refused_in_a_row = 0
    for number, video_id in enumerate(todo, start=1):
        outcome = _download(yt_dlp, options, video_id, pause)
        if outcome == 'ok':
            refused_in_a_row = 0
            print(f'  {number}/{len(todo)} {video_id} ok', flush=True)
        elif outcome == 'refused':
            # Skipped for this run, not recorded: YouTube refused, the video
            # may be fine. Many refusals in a row is a block; stop and resume
            # later rather than skip the whole list.
            refused_in_a_row += 1
            print(f'  {number}/{len(todo)} {video_id} refused, skipped for now', flush=True)
            if refused_in_a_row >= 5:
                print('  YouTube is refusing repeatedly; stopping. Re-run later to resume.')
                break
        else:
            refused_in_a_row = 0
            unavailable[video_id] = outcome
            unavailable_path.write_text(json.dumps(unavailable, indent=2))
            print(f'  {number}/{len(todo)} {video_id} unavailable: {outcome}', flush=True)
        time.sleep(pause + random.uniform(0, pause / 2))


REFUSALS = ('429', 'Too Many Requests', 'Sign in to confirm', '403')


def _download(yt_dlp, options: dict, video_id: str, pause: float, attempts: int = 3) -> str:
    """'ok', 'refused' (YouTube said no, possibly transiently), or why the video is gone."""
    for attempt in range(1, attempts + 1):
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                ydl.download([f'https://www.youtube.com/watch?v={video_id}'])
            return 'ok'
        except Exception as exc:
            message = str(exc).splitlines()[0][:160]
            if not any(sign in message for sign in REFUSALS):
                return message
            if attempt < attempts:
                time.sleep(pause * 4)
    return 'refused'


# --- Measure ----------------------------------------------------------------

def rises_at_floor(samples) -> list[dict]:
    """Every rise above 0 dB, so thresholds can be swept afterwards."""
    import moments

    floor = moments.AUDIO_MIN_RISE_DB
    moments.AUDIO_MIN_RISE_DB = 0.0
    try:
        return moments.audio_rises(samples)
    finally:
        moments.AUDIO_MIN_RISE_DB = floor


def measure(data: Path) -> None:
    import moments

    cache = data / 'measured'
    cache.mkdir(parents=True, exist_ok=True)
    videos = read_labels(data / 'labels')
    done = 0
    for video_id in videos:
        target = cache / f'{video_id}.json'
        source = _audio_file(data / 'audio', video_id)
        if target.exists() or source is None:
            continue
        samples = moments.read_audio(str(source))
        if samples is None or not len(samples):
            record = {'id': video_id, 'error': 'no audio decoded'}
        else:
            record = {'id': video_id, 'duration': len(samples) / moments.AUDIO_RATE,
                      'rises': [[e['t_seconds'], e['rise_db']] for e in rises_at_floor(samples)]}
        target.write_text(json.dumps(record), encoding='utf-8')
        done += 1
        if done % 20 == 0:
            print(f'  {done} measured', flush=True)
    print(f'Measured {done} new videos.')


# --- Score ------------------------------------------------------------------

def segment_fires(segment: dict, rises: list[list[float]], threshold: float) -> bool:
    lo = segment['start'] - SEGMENT_SLACK_SECONDS
    hi = segment['end'] + SEGMENT_SLACK_SECONDS
    return any(lo <= t <= hi and rise >= threshold for t, rise in rises)


def rate_in(segments: list[dict], rises_by_video: dict, threshold: float) -> float:
    """Rises per second inside the given (video id, segment) spans."""
    seconds = sum(s['end'] - s['start'] for _, s in segments)
    count = sum(1 for vid, s in segments
                for t, rise in rises_by_video[vid] if s['start'] <= t <= s['end'] and rise >= threshold)
    return count / seconds if seconds else 0.0


def strength(value: float, low: float, high: float) -> float:
    if value <= low:
        return 0.0
    if value >= high:
        return 1.0
    return (value - low) / (high - low)


def combined_estimate(rises_by_video: dict, labels: dict, nexar: Path) -> dict | None:
    """Estimated moments per minute of ordinary driving, and share of crashes found.

    Treats sound and jolt as independent: pairs each jolt Nexar measured on
    ordinary driving with the chance, from NINA's driving segments, that a
    sound strong enough to complete the fusion falls within AGREE_SECONDS.
    For crashes, pairs the jolt Nexar measured at the event with the sound
    NINA measured in the crash segment. Independence is an assumption -- a
    real crash makes both at once -- so this bounds, rather than measures,
    the combined behaviour.
    """
    import moments
    import nexar as nexar_eval

    records = [json.loads(p.read_text(encoding='utf-8')) for p in sorted((nexar / 'measured').glob('*.json'))]
    records = [r for r in records if 'error' not in r]
    if not records:
        return None

    def jolt_strength(z):
        return min(strength(z, moments.JOLT_MIN_Z, moments.JOLT_FULL_Z), moments.SINGLE_SIGNAL_MAX)

    def audio_needed(j):
        """Smallest rise (dB) that, with jolt strength j, reaches MIN_SCORE."""
        # 1 - (1-a)(1-j) >= MIN_SCORE  ->  a >= 1 - (1-MIN_SCORE)/(1-j)
        a = 1 - (1 - moments.MIN_SCORE) / (1 - j) if j < 1 else 0.0
        if a > moments.SINGLE_SIGNAL_MAX:
            return None
        a = max(a, 0.0)
        return moments.AUDIO_MIN_RISE_DB + a * (moments.AUDIO_FULL_RISE_DB - moments.AUDIO_MIN_RISE_DB)

    driving = [(vid, s) for vid, segs in labels.items() if vid in rises_by_video for s in segs if s['cls'] == 'driving']
    window = 2 * moments.AGREE_SECONDS
    # For each agreement-sized stretch of ordinary driving, its loudest rise.
    # Measured directly rather than from a rate: one sound spans several
    # 100 ms windows, and counting each as a separate rise overstated how
    # often a sound lands beside a random jolt.
    stretch_peaks = []
    for vid, segment in driving:
        start = segment['start']
        while start + window <= segment['end']:
            stretch_peaks.append(max((rise for t, rise in rises_by_video[vid]
                                      if start <= t < start + window), default=0.0))
            start += window / 4
    stretch_peaks.sort()

    def chance_of_sound(threshold):
        if not stretch_peaks:
            return 0.0
        return (len(stretch_peaks) - bisect.bisect_left(stretch_peaks, threshold)) / len(stretch_peaks)

    false_moments = 0.0
    negative_seconds = 0.0
    for record in records:
        if record['label'] != 'negative':
            continue
        negative_seconds += record['duration']
        for event in nexar_eval.jolt_events(record):
            need = audio_needed(jolt_strength(event['z']))
            if need is None:
                continue
            false_moments += chance_of_sound(max(need, moments.AUDIO_MIN_RISE_DB))

    crash_peaks = [max((rise for t, rise in rises_by_video[vid]
                        if s['start'] - SEGMENT_SLACK_SECONDS <= t <= s['end'] + SEGMENT_SLACK_SECONDS), default=0.0)
                   for vid, segs in labels.items() if vid in rises_by_video for s in segs if s['cls'] == 'crash']
    found = []
    for record in records:
        if record['label'] != 'positive' or record.get('time_of_event') is None:
            continue
        near = [e['z'] for e in nexar_eval.jolt_events(record)
                if abs(e['t_seconds'] - record['time_of_event']) <= moments.AGREE_SECONDS]
        need = audio_needed(jolt_strength(max(near, default=0.0)))
        if need is None or not crash_peaks:
            found.append(0.0)
            continue
        found.append(sum(1 for peak in crash_peaks if peak >= max(need, moments.AUDIO_MIN_RISE_DB)) / len(crash_peaks))

    return {
        'false_moments_per_hour': 3600 * false_moments / negative_seconds if negative_seconds else None,
        'crashes_found': sum(found) / len(found) if found else None,
        'crash_segments': len(crash_peaks),
        'driving_stretches': len(stretch_peaks),
        'sound_by_chance': {db: chance_of_sound(db) for db in SWEEP_DB},
    }


def score(data: Path, nexar: Path) -> None:
    labels = read_labels(data / 'labels')
    rises_by_video = {}
    for path in sorted((data / 'measured').glob('*.json')):
        record = json.loads(path.read_text(encoding='utf-8'))
        if 'error' not in record:
            rises_by_video[record['id']] = record['rises']
    if not rises_by_video:
        raise SystemExit('Nothing measured yet: run `fetch` and `measure` first.')

    by_class = defaultdict(list)
    for vid, segments in labels.items():
        if vid in rises_by_video:
            for segment in segments:
                by_class[segment['cls']].append((vid, segment))

    classes = sorted(by_class, key=lambda c: (c != 'crash', -len(by_class[c])))
    result = {'videos': len(rises_by_video), 'segments': {c: len(v) for c, v in by_class.items()}, 'sweep': []}
    header = '| rise dB >= | ' + ' | '.join(classes) + ' | driving rises/min |'
    print(f"{len(rises_by_video)} videos measured. Share of labelled segments containing a rise:\n")
    print(header)
    print('|' + '---|' * (len(classes) + 2))
    for threshold in SWEEP_DB:
        shares = {c: sum(segment_fires(s, rises_by_video[v], threshold) for v, s in by_class[c]) / len(by_class[c])
                  for c in classes}
        per_minute = 60 * rate_in(by_class.get('driving', []), rises_by_video, threshold)
        result['sweep'].append({'db': threshold, 'shares': shares, 'driving_rises_per_minute': per_minute})
        print(f'| {threshold} | ' + ' | '.join(f'{100 * shares[c]:.0f}%' for c in classes) + f' | {per_minute:.2f} |')
    print('\nSegments per class: ' + ', '.join(f'{c} {len(by_class[c])}' for c in classes))

    estimate = combined_estimate(rises_by_video, labels, nexar)
    result['combined'] = estimate
    if estimate:
        print(f"\nCombined with Nexar's jolts (assuming the two are independent), at the current thresholds:")
        print(f"  estimated false moments per hour of ordinary driving: {estimate['false_moments_per_hour']:.2f}")
        print(f"  estimated share of crashes that would make a moment:   {100 * estimate['crashes_found']:.1f}% "
              f"(from {estimate['crash_segments']} crash segments)")
        print('  chance a 2 s stretch of ordinary driving holds a sound of at least: ' +
              ', '.join(f"{db} dB {100 * p:.0f}%" for db, p in estimate['sound_by_chance'].items()) +
              f" ({estimate['driving_stretches']} stretches)")
    (data / 'score.json').write_text(json.dumps(result, indent=2), encoding='utf-8')


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('command', choices=('fetch', 'measure', 'score'))
    parser.add_argument('--data', type=Path, default=DEFAULT_DATA)
    parser.add_argument('--nexar', type=Path, default=DEFAULT_NEXAR)
    parser.add_argument('--pause', type=float, default=8.0, help='fetch: seconds between downloads')
    args = parser.parse_args(argv)
    if args.command == 'fetch':
        fetch(args.data, args.pause)
    elif args.command == 'measure':
        measure(args.data)
    else:
        score(args.data, args.nexar)


if __name__ == '__main__':
    main()
