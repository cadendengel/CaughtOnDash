
from __future__ import annotations

import time

from django.core.management.base import BaseCommand

from apps.videos.analysis_service import analyze_ready_videos
class Command(BaseCommand):
    help = "Run on-demand AI analysis for ready videos using the shared backend pipeline."

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
            processed = analyze_ready_videos(limit=10, generated_by='render-shell')
            if processed == 0:
                self.stdout.write('No videos to analyze at this time.')
            else:
                self.stdout.write(f'Processed {processed} videos.')
            return processed

        # run once or loop
        if loop:
            self.stdout.write('Starting AI analysis loop. Polling for videos...')
            while True:  # pragma: no cover - long-running loop
                run_once()
                time.sleep(sleep_secs)
        else:
            count = run_once()
            self.stdout.write(f'Processed {count} videos.')
