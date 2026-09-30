"""Test the whole moment rule -- sound and jolt together -- on DoTA's crash clips.

eval/nexar.py and eval/nina.py measured the two signals apart, on different
cameras and microphones, and their combination did not carry over to real
footage. DoTA (Detection of Traffic Anomaly, github.com/MoonBlvd/Detection-of-
Traffic-Anomaly) labels 4,677 dashcam crashes in 188 YouTube compilation
videos; 2,724 of them involve the recording car ("ego"), which is the case a
dashcam owner reports. Fetched from YouTube, they come with their sound, so
moments.py can be run as it runs on an upload: both signals, fused.

Each clip is cut from its source with a few seconds either side and scored:
does a moment land on the labelled anomaly, and how many land elsewhere, in
the normal driving around it?

Four steps, each resumable:

    python eval/dota.py fetch     # 360p source videos with sound, via yt-dlp, throttled
    python eval/dota.py cut       # one short file per ego clip
    python eval/dota.py measure   # both signals per clip, cached
    python eval/dota.py score     # the fused rule at the current thresholds

Labels come from a clone of the DoTA repository (default ~/datasets/dota).
Compilations often lay music over the footage; that is part of what is being
measured, not filtered out. The videos are YouTube content: keep them local.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ANALYZER = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ANALYZER))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import youtube  # noqa: E402  (eval/ is on the path above)

DEFAULT_DATA = Path.home() / 'datasets' / 'dota'
# DoTA's frame numbers count every third source frame (its video2frames
# downsampling), so frame n is at n * 3 / fps seconds.
DOWNSAMPLE = 3
# Context either side of a clip: audio_rises compares against the surrounding
# 3 s, and moments.py ignores a clip's first second.
MARGIN_SECONDS = 5.0
# A moment within this of the labelled anomaly span counts as on it.
HIT_SLACK_SECONDS = 1.0
FORMAT = 'bv*[height<=360][ext=mp4]+ba[ext=m4a]/b[height<=360]/bv*[height<=480]+ba/b'


# --- Labels -----------------------------------------------------------------

def read_clips(data: Path, ego_only: bool = True) -> dict[str, dict]:
    clips = {}
    for split in ('metadata_train.json', 'metadata_val.json'):
        path = data / 'dataset' / split
        if path.exists():
            clips.update(json.loads(path.read_text(encoding='utf-8')))
    for name, clip in clips.items():
        clip['source'] = name.rsplit('_', 1)[0]
        clip['ego'] = clip['anomaly_class'].startswith('ego')
    return {k: v for k, v in clips.items() if v['ego'] or not ego_only}


def clip_times(clip: dict, fps: float) -> dict:
    """Source-video seconds for the clip, and the anomaly within the cut section."""
    start = clip['video_start'] * DOWNSAMPLE / fps
    end = (clip['video_end'] + 1) * DOWNSAMPLE / fps
    section_start = max(0.0, start - MARGIN_SECONDS)
    return {
        'section_start': section_start,
        'section_duration': end + MARGIN_SECONDS - section_start,
        'event_start': start + clip['anomaly_start'] * DOWNSAMPLE / fps - section_start,
        'event_end': start + clip['anomaly_end'] * DOWNSAMPLE / fps - section_start,
    }


# --- Fetch and cut ----------------------------------------------------------

def fetch(data: Path, pause: float) -> None:
    sources = sorted({c['source'] for c in read_clips(data).values()})
    youtube.download_all(sources, data / 'videos', data / 'unavailable.json', fmt=FORMAT, pause=pause,
                         extra={'merge_output_format': 'mp4'})


def _fps(path: Path) -> float:
    import cv2

    capture = cv2.VideoCapture(str(path))
    try:
        return capture.get(cv2.CAP_PROP_FPS) or 30.0
    finally:
        capture.release()


def _cut_one(job: tuple[str, str, float, float, str]) -> tuple[str, bool]:
    name, source, start, duration, target = job
    completed = subprocess.run(
        ['ffmpeg', '-v', 'error', '-y', '-ss', f'{start:.3f}', '-i', source, '-t', f'{duration:.3f}',
         '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '26', '-c:a', 'aac', '-b:a', '128k', target],
        capture_output=True, timeout=300, check=False)
    return name, completed.returncode == 0 and os.path.exists(target)


def cut(data: Path, workers: int) -> None:
    clips = read_clips(data)
    out = data / 'clips'
    out.mkdir(parents=True, exist_ok=True)
    fps_of, jobs, times = {}, [], {}
    for name, clip in clips.items():
        source = youtube.media_file(data / 'videos', clip['source'])
        if source is None:
            continue
        if clip['source'] not in fps_of:
            fps_of[clip['source']] = _fps(source)
        t = clip_times(clip, fps_of[clip['source']])
        times[name] = {**t, 'fps': fps_of[clip['source']]}
        target = out / f'{name}.mp4'
        if not target.exists():
            jobs.append((name, str(source), t['section_start'], t['section_duration'], str(target)))
    (data / 'clip_times.json').write_text(json.dumps(times, indent=2), encoding='utf-8')
    print(f'Cutting {len(jobs)} clips from {len(fps_of)} source videos ...', flush=True)
    failed = 0
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for number, future in enumerate(as_completed(pool.submit(_cut_one, job) for job in jobs), start=1):
            if not future.result()[1]:
                failed += 1
            if number % 200 == 0:
                print(f'  {number}/{len(jobs)}', flush=True)
    print(f'Done; {failed} failed.')


# --- Measure ----------------------------------------------------------------

def measure_one(name: str, path: str, times: dict) -> dict:
    """Both signals, measured exactly as moments.find does, below its floors."""
    import cv2

    import detection
    import moments
    import nina

    cv2.setNumThreads(1)
    samples = moments.read_audio(path)
    capture = cv2.VideoCapture(path)
    if not capture.isOpened():
        return {'id': name, 'error': 'could not open'}
    try:
        fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        box = detection.content_box(capture, detection.frame_indices(frame_count, fps, 1.0, 60))
        shifts, cuts = moments.measure_shifts(capture, fps, box)
    finally:
        capture.release()
    has_audio = samples is not None and len(samples) > 0
    return {
        'id': name, 'duration': frame_count / fps if fps else 0.0, 'has_audio': has_audio,
        'rises': [[e['t_seconds'], e['rise_db'], e['peak_dbfs']] for e in nina.rises_at_floor(samples)] if has_audio else [],
        'shifts': [[round(v, 4) for v in s] for s in shifts], 'cuts': cuts,
        'event_start': times['event_start'], 'event_end': times['event_end'],
    }


def measure(data: Path, workers: int) -> None:
    times = json.loads((data / 'clip_times.json').read_text(encoding='utf-8'))
    cache = data / 'measured'
    cache.mkdir(parents=True, exist_ok=True)
    todo = [(n, str(data / 'clips' / f'{n}.mp4'), t) for n, t in times.items()
            if (data / 'clips' / f'{n}.mp4').exists() and not (cache / f'{n}.json').exists()]
    print(f'Measuring {len(todo)} clips with {workers} workers ...', flush=True)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(measure_one, *job) for job in todo]
        for number, future in enumerate(as_completed(futures), start=1):
            record = future.result()
            (cache / f"{record['id']}.json").write_text(json.dumps(record), encoding='utf-8')
            if number % 200 == 0:
                print(f'  {number}/{len(todo)}', flush=True)


# --- Score ------------------------------------------------------------------

def run_rule(record: dict) -> tuple[list[dict], list[dict]]:
    """(moments, possible) for one clip, at moments.py's current thresholds."""
    import moments

    audio = []
    for t, rise, peak in record['rises']:
        if rise > moments.AUDIO_MIN_RISE_DB:
            audio.append({'t_seconds': t, 'rise_db': rise, 'peak_dbfs': peak,
                          'strength': round(moments._ramp(rise, moments.AUDIO_MIN_RISE_DB,
                                                          moments.AUDIO_FULL_RISE_DB), 3)})
    audio = moments.usable_events(audio, record['duration'], record['cuts'])
    jolt = moments.usable_events(moments.jolt_scores([tuple(s) for s in record['shifts']]),
                                 record['duration'], record['cuts'])
    found = moments.find_moments(audio, jolt)
    possible = [m for m in moments.find_moments(audio, jolt, moments.MAX_POSSIBLE, moments.MIN_POSSIBLE_SCORE,
                                                exclude=found) if m['score'] < moments.MIN_SCORE]
    return found, possible


