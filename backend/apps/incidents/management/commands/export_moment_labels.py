"""Write every moment verdict as CSV, for tuning the moment thresholds.

    python manage.py export_moment_labels > labels.csv

One row per verdict, with the signals the analyzer had when it listed the
moment, so thresholds can be re-scored without re-analyzing anything.
"""

import csv
import sys

from django.core.management.base import BaseCommand

from apps.incidents.models import MomentLabel


class Command(BaseCommand):
    help = 'Export moment verdicts (incident / not an incident) as CSV.'

    def handle(self, *args, **options):
        writer = csv.writer(self.stdout)
        writer.writerow(['video_id', 'analyzer_version', 't_seconds', 'listed_as', 'score', 'verdict',
                         'audio_rise_db', 'audio_strength', 'jolt_z', 'jolt_strength', 'labelled_at'])
        for label in MomentLabel.objects.order_by('labelled_at'):
            audio = (label.signals or {}).get('audio') or {}
            jolt = (label.signals or {}).get('jolt') or {}
            writer.writerow([label.video_id, label.analyzer_version, label.t_seconds, label.listed_as, label.score,
                             label.verdict, audio.get('rise_db', ''), audio.get('strength', ''),
                             jolt.get('z', ''), jolt.get('strength', ''), label.labelled_at.isoformat()])
