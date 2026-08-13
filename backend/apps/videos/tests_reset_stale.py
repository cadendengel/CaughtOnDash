"""Freeing jobs whose worker died, from the worker itself.

A worker that crashes mid-job leaves the video claimed and 'processing'
forever. Requeue skips that state on purpose -- it must not yank a job from a
worker that is genuinely running it -- so nothing ever cleared it, and the site
showed a progress bar that would never move. Clearing one needed a
hand-authenticated admin call, which is why it sat stuck.
"""

from datetime import timedelta
from unittest import mock

from django.test import Client, TestCase
from django.utils import timezone

from apps.videos.models import Video
from apps.videos.worker_services import STALE_PROCESSING_MINUTES

WORKER_TOKEN = 'test-worker-token'
URL = '/api/videos/worker/jobs/reset-stale/'


def _processing(title, last_seen_minutes_ago):
    return Video.objects.create(
        owner_clerk_user_id='u1',
        title=title,
        status='ready',
        visibility='public',
        approval_status='approved',
        analysis_status='processing',
        analysis_stage='claimed',
        analysis_progress=0,
        worker_id='dead-worker',
        worker_name='Dead Worker',
        worker_claimed_at=timezone.now() - timedelta(minutes=last_seen_minutes_ago),
        worker_last_seen_at=timezone.now() - timedelta(minutes=last_seen_minutes_ago),
    )


@mock.patch.dict('os.environ', {'WORKER_API_TOKEN': WORKER_TOKEN})
class WorkerResetStaleTests(TestCase):
    def setUp(self):
        self.client = Client()

    def _post(self, query='', token=WORKER_TOKEN):
        headers = {'HTTP_AUTHORIZATION': f'Bearer {token}'} if token else {}
        return self.client.post(URL + query, content_type='application/json', **headers)

    def test_it_frees_a_job_whose_worker_went_quiet(self):
        video = _processing('Orphaned by a crash', 60)

        response = self._post()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['reset_count'], 1)
        video.refresh_from_db()
        self.assertEqual(video.analysis_status, 'pending')
        self.assertEqual(video.analysis_stage, 'queued')
        self.assertIsNone(video.worker_id)
        self.assertIn('heartbeat timeout', video.analysis_error)

    def test_it_leaves_a_job_a_live_worker_is_holding(self):
        """The one thing this must never do."""
        video = _processing('Being worked on right now', 0)

        self.assertEqual(self._post().json()['reset_count'], 0)
        video.refresh_from_db()
        self.assertEqual(video.analysis_status, 'processing')
        self.assertEqual(video.worker_id, 'dead-worker')

    def test_it_leaves_completed_videos_alone(self):
        done = Video.objects.create(
            owner_clerk_user_id='u1', title='Done', status='ready',
            approval_status='approved', analysis_status='complete')

        self.assertEqual(self._post().json()['reset_count'], 0)
        done.refresh_from_db()
        self.assertEqual(done.analysis_status, 'complete')

    def test_a_freed_job_becomes_queued_again(self):
        """It has to re-enter the queue, or freeing it achieves nothing."""
        from apps.videos.worker_services import claimable_jobs

        _processing('Orphaned', 60)
        self.assertEqual(claimable_jobs().count(), 0)

        self._post()

        self.assertEqual(claimable_jobs().count(), 1)

    def test_a_custom_timeout_is_honoured(self):
        _processing('Quiet for six minutes', 6)

        self.assertEqual(self._post('?timeout_minutes=10').json()['reset_count'], 0)
        self.assertEqual(self._post('?timeout_minutes=5').json()['reset_count'], 1)

    def test_a_zero_timeout_is_refused(self):
        """Zero would reset jobs a live worker is holding."""
        _processing('Being worked on right now', 0)

        response = self._post('?timeout_minutes=0')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Video.objects.get().analysis_status, 'processing')

    def test_a_nonsense_timeout_is_refused(self):
        response = self._post('?timeout_minutes=soon')

        self.assertEqual(response.status_code, 400)

    def test_it_defaults_to_the_shared_staleness_threshold(self):
        """So the worker and the moderation 'stuck' group agree on stale."""
        _processing('Just over the line', STALE_PROCESSING_MINUTES + 1)

        self.assertEqual(self._post().json()['reset_count'], 1)

    def test_it_requires_the_worker_token(self):
        _processing('Orphaned', 60)

        self.assertIn(self._post(token=None).status_code, (401, 403))
        self.assertEqual(Video.objects.get().analysis_status, 'processing')

    def test_get_is_not_allowed(self):
        self.assertEqual(
            self.client.get(URL, HTTP_AUTHORIZATION=f'Bearer {WORKER_TOKEN}').status_code, 405)
