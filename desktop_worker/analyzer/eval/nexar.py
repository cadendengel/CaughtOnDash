"""Measure the moment detector's camera-jolt signal against Nexar's labelled clips.

The thresholds in moments.py were set on one real collision and the 16 false
alarms around it. Nexar's collision-prediction set has 1,500 US dashcam clips,
half with an annotated event time (a collision or near-miss) and half ordinary
driving, which turns "it found the one crash" into a hit rate and a false-alarm
rate.

Only the jolt half can be tested here: Nexar removed the audio from every clip,
and a moment needs sound and jolt to agree. So this measures how often the jolt
fires at the event and how often it fires anywhere else -- the part of a false
moment the audio then has to veto.

Near-misses are labelled positive too. A swerve that avoids contact may jolt
the camera less or not at all, so a miss on a positive is not necessarily a
fault; the report says what share of positives had any jolt at all.

Three steps, each resumable:

    python eval/nexar.py fetch   [--limit N]   # download (needs Hugging Face access)
    python eval/nexar.py measure [--workers N] # one pass over the video; cached
    python eval/nexar.py score   [--window S]  # instant, re-run freely

Data lives outside the repo (default ~/datasets/nexar). Nexar's licence forbids
re-identifying people or vehicles: never run plate reading on these clips.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ANALYZER = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ANALYZER))

REPO_ID = 'nexar-ai/nexar_collision_prediction'
DEFAULT_DATA = Path.home() / 'datasets' / 'nexar'
CLASSES = ('positive', 'negative')
# Jolt thresholds to report, as robust z. 4 is where moments.py starts
# counting a jolt at all; 8 is where a jolt alone lists as "possible"; 12
# is full strength.
SWEEP_Z = (4, 6, 8, 10, 12, 15, 20, 30)


# --- Labels -----------------------------------------------------------------

def _column(row: dict, *needles: str) -> str | None:
    """The first column whose name contains every needle, case-insensitively."""
    for name in row:
        lowered = name.lower()
        if all(needle in lowered for needle in needles):
            return name
    return None


def _float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def read_labels(data: Path) -> list[dict]:
    """One entry per clip listed in train/<class>/metadata.csv.

    Column names are matched loosely rather than hard-coded, so a renamed
    column fails loudly below instead of silently scoring every clip as
    unlabelled.
    """
    clips = []
    for label in CLASSES:
        path = data / 'train' / label / 'metadata.csv'
        if not path.exists():
            continue
        with path.open(newline='', encoding='utf-8') as handle:
            for row in csv.DictReader(handle):
                name_col = _column(row, 'file') or _column(row, 'id')
                event_col = _column(row, 'event')
                alert_col = _column(row, 'alert')
                if name_col is None:
                    raise SystemExit(f'{path}: no file-name column in {list(row)}')
                name = row[name_col]
                if not name.lower().endswith('.mp4'):
                    name = f'{name}.mp4'
                clips.append({
                    'id': Path(name).stem,
                    'label': label,
                    'path': str(data / 'train' / label / name),
                    'time_of_event': _float(row.get(event_col)) if event_col else None,
                    'time_of_alert': _float(row.get(alert_col)) if alert_col else None,
                    'extra': {k: v for k, v in row.items() if k not in (name_col, event_col, alert_col)},
                })
    return clips


# --- Fetch ------------------------------------------------------------------

def fetch(data: Path, limit: int | None) -> None:
    from huggingface_hub import hf_hub_download, snapshot_download

    data.mkdir(parents=True, exist_ok=True)
    for label in CLASSES:
        hf_hub_download(REPO_ID, f'train/{label}/metadata.csv', repo_type='dataset', local_dir=data)

    clips = read_labels(data)
    chosen = []
    for label in CLASSES:
        of_label = [c for c in clips if c['label'] == label]
        chosen += of_label[:limit] if limit else of_label
    patterns = [os.path.relpath(c['path'], data).replace(os.sep, '/') for c in chosen]
    print(f'Fetching {len(patterns)} clips into {data} ...')
    snapshot_download(REPO_ID, repo_type='dataset', local_dir=data, allow_patterns=patterns, max_workers=8)
    print('Done.')


# --- Measure ----------------------------------------------------------------

def measure_one(clip: dict) -> dict:
    """Shifts and cuts for one clip, exactly as moments.find measures them."""
    import cv2

    import detection
    import moments

    cv2.setNumThreads(1)
    capture = cv2.VideoCapture(clip['path'])
    if not capture.isOpened():
        return {**clip, 'error': 'could not open'}
    try:
        fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        box = detection.content_box(capture, detection.frame_indices(frame_count, fps, 1.0, 60))
        shifts, cuts = moments.measure_shifts(capture, fps, box)
    finally:
        capture.release()
    audio = moments.read_audio(clip['path'])
    return {**clip, 'fps': fps, 'duration': frame_count / fps if fps else 0.0,
            'has_audio': audio is not None and len(audio) > 0,
            'shifts': [[round(v, 4) for v in s] for s in shifts], 'cuts': cuts}


def measure(data: Path, workers: int) -> None:
    cache = data / 'measured'
    cache.mkdir(parents=True, exist_ok=True)
    todo = [c for c in read_labels(data)
            if Path(c['path']).exists() and not (cache / f"{c['id']}.json").exists()]
    print(f'Measuring {len(todo)} clips with {workers} workers ...')
    done = 0
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for future in as_completed(pool.submit(measure_one, clip) for clip in todo):
            result = future.result()
            (cache / f"{result['id']}.json").write_text(json.dumps(result), encoding='utf-8')
            done += 1
            if done % 50 == 0 or done == len(todo):
                print(f'  {done}/{len(todo)}', flush=True)


# --- Score ------------------------------------------------------------------

def jolt_events(record: dict) -> list[dict]:
    """Every usable jolt above zero z, so thresholds can be swept afterwards."""
    import moments

    floor = moments.JOLT_MIN_Z
    moments.JOLT_MIN_Z = 0.0
    try:
        events = moments.jolt_scores([tuple(s) for s in record['shifts']])
    finally:
        moments.JOLT_MIN_Z = floor
    return moments.usable_events(events, record['duration'], record['cuts'])


def clip_outcome(record: dict, events: list[dict], threshold: float, window: float) -> dict:
    """For one clip at one threshold: did a jolt fire at the event, and elsewhere?

    "Elsewhere" on a positive is anything further than `window` from the
    labelled time -- a jolt somewhere else in a crash clip is a false alarm
    too.
    """
    firing = [e for e in events if e['z'] >= threshold]
    event_t = record.get('time_of_event')
    if record['label'] == 'positive' and event_t is not None:
        near = [e for e in firing if abs(e['t_seconds'] - event_t) <= window]
        far = [e for e in firing if abs(e['t_seconds'] - event_t) > window]
        best = max(near, key=lambda e: e['z'], default=None)
        return {'hit': bool(near), 'false_alarms': len(far),
                'offset': round(best['t_seconds'] - event_t, 2) if best else None}
    return {'hit': None, 'false_alarms': len(firing), 'offset': None}


def clip_auc(positive_scores: list[float], negative_scores: list[float]) -> float | None:
    """Chance that a random positive clip outscores a random negative one.

    0.5 is no better than a coin; 1.0 separates them perfectly. Ties count half.
    """
    if not positive_scores or not negative_scores:
        return None
    wins = 0.0
    for p in positive_scores:
        for n in negative_scores:
            wins += 1.0 if p > n else 0.5 if p == n else 0.0
    return wins / (len(positive_scores) * len(negative_scores))


def summarise(records: list[dict], window: float) -> dict:
    events = {r['id']: jolt_events(r) for r in records}
    positives = [r for r in records if r['label'] == 'positive' and r.get('time_of_event') is not None]
    negatives = [r for r in records if r['label'] == 'negative']
    minutes = sum(r['duration'] for r in records) / 60 or 1.0
    negative_seconds = sum(r['duration'] for r in negatives) or 1.0

    rows = []
    for threshold in SWEEP_Z:
        outcomes = {r['id']: clip_outcome(r, events[r['id']], threshold, window) for r in records}
        hits = sum(1 for r in positives if outcomes[r['id']]['hit'])
        neg_flagged = sum(1 for r in negatives if outcomes[r['id']]['false_alarms'])
        false_total = sum(o['false_alarms'] for o in outcomes.values())
        offsets = sorted(outcomes[r['id']]['offset'] for r in positives
                         if outcomes[r['id']]['offset'] is not None)
        # What a jolt firing at random, as often as it does on ordinary
        # driving, would still "catch" inside the hit window. A hit rate is
        # only worth its margin over this.
        per_second = sum(outcomes[r['id']]['false_alarms'] for r in negatives) / negative_seconds
        rows.append({
            'z': threshold,
            'hit_rate': hits / len(positives) if positives else None,
            'chance_hit_rate': 1 - math.exp(-per_second * 2 * window) if negatives else None,
            'negatives_flagged': neg_flagged / len(negatives) if negatives else None,
            'false_alarms_per_minute': false_total / minutes,
            # Signed: positive means the jolt comes after the labelled time.
            'median_offset_s': offsets[len(offsets) // 2] if offsets else None,
        })

    # Peak jolt at the event, whatever its size: how many positives show
    # anything at all, which bounds what any threshold could catch.
    peaks = []
    for r in positives:
        near = [e['z'] for e in events[r['id']] if abs(e['t_seconds'] - r['time_of_event']) <= window]
        peaks.append(max(near, default=0.0))
    # Is the clip's single strongest jolt the event? And does a clip's peak
    # jolt tell crash clips from ordinary ones at all?
    def strongest(record):
        return max(events[record['id']], key=lambda e: e['z'], default=None)

    top_at_event = sum(1 for r in positives
                       if (top := strongest(r)) and abs(top['t_seconds'] - r['time_of_event']) <= window)
    peak_of = {r['id']: (strongest(r) or {'z': 0.0})['z'] for r in records}
    return {
        'clips': len(records), 'positives': len(positives), 'negatives': len(negatives),
        'with_audio': sum(1 for r in records if r.get('has_audio')),
        'window_s': window, 'sweep': rows,
        'positive_peak_z': {
            'none': sum(1 for p in peaks if p == 0.0),
            'median': sorted(peaks)[len(peaks) // 2] if peaks else None,
        },
        'strongest_jolt_at_event': top_at_event / len(positives) if positives else None,
        'clip_peak_auc': clip_auc([peak_of[r['id']] for r in positives], [peak_of[r['id']] for r in negatives]),
    }


def _pct(value) -> str:
    return '-' if value is None else f'{100 * value:.1f}%'


def score(data: Path, window: float) -> None:
    records = [json.loads(p.read_text(encoding='utf-8')) for p in sorted((data / 'measured').glob('*.json'))]
    records = [r for r in records if 'error' not in r]
    if not records:
        raise SystemExit('Nothing measured yet: run `measure` first.')
    result = summarise(records, window)
    (data / 'score.json').write_text(json.dumps(result, indent=2), encoding='utf-8')

    print(f"{result['clips']} clips: {result['positives']} positive, {result['negatives']} negative; "
          f"{result['with_audio']} with audio. Hit window +/-{window}s.\n")
    print('| jolt z >= | hit rate | by chance | negatives flagged | false alarms / min | median offset |')
    print('|---|---|---|---|---|---|')
    for row in result['sweep']:
        offset = '-' if row['median_offset_s'] is None else f"{row['median_offset_s']:+.2f}s"
        print(f"| {row['z']} | {_pct(row['hit_rate'])} | {_pct(row['chance_hit_rate'])} | "
              f"{_pct(row['negatives_flagged'])} | {row['false_alarms_per_minute']:.2f} | {offset} |")
    peak = result['positive_peak_z']
    print(f"\nPositives with no jolt at all near the event: {peak['none']} "
          f"(median peak z at the event: {peak['median'] or 0:.1f})")
    print(f"Strongest jolt in the clip is the event: {_pct(result['strongest_jolt_at_event'])} of positives")
    auc = result['clip_peak_auc']
    print(f"Clip peak jolt separating positives from negatives (AUC, 0.5 = coin): "
          f"{'-' if auc is None else f'{auc:.3f}'}")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('command', choices=('fetch', 'measure', 'score'))
    parser.add_argument('--data', type=Path, default=DEFAULT_DATA)
    parser.add_argument('--limit', type=int, help='fetch: clips per class (default all)')
    parser.add_argument('--workers', type=int, default=max(1, (os.cpu_count() or 2) // 2))
    parser.add_argument('--window', type=float, default=1.0, help='score: seconds either side of the event')
    args = parser.parse_args(argv)
    if args.command == 'fetch':
        fetch(args.data, args.limit)
    elif args.command == 'measure':
        measure(args.data, args.workers)
    else:
        score(args.data, args.window)


if __name__ == '__main__':
    main()