def on_event(moment: dict, record: dict) -> bool:
    return record['event_start'] - HIT_SLACK_SECONDS <= moment['t_seconds'] <= record['event_end'] + HIT_SLACK_SECONDS


def kind(moment: dict) -> str:
    if moment.get('audio') and moment.get('jolt'):
        return 'both'
    return 'sound only' if moment.get('audio') else 'jolt only'


def summarise(records: list[dict]) -> dict:
    """Hits on the anomaly and false moments in the normal driving around it."""
    import statistics

    counted = [r for r in records if r['has_audio']]
    hits = hit_possible = 0
    false_moments = 0
    false_possible = {'both': 0, 'sound only': 0, 'jolt only': 0}
    hit_possible_kind = {'both': 0, 'sound only': 0, 'jolt only': 0}
    offsets = []
    normal_seconds = 0.0
    for record in counted:
        found, possible = run_rule(record)
        on = [m for m in found if on_event(m, record)]
        if on:
            hits += 1
            offsets.append(min((m['t_seconds'] - record['event_start'] for m in on), key=abs))
        elif any(on_event(m, record) for m in possible):
            hit_possible += 1
            hit_possible_kind[kind(next(m for m in possible if on_event(m, record)))] += 1
        false_moments += sum(1 for m in found if not on_event(m, record))
        for m in possible:
            if not on_event(m, record):
                false_possible[kind(m)] += 1
        span = (record['event_end'] - record['event_start']) + 2 * HIT_SLACK_SECONDS
        normal_seconds += max(0.0, record['duration'] - span)

    hours = normal_seconds / 3600 or 1.0
    return {
        'clips': len(records), 'with_audio': len(counted),
        'moment_on_anomaly': hits / len(counted) if counted else None,
        'possible_only_on_anomaly': hit_possible / len(counted) if counted else None,
        'possible_on_anomaly_by_kind': hit_possible_kind,
        'false_moments_per_hour': false_moments / hours,
        'false_possible_per_hour': {k: v / hours for k, v in false_possible.items()},
        'normal_hours': normal_seconds / 3600,
        'median_offset_s': statistics.median(offsets) if offsets else None,
    }


