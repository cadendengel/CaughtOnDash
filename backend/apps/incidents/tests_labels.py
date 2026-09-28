"""Verdicts on candidate moments: the labelled set the thresholds get tuned on."""

import io
import json

from django.core.management import call_command
from django.test import TestCase, override_settings

from apps.incidents.crypto import generate_key
from apps.incidents.models import EvidenceRecord, MomentLabel
from apps.videos.models import Video

OWNER, STRANGER = 'user_owner', 'user_stranger'
COLLISION = {'t_seconds': 26.1, 'score': 0.91,
             'audio': {'t_seconds': 26.1, 'rise_db': 6.6, 'strength': 0.717},
             'jolt': {'t_seconds': 26.2, 'z': 15.2, 'strength': 1.0}}
CARRIER = {'t_seconds': 15.7, 'score': 0.577, 'audio': {'rise_db': 3.5, 'strength': 0.1},
           'jolt': {'z': 8.2, 'strength': 0.53}}


@override_settings(INCIDENT_ENCRYPTION_KEYS=[generate_key()])
class MomentLabelTests(TestCase):
    def setUp(self):
        self.video = Video.objects.create(owner_clerk_user_id=OWNER, status='ready',
                                          ai_metadata={'analyzer_version': 'detect-4.6'})
        EvidenceRecord.objects.create(video=self.video, moments={
            'available': True, 'moments': [COLLISION], 'possible': [CARRIER]})

    def _label(self, t, verdict, caller=OWNER):
        return self.client.post(f'/api/videos/{self.video.id}/incident/moments/label/',
                                data=json.dumps({'t_seconds': t, 'verdict': verdict}),
                                content_type='application/json', HTTP_X_CLERK_USER_ID=caller)

    def test_owner_labels_a_moment_with_the_signals_it_had(self):
        response = self._label(26.1, 'incident')
        self.assertEqual(response.status_code, 200, response.content)
        label = MomentLabel.objects.get()
        self.assertEqual((label.verdict, label.listed_as, label.analyzer_version), ('incident', 'moment', 'detect-4.6'))
        self.assertEqual(label.signals['jolt']['z'], 15.2)
        self.assertEqual(response.json()['moment_labels'][0]['verdict'], 'incident')

    def test_a_possible_can_be_labelled_too(self):
        # A "possible" confirmed as the incident is a miss worth knowing about.
        self._label(15.7, 'not_incident')
        self.assertEqual(MomentLabel.objects.get().listed_as, 'possible')

    def test_changing_and_clearing_a_verdict(self):
        self._label(26.1, 'incident')
        self._label(26.1, 'not_incident')
        self.assertEqual(MomentLabel.objects.get().verdict, 'not_incident')
        self._label(26.1, None)
        self.assertFalse(MomentLabel.objects.exists())

    def test_only_listed_moments_and_real_verdicts(self):
        self.assertEqual(self._label(12.0, 'incident').status_code, 404)
        self.assertEqual(self._label(26.1, 'maybe').status_code, 400)
        self.assertEqual(self._label('soon', 'incident').status_code, 400)
        self.assertFalse(MomentLabel.objects.exists())

    def test_strangers_cannot_label(self):
        self.assertEqual(self._label(26.1, 'incident', caller=STRANGER).status_code, 404)
        self.assertFalse(MomentLabel.objects.exists())

    def test_the_report_shows_labels_for_the_current_analysis_only(self):
        self._label(26.1, 'incident')
        MomentLabel.objects.create(video=self.video, t_seconds=8.0, analyzer_version='detect-4.5',
                                   verdict='not_incident', labelled_by=OWNER)
        report = self.client.get(f'/api/videos/{self.video.id}/incident/', HTTP_X_CLERK_USER_ID=OWNER).json()
        self.assertEqual([(l['t_seconds'], l['verdict']) for l in report['moment_labels']], [(26.1, 'incident')])

    def test_export_writes_one_row_per_verdict(self):
        self._label(26.1, 'incident')
        self._label(15.7, 'not_incident')
        out = io.StringIO()
        call_command('export_moment_labels', stdout=out)
        lines = out.getvalue().strip().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertIn('incident,6.6,0.717,15.2,1.0', lines[1])
