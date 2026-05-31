from __future__ import annotations

import time
from typing import Iterable
from datetime import datetime

from django.core.management.base import BaseCommand

from apps.videos.models import Video
from apps.videos.tagging import normalize_video_tags
from apps.videos.analysis_validator import validate_analysis
from apps.videos.models import AIAnalysis


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

                    # Simple heuristic: suggest AI tags based on title/description keywords
                    suggestions = self._suggest_tags_from_text(v.title + " " + v.description)

                    # Build a minimal analysis JSON payload
                    analysis_payload = {
                        "schema_version": "1.0",
                        "generated_by": "run_ai_worker",
                        "generated_at": datetime.utcnow().isoformat() + "Z",
                        "summary": ", ".join([s["text"] for s in suggestions]) if suggestions else "",
                        "events": [
                            {
                                "label": s["text"],
                                "start_time_seconds": 0,
                                "confidence": 0.5,
                                "source": "ai",
                            }
                            for s in suggestions
                        ],
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
