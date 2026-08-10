"""Version-aware bulk requeue: re-run the corpus after an analyzer change.

The point is to refresh stale results when the algorithm changes without
re-moderating anything -- a version bump is not a moderation decision -- and
without touching in-flight work or videos already on the current version.
"""

import json
from unittest import mock

from django.test import Client, TestCase
from django.utils import timezone

from apps.videos.models import AnalysisRun, Video
from apps.videos.worker_services import requeue_stale_version

WORKER_TOKEN = 'test-worker-token'


def _analyzed_video(version, **overrides):
    fields = dict(
        owner_clerk_user_id='user_owner',
        title='Clip',
        status='ready',
        visibility='public',
        approval_status='approved',
        analysis_status='complete',
        analysis_requested_at=timezone.now(),
        ai_metadata={'analyzer_version': version} if version else {},
    )
    fields.update(overrides)
    return Video.objects.create(**fields)


class RequeueStaleVersionTests(TestCase):
    def test_requeues_only_videos_not_on_the_target_version(self):
        old = _analyzed_video('detect-1.0')
        current = _analyzed_video('detect-2.0')

        result = requeue_stale_version('detect-2.0', requested_by='w1')

        self.assertTrue(result['success'])
        self.assertEqual(result['requeued'], 1)
        self.assertEqual(result['skipped_current_version'], 1)

        old.refresh_from_db()
        current.refresh_from_db()
        self.assertEqual(old.analysis_status, 'pending')      # queued for re-run
        self.assertEqual(current.analysis_status, 'complete')  # left alone

    def test_a_requeued_video_keeps_its_approval(self):
        # A version bump is not a re-moderation: the video must NOT go back to
        # pending_review, or a full re-run would need the whole site re-approved.
        old = _analyzed_video('detect-1.0')

        requeue_stale_version('detect-2.0')

        old.refresh_from_db()
        self.assertEqual(old.approval_status, 'approved')
        self.assertEqual(old.analysis_status, 'pending')

    def test_missing_version_metadata_counts_as_stale(self):
        no_version = _analyzed_video(None)  # analyzed before versions existed

        result = requeue_stale_version('detect-2.0')

        self.assertEqual(result['requeued'], 1)
        no_version.refresh_from_db()
        self.assertEqual(no_version.analysis_status, 'pending')

    def test_in_flight_and_queued_work_is_left_alone(self):
        processing = _analyzed_video('detect-1.0', analysis_status='processing')
        pending = _analyzed_video('detect-1.0', analysis_status='pending')

        result = requeue_stale_version('detect-2.0')

        self.assertEqual(result['requeued'], 0)
        processing.refresh_from_db()
        pending.refresh_from_db()
        self.assertEqual(processing.analysis_status, 'processing')
        self.assertEqual(pending.analysis_status, 'pending')

    def test_unapproved_and_deleted_videos_are_ignored(self):
        unapproved = _analyzed_video('detect-1.0', approval_status='pending_review')
        deleted = _analyzed_video('detect-1.0', deleted_at=timezone.now())
        not_ready = _analyzed_video('detect-1.0', status='pending')

        result = requeue_stale_version('detect-2.0')

        self.assertEqual(result['requeued'], 0)

    def test_it_is_idempotent(self):
        _analyzed_video('detect-1.0')

        first = requeue_stale_version('detect-2.0')
        # Simulate the worker having re-run it onto the new version.
        v = Video.objects.get()
        v.analysis_status = 'complete'
        v.ai_metadata = {'analyzer_version': 'detect-2.0'}
        v.save()
        second = requeue_stale_version('detect-2.0')

        self.assertEqual(first['requeued'], 1)
        self.assertEqual(second['requeued'], 0)

    def test_records_an_analysis_run_past_the_review_gate(self):
        old = _analyzed_video('detect-1.0')

        requeue_stale_version('detect-2.0', requested_by='w1')

        run = AnalysisRun.objects.filter(video=old).order_by('-attempt_number').first()
        self.assertIsNotNone(run)
        self.assertEqual(run.status, 'approved')  # not 'awaiting_approval'

    def test_blank_version_is_rejected(self):
        result = requeue_stale_version('')
        self.assertFalse(result['success'])


@mock.patch.dict('os.environ', {'WORKER_API_TOKEN': WORKER_TOKEN})
class RequeueEndpointTests(TestCase):
    """The worker-authenticated HTTP endpoint, driven the way the desktop worker
    calls in: a bearer token and no CSRF cookie."""

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.auth = {'HTTP_AUTHORIZATION': f'Bearer {WORKER_TOKEN}'}

    def _post(self, body):
        return self.client.post(
            '/api/videos/worker/jobs/requeue-stale/',
            data=json.dumps(body), content_type='application/json', **self.auth,
        )

    def test_requeues_stale_videos_over_http(self):
        _analyzed_video('detect-1.0')
        _analyzed_video('detect-2.0')

        response = self._post({'worker_id': 'w1', 'analyzer_version': 'detect-2.0'})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['requeued'], 1)
        self.assertEqual(body['skipped_current_version'], 1)

    def test_missing_analyzer_version_is_400(self):
        response = self._post({'worker_id': 'w1'})
        self.assertEqual(response.status_code, 400)

    def test_requires_worker_auth(self):
        _analyzed_video('detect-1.0')
        response = self.client.post(
            '/api/videos/worker/jobs/requeue-stale/',
            data=json.dumps({'analyzer_version': 'detect-2.0'}),
            content_type='application/json',
        )  # no bearer token
        self.assertIn(response.status_code, (401, 403))
