"""Throttled YouTube downloads for the evaluation datasets (NINA, DoTA).

Both publish labels keyed to YouTube video ids and nothing else, so the media
has to be fetched from YouTube: slowly, resumably, and without mistaking
YouTube refusing a request for the video being gone.

The media is YouTube content: it stays local and is never redistributed.
"""

from __future__ import annotations

import json
import random
import time
from pathlib import Path

# YouTube refusing or throttling the request -- not a fact about the video.
REFUSALS = ('429', 'Too Many Requests', 'Sign in to confirm', '403')
MEDIA_SUFFIXES = ('.mp4', '.mkv', '.m4a', '.webm', '.opus', '.mp3', '.wav', '.ogg', '.aac')


def media_file(directory: Path, video_id: str) -> Path | None:
    for suffix in MEDIA_SUFFIXES:
        candidate = directory / f'{video_id}{suffix}'
        if candidate.exists():
            return candidate
    return None


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


def download_all(video_ids: list[str], out_dir: Path, unavailable_path: Path, fmt: str,
                 pause: float, extra: dict | None = None) -> None:
    """Fetch each id not already on disk or known gone; resumable.

    A video that is removed or private is recorded in `unavailable_path` and
    never retried. One YouTube refuses is skipped for this run only; five in a
    row is a block, and the run stops so a later one can resume.
    """
    import yt_dlp

    out_dir.mkdir(parents=True, exist_ok=True)
    unavailable = json.loads(unavailable_path.read_text()) if unavailable_path.exists() else {}
    todo = [v for v in video_ids if not media_file(out_dir, v) and v not in unavailable]
    print(f'{len(video_ids)} videos; {len(todo)} to fetch, {len(unavailable)} known unavailable.', flush=True)
    options = {
        'format': fmt,
        'outtmpl': str(out_dir / '%(id)s.%(ext)s'),
        'quiet': True, 'no_warnings': True, 'noprogress': True,
        'retries': 3,
        # YouTube gates downloads behind a JavaScript challenge; without a
        # runtime yt-dlp is refused with 403s. Node is already installed for
        # the frontend.
        'js_runtimes': {'node': {}},
        **(extra or {}),
    }
    refused_in_a_row = 0
    for number, video_id in enumerate(todo, start=1):
        outcome = _download(yt_dlp, options, video_id, pause)
        if outcome == 'ok':
            refused_in_a_row = 0
            print(f'  {number}/{len(todo)} {video_id} ok', flush=True)
        elif outcome == 'refused':
            refused_in_a_row += 1
            print(f'  {number}/{len(todo)} {video_id} refused, skipped for now', flush=True)
            if refused_in_a_row >= 5:
                print('  YouTube is refusing repeatedly; stopping. Re-run later to resume.', flush=True)
                break
        else:
            refused_in_a_row = 0
            unavailable[video_id] = outcome
            unavailable_path.write_text(json.dumps(unavailable, indent=2))
            print(f'  {number}/{len(todo)} {video_id} unavailable: {outcome}', flush=True)
        time.sleep(pause + random.uniform(0, pause / 2))