def score(data: Path) -> None:
    records = [json.loads(p.read_text(encoding='utf-8')) for p in sorted((data / 'measured').glob('*.json'))]
    records = [r for r in records if 'error' not in r]
    if not records:
        raise SystemExit('Nothing measured yet: run fetch, cut and measure first.')
    result = summarise(records)
    (data / 'score.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    pct = lambda v: '-' if v is None else f'{100 * v:.1f}%'
    print(f"{result['clips']} ego crash clips measured, {result['with_audio']} with audio; "
          f"{result['normal_hours']:.2f} h of normal driving around them.\n")
    print(f"Moment on the anomaly:               {pct(result['moment_on_anomaly'])}")
    print(f"Only a 'possible' on the anomaly:    {pct(result['possible_only_on_anomaly'])} "
          f"{result['possible_on_anomaly_by_kind']}")
    print(f"False moments per hour (normal):     {result['false_moments_per_hour']:.1f}")
    print('False possibles per hour (normal):   ' +
          ', '.join(f'{k} {v:.1f}' for k, v in result['false_possible_per_hour'].items()))
    offset = result['median_offset_s']
    print(f"Median moment offset from anomaly start: {'-' if offset is None else f'{offset:+.2f}s'}")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('command', choices=('fetch', 'cut', 'measure', 'score'))
    parser.add_argument('--data', type=Path, default=DEFAULT_DATA)
    parser.add_argument('--pause', type=float, default=8.0, help='fetch: seconds between downloads')
    parser.add_argument('--workers', type=int, default=max(1, (os.cpu_count() or 2) // 2))
    args = parser.parse_args(argv)
    if args.command == 'fetch':
        fetch(args.data, args.pause)
    elif args.command == 'cut':
        cut(args.data, args.workers)
    elif args.command == 'measure':
        measure(args.data, args.workers)
    else:
        score(args.data)


if __name__ == '__main__':
    main()
