"""A heartbeat must never undo a finished job.

worker_heartbeat updated the job with a bare save(), which writes every column
from the copy that was read. A heartbeat that loaded the row just before
complete_job committed wrote its stale snapshot back, reverting analysis_status
from 'complete' to 'processing'. In production that left a video displaying
"Processing: 100% (complete)" -- a status and a stage that contradict each
other.
"""

import json
from unittest import mock

from django.test import Client, TestCase
from django.utils import timezone

from apps.videos.models import Video

WORKER_TOKEN = 'test-worker-token'
WORKER = 'worker-1'


@mock.patch.dict('os.environ', {'WORKER_API_TOKEN': WORKER_TOKEN})
class HeartbeatDoesNotClobberJobsTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.auth = {'HTTP_AUTHORIZATION': f'Bearer {WORKER_TOKEN}'}
        self.video = Video.objects.create(
            owner_clerk_user_id='user_owner',
            title='Clip',
            status='ready',
            analysis_status='processing',
            analysis_stage='analyzing',
            analysis_progress=40,
            worker_id=WORKER,
            worker_claimed_at=timezone.now(),
        )

    def _heartbeat(self, **overrides):
        body = {
            'worker_id': WORKER,
            'worker_name': 'Worker One',
            'status': 'processing',
            'current_job_id': str(self.video.id),
            'stage': 'analyzing',
            'progress': 60,
        }
        body.update(overrides)
        return self.client.post(
            '/api/videos/worker/heartbeat/',
            data=json.dumps(body),
            content_type='application/json',
            **self.auth,
        )

    def test_heartbeat_updates_a_processing_job(self):
        self.assertEqual(self._heartbeat().status_code, 200)

        self.video.refresh_from_db()
        self.assertEqual(self.video.analysis_progress, 60)
        self.assertEqual(self.video.analysis_stage, 'analyzing')
        self.assertIsNotNone(self.video.worker_last_seen_at)

    def test_a_late_heartbeat_cannot_revive_a_completed_job(self):
        # The production failure: complete_job lands, then a heartbeat that was
        # already in flight arrives carrying the pre-completion state.
        Video.objects.filter(id=self.video.id).update(
            analysis_status='complete', analysis_stage='complete', analysis_progress=100)

        self.assertEqual(self._heartbeat(stage='analyzing', progress=60).status_code, 200)

        self.video.refresh_from_db()
        self.assertEqual(self.video.analysis_status, 'complete')
        self.assertEqual(self.video.analysis_stage, 'complete')
        self.assertEqual(self.video.analysis_progress, 100)

    def test_a_late_heartbeat_cannot_revive_a_failed_job(self):
        Video.objects.filter(id=self.video.id).update(
            analysis_status='failed', analysis_error='boom')

        self._heartbeat()

        self.video.refresh_from_db()
        self.assertEqual(self.video.analysis_status, 'failed')
        self.assertEqual(self.video.analysis_error, 'boom')

    def test_a_heartbeat_from_a_different_worker_is_ignored(self):
        # Otherwise a stale worker could drive the progress of a job another
        # worker has since claimed.
        self._heartbeat(worker_id='someone-else', progress=5)

        self.video.refresh_from_db()
        self.assertEqual(self.video.analysis_progress, 40)

    def test_heartbeat_does_not_touch_unrelated_fields(self):
        Video.objects.filter(id=self.video.id).update(
            ai_summary='previous summary', tags=[{'text': 'mine', 'source': 'user'}])

        self._heartbeat()

        self.video.refresh_from_db()
        self.assertEqual(self.video.ai_summary, 'previous summary')
        self.assertEqual(self.video.tags, [{'text': 'mine', 'source': 'user'}])

    def test_blank_stage_leaves_the_existing_stage_alone(self):
        # An idle-ish heartbeat should not blank out where the job had got to.
        self._heartbeat(stage='', progress=70)

        self.video.refresh_from_db()
        self.assertEqual(self.video.analysis_stage, 'analyzing')
        self.assertEqual(self.video.analysis_progress, 70)

    def test_heartbeat_for_an_unknown_job_is_harmless(self):
        response = self._heartbeat(
            current_job_id='00000000-0000-0000-0000-000000000000')
        self.assertEqual(response.status_code, 200)
