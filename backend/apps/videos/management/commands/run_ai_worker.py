
from __future__ import annotations

import os
import subprocess
import tempfile
import time
from typing import Iterable, Optional, List, Dict

import requests
from datetime import datetime
from django.core.management.base import BaseCommand

from apps.videos.analysis_validator import validate_analysis
from apps.videos.models import AIAnalysis, Video
from apps.videos.tagging import normalize_video_tags
class Command(BaseCommand):
    help = "Run a simple AI worker that simulates analysis and appends AI tags to videos."

    def add_arguments(self, parser):
        parser.add_argument(
            "--sleep",
            type=int,
            default=5,
            help="Seconds to sleep between polling loops when --loop is set",
        )
        parser.add_argument(
            "--loop",
            action="store_true",
            help="Run continuously polling for videos to analyze",
        )

    def handle(self, *args, **options):
        sleep_secs = options.get("sleep", 5)
        loop = options.get("loop", False)

        def run_once():
            videos = self._get_candidates()
            if not videos:
                self.stdout.write("No videos to analyze at this time.")
                return 0

            for v in videos:
                try:
                    self.stdout.write(f"Analyzing video {v.id} ({v.title})...")
                    # Mark as processing to avoid duplicate work
                    v.status = "processing"
                    v.save(update_fields=["status"])

                    # Attempt ASR (HF) and CV (YOLO) if available, then suggest tags
                    transcript = None
                    detections: List[Dict] = []
                    hf_token = os.environ.get("HF_API_TOKEN")
                    if v.playback_url and hf_token:
                        audio_path = self._extract_audio(v.playback_url)
                        if audio_path:
                            transcript = self._hf_transcribe(audio_path, hf_token)

                    # Sample frames and run YOLO detections (best-effort, lazy import)
                    if v.playback_url:
                        frames = self._sample_frames(v.playback_url, count=6)
                        if frames:
                            detections = self._run_yolo_on_frames(frames)

                    text_source = v.title + " " + v.description + (" " + (transcript or "") if transcript else "")
                    suggestions = self._suggest_tags_from_text(text_source)

                    # Build a minimal analysis JSON payload
                    analysis_payload = {
                        "schema_version": "1.0",
                        "generated_by": "run_ai_worker",
                        "generated_at": datetime.utcnow().isoformat() + "Z",
                        "summary": transcript or ", ".join([s["text"] for s in suggestions]) if (transcript or suggestions) else "",
                        "events": [
                            {
                                "label": s["text"],
                                "start_time_seconds": 0,
                                "confidence": 0.5,
                                "source": "ai",
                                "metadata": {},
                            }
                            for s in suggestions
                        ],
                        "metadata": {
                            "transcript": transcript,
                            "detections": detections,
                        },
                    }

                    # Validate payload before saving
                    try:
                        validate_analysis(analysis_payload)
                    except Exception as exc:  # pragma: no cover - validation/runtime
                        self.stderr.write(f"Analysis payload validation failed for {v.id}: {exc}")
                        v.status = "failed"
                        v.save(update_fields=["status"])
                        continue

                    # Persist AIAnalysis record
                    AIAnalysis.objects.create(video=v, schema_version="1.0", generated_by="run_ai_worker", analysis=analysis_payload)

                    # Merge with existing tags, preserving sources and adding AI tags
                    existing = normalize_video_tags(v.tags or [], default_source="user")
                    existing_texts = {t["text"].lower() for t in existing}
                    for s in suggestions:
                        if s["text"].lower() not in existing_texts:
                            existing.append(s)

                    v.tags = existing
                    v.save(update_fields=["tags", "updated_at", "status"])
                    # Leave as 'ready' after analysis
                    v.status = "ready"
                    v.save(update_fields=["status"])
                    self.stdout.write(f"Analysis complete for {v.id}: tags={[t['text'] for t in existing]}")
                except Exception as exc:  # pragma: no cover - worker runtime
                    self.stderr.write(f"Failed to analyze {v.id}: {exc}")
                    try:
                        v.status = "failed"
                        v.save(update_fields=["status"])
                    except Exception:
                        pass
            return len(videos)

        # run once or loop
        if loop:
            self.stdout.write("Starting AI worker loop. Polling for videos...")
            while True:  # pragma: no cover - long-running loop
                run_once()
                time.sleep(sleep_secs)
        else:
            count = run_once()
            self.stdout.write(f"Processed {count} videos.")

    def _get_candidates(self) -> Iterable[Video]:
        # Candidate videos: status == 'ready' and have no AI-sourced tags yet.
        qs = Video.objects.filter(status="ready").order_by("created_at")[:10]
        candidates = []
        for v in qs:
            tags = normalize_video_tags(v.tags or [], default_source="user")
            has_ai = any(t.get("source") == "ai" for t in tags)
            if not has_ai:
                candidates.append(v)
        return candidates

    def _suggest_tags_from_text(self, text: str) -> list[dict[str, object]]:
        # Very small heuristic-based tagger to bootstrap AI behavior.
        text_low = (text or "").lower()
        suggestions = []
        if any(k in text_low for k in ("brake", "braking", "brake check", "screech")):
            suggestions.append({"text": "brake check", "source": "ai"})
        if any(k in text_low for k in ("near miss", "near-miss", "almost", "close call")):
            suggestions.append({"text": "near miss", "source": "ai"})
        if any(k in text_low for k in ("collision", "hit", "crash", "bumper")):
            suggestions.append({"text": "collision", "source": "ai"})
        if any(k in text_low for k in ("tailgating", "tailgate")):
            suggestions.append({"text": "tailgating", "source": "ai"})

        # Always include a generic suggested tag to show provenance in UI for small samples
        if not suggestions and text_low.strip():
            suggestions.append({"text": "analysis-suggested", "source": "ai"})

        return suggestions

    def _extract_audio(self, playback_url: str) -> Optional[str]:
        """Download or read `playback_url` and extract a 16kHz mono WAV using ffmpeg.

        Returns path to WAV file or None on failure.
        """
        try:
            tmp_fd, tmp_path = tempfile.mkstemp(suffix=".wav")
            os.close(tmp_fd)
            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                playback_url,
                "-ar",
                "16000",
                "-ac",
                "1",
                "-f",
                "wav",
                tmp_path,
            ]
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return tmp_path
        except Exception as exc:
            self.stderr.write(f"Audio extraction failed for {playback_url}: {exc}")
            return None

    def _sample_frames(self, playback_url: str, count: int = 6) -> List[str]:
        """Sample up to `count` frames from the video URL using ffmpeg.

        Returns list of file paths for the extracted frames.
        """
        out_paths: List[str] = []
        try:
            # Get duration from video metadata if possible using ffprobe
            duration = None
            try:
                proc = subprocess.run([
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    playback_url,
                ], capture_output=True, text=True, check=True)
                duration = float(proc.stdout.strip())
            except Exception:
                duration = None

            timestamps: List[float] = []
            if duration and duration > 0:
                for i in range(1, count + 1):
                    ts = (i * duration) / (count + 1)
                    timestamps.append(ts)
            else:
                timestamps = [0.5 * i for i in range(count)]

            tmpdir = tempfile.mkdtemp(prefix="frames_")
            for idx, ts in enumerate(timestamps):
                out_path = os.path.join(tmpdir, f"frame_{idx:03d}.jpg")
                cmd = [
                    "ffmpeg",
                    "-ss",
                    str(ts),
                    "-i",
                    playback_url,
                    "-frames:v",
                    "1",
                    "-q:v",
                    "2",
                    out_path,
                ]
                try:
                    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    if os.path.exists(out_path):
                        out_paths.append(out_path)
                except Exception:
                    # ignore failures per-frame
                    continue
            return out_paths
        except Exception as exc:
            self.stderr.write(f"Frame sampling failed for {playback_url}: {exc}")
            return out_paths

    def _run_yolo_on_frames(self, frame_paths: List[str]) -> List[Dict]:
        """Run YOLO detection on frame images and return a list of detections.

        Each detection is a dict: {label, confidence, bbox: [x1,y1,x2,y2], frame_path}
        """
        detections: List[Dict] = []
        if not frame_paths:
            return detections
        try:
            # Import lazily so tests without ultralytics installed don't fail
            from ultralytics import YOLO

            model_path = os.environ.get("YOLO_MODEL", "yolov8n.pt")
            model = YOLO(model_path)
            for frame in frame_paths:
                try:
                    results = model.predict(source=frame, imgsz=640, conf=0.25, verbose=False)
                    for r in results:
                        try:
                            boxes = getattr(r, "boxes", None)
                            if boxes is None:
                                continue
                            for box in boxes:
                                # best-effort extraction of fields
                                try:
                                    xyxy = box.xyxy[0].tolist() if hasattr(box, "xyxy") else []
                                    conf = float(box.conf[0]) if hasattr(box, "conf") else float(box[4])
                                    cls = int(box.cls[0]) if hasattr(box, "cls") else int(box[5])
                                    label = model.names.get(cls, str(cls)) if hasattr(model, "names") else str(cls)
                                except Exception:
                                    # fallback parsing
                                    xyxy = []
                                    conf = 0.0
                                    label = "unknown"
                                detections.append({
                                    "label": label,
                                    "confidence": conf,
                                    "bbox": xyxy,
                                    "frame": frame,
                                })
                        except Exception:
                            continue
                except Exception:
                    continue
            return detections
        except Exception as exc:
            # ultralytics not available or detection failed
            self.stderr.write(f"YOLO detection skipped/failed: {exc}")
            return detections
