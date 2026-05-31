from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Iterable

import requests
from django.db import close_old_connections, transaction
from django.utils import timezone

from apps.videos.analysis_validator import validate_analysis
from apps.videos.models import AIAnalysis, Video
from apps.videos.tagging import normalize_video_tags

MAX_VIDEO_DURATION_SECONDS = int(os.getenv('AI_MAX_VIDEO_SECONDS', '60'))


def _remove_path(path: str | None) -> None:
    if not path:
        return
    try:
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        elif os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def _ffprobe_duration(source: str) -> int | None:
    try:
        proc = subprocess.run(
            [
                'ffprobe',
                '-v',
                'error',
                '-show_entries',
                'format=duration',
                '-of',
                'default=noprint_wrappers=1:nokey=1',
                source,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        value = float((proc.stdout or '').strip())
        return max(0, int(round(value)))
    except Exception:
        return None


def probe_video_duration_seconds(source: str) -> int | None:
    return _ffprobe_duration(source)


def probe_uploaded_video_duration(upload_bytes: bytes, upload_name: str = '') -> int | None:
    suffix = Path(upload_name).suffix if upload_name else ''
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix or '.mp4')
    try:
        temp_file.write(upload_bytes)
        temp_file.flush()
        temp_file.close()
        return _ffprobe_duration(temp_file.name)
    finally:
        _remove_path(temp_file.name)


def _extract_audio(playback_url: str) -> str | None:
    try:
        tmp_fd, tmp_path = tempfile.mkstemp(suffix='.wav')
        os.close(tmp_fd)
        subprocess.run(
            [
                'ffmpeg',
                '-y',
                '-i',
                playback_url,
                '-ar',
                '16000',
                '-ac',
                '1',
                '-f',
                'wav',
                tmp_path,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return tmp_path
    except Exception:
        return None


def _hf_transcribe(audio_path: str, hf_token: str) -> str | None:
    if not audio_path or not hf_token:
        return None

    model_name = os.getenv('HF_ASR_MODEL', 'openai/whisper-large-v2')
    endpoint = f'https://api-inference.huggingface.co/models/{model_name}'
    try:
        with open(audio_path, 'rb') as audio_file:
            response = requests.post(
                endpoint,
                headers={'Authorization': f'Bearer {hf_token}'},
                data=audio_file.read(),
                timeout=120,
            )
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict):
            return str(data.get('text') or '').strip() or None
    except Exception:
        return None
    return None


def _sample_frames(playback_url: str, count: int = 6) -> list[str]:
    out_paths: list[str] = []
    try:
        duration = _ffprobe_duration(playback_url)
        if duration and duration > 0:
            timestamps = [(index * duration) / (count + 1) for index in range(1, count + 1)]
        else:
            timestamps = [0.5 * index for index in range(count)]

        tmpdir = tempfile.mkdtemp(prefix='frames_')
        for idx, timestamp in enumerate(timestamps):
            out_path = os.path.join(tmpdir, f'frame_{idx:03d}.jpg')
            try:
                subprocess.run(
                    [
                        'ffmpeg',
                        '-ss',
                        str(timestamp),
                        '-i',
                        playback_url,
                        '-frames:v',
                        '1',
                        '-q:v',
                        '2',
                        out_path,
                    ],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                if os.path.exists(out_path):
                    out_paths.append(out_path)
            except Exception:
                continue
        return out_paths
    except Exception:
        return out_paths


def _run_yolo_on_frames(frame_paths: Iterable[str]) -> list[dict]:
    detections: list[dict] = []
    frame_list = list(frame_paths)
    if not frame_list:
        return detections

    try:
        from ultralytics import YOLO

        model_path = os.environ.get('YOLO_MODEL', 'yolov8n.pt')
        model = YOLO(model_path)
        for frame in frame_list:
            try:
                results = model.predict(source=frame, imgsz=640, conf=0.25, verbose=False)
                for result in results:
                    boxes = getattr(result, 'boxes', None)
                    if boxes is None:
                        continue
                    for box in boxes:
                        try:
                            xyxy = box.xyxy[0].tolist() if hasattr(box, 'xyxy') else []
                            confidence = float(box.conf[0]) if hasattr(box, 'conf') else float(box[4])
                            cls = int(box.cls[0]) if hasattr(box, 'cls') else int(box[5])
                            label = model.names.get(cls, str(cls)) if hasattr(model, 'names') else str(cls)
                        except Exception:
                            xyxy = []
                            confidence = 0.0
                            label = 'unknown'
                        detections.append(
                            {
                                'label': label,
                                'confidence': confidence,
                                'bbox': xyxy,
                                'frame': frame,
                            }
                        )
            except Exception:
                continue
    except Exception:
        return detections

    return detections


def suggest_tags_from_text(text: str) -> list[dict[str, str]]:
    text_low = (text or '').lower()
    suggestions: list[dict[str, str]] = []
    if any(keyword in text_low for keyword in ('brake', 'braking', 'brake check', 'screech')):
        suggestions.append({'text': 'brake check', 'source': 'ai'})
    if any(keyword in text_low for keyword in ('near miss', 'near-miss', 'almost', 'close call')):
        suggestions.append({'text': 'near miss', 'source': 'ai'})
    if any(keyword in text_low for keyword in ('collision', 'hit', 'crash', 'bumper')):
        suggestions.append({'text': 'collision', 'source': 'ai'})
    if any(keyword in text_low for keyword in ('tailgating', 'tailgate')):
        suggestions.append({'text': 'tailgating', 'source': 'ai'})

    if not suggestions and text_low.strip():
        suggestions.append({'text': 'analysis-suggested', 'source': 'ai'})

    return suggestions


def _build_analysis_payload(
    video: Video,
    transcript: str | None,
    detections: list[dict],
    suggestions: list[dict],
    generated_by: str,
) -> dict:
    summary = transcript or ', '.join([item['text'] for item in suggestions]) if suggestions else ''
    events = [
        {
            'label': item['text'],
            'start_time_seconds': 0,
            'confidence': 0.5,
            'source': 'ai',
            'metadata': {},
        }
        for item in suggestions
    ]
    return {
        'schema_version': '1.0',
        'generated_by': generated_by,
        'generated_at': timezone.now().isoformat(),
        'summary': summary,
        'events': events,
        'metadata': {
            'transcript': transcript,
            'detections': detections,
        },
    }


def run_video_analysis(video_id, generated_by: str = 'render-on-demand') -> AIAnalysis:
    video = Video.objects.get(id=video_id, deleted_at__isnull=True)
    if not video.playback_url:
        raise RuntimeError('Video does not have a playback URL yet.')

    video.analysis_status = 'processing'
    video.analysis_error = ''
    video.save(update_fields=['analysis_status', 'analysis_error', 'updated_at'])

    temp_paths: list[str] = []
    try:
        transcript = None
        detections: list[dict] = []
        hf_token = os.environ.get('HF_API_TOKEN')

        if hf_token:
            audio_path = _extract_audio(video.playback_url)
            if audio_path:
                temp_paths.append(audio_path)
                transcript = _hf_transcribe(audio_path, hf_token)

        frames = _sample_frames(video.playback_url, count=6)
        temp_paths.extend(frames)
        if frames:
            detections = _run_yolo_on_frames(frames)

        text_source = f"{video.title} {video.description} {transcript or ''}".strip()
        suggestions = suggest_tags_from_text(text_source)
        analysis_payload = _build_analysis_payload(video, transcript, detections, suggestions, generated_by)
        validate_analysis(analysis_payload)

        analysis = AIAnalysis.objects.create(
            video=video,
            schema_version='1.0',
            generated_by=generated_by,
            analysis=analysis_payload,
        )

        existing_tags = normalize_video_tags(video.tags or [], default_source='user')
        existing_texts = {tag['text'].lower() for tag in existing_tags}
        for suggestion in suggestions:
            if suggestion['text'].lower() not in existing_texts:
                existing_tags.append(suggestion)

        video.tags = existing_tags
        video.analysis_status = 'ready'
        video.analysis_error = ''
        video.save(update_fields=['tags', 'analysis_status', 'analysis_error', 'updated_at'])
        return analysis
    except Exception as exc:
        video.analysis_status = 'failed'
        video.analysis_error = str(exc)
        video.save(update_fields=['analysis_status', 'analysis_error', 'updated_at'])
        raise
    finally:
        for path in temp_paths:
            _remove_path(path)


def schedule_video_analysis(video_id, generated_by: str = 'render-on-demand', force: bool = False) -> Video:
    video = Video.objects.get(id=video_id, deleted_at__isnull=True)
    if not force and video.analysis_status in {'queued', 'processing'}:
        return video

    video.analysis_status = 'queued'
    video.analysis_error = ''
    video.save(update_fields=['analysis_status', 'analysis_error', 'updated_at'])

    def runner() -> None:
        close_old_connections()
        try:
            run_video_analysis(video.id, generated_by=generated_by)
        except Exception:
            pass
        finally:
            close_old_connections()

    transaction.on_commit(lambda: threading.Thread(target=runner, daemon=True).start())
    return video


def analyze_ready_videos(limit: int = 10, generated_by: str = 'render-shell') -> int:
    candidates = (
        Video.objects.filter(status='ready', deleted_at__isnull=True)
        .order_by('created_at')[:limit]
    )
    processed = 0
    for video in candidates:
        tags = normalize_video_tags(video.tags or [], default_source='user')
        if any(tag.get('source') == 'ai' for tag in tags):
            continue
        try:
            run_video_analysis(video.id, generated_by=generated_by)
            processed += 1
        except Exception:
            continue
    return processed