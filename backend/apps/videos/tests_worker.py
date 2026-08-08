import json
from unittest import mock

from django.test import Client, TestCase
from django.utils import timezone

from apps.videos.models import Video
from apps.videos.worker_services import claim_job, complete_job, get_next_pending_job

WORKER_TOKEN = 'test-worker-token'


class WorkerServiceTests(TestCase):
    def test_pending_video_can_be_claimed_and_completed(self):
        video = Video.objects.create(
            owner_clerk_user_id="local-test-user",
            title="Local worker smoke test",
            status="ready",
            approval_status="approved",
            analysis_status="pending",
            analysis_requested_at=timezone.now(),
        )

        pending_job = get_next_pending_job()
        self.assertIsNotNone(pending_job)
        self.assertEqual(pending_job.id, video.id)

        claim_result = claim_job(video.id, "local-worker", "Local Worker")
        self.assertTrue(claim_result["success"])

        complete_result = complete_job(
            video.id,
            "local-worker",
            "Smoke test summary",
            ["smoke", "test"],
            [],
            {"source": "test"},
        )
        self.assertTrue(complete_result["success"])

        refreshed_video = Video.objects.get(id=video.id)
        self.assertEqual(refreshed_video.analysis_status, "complete")
        self.assertEqual(refreshed_video.ai_summary, "Smoke test summary")
        self.assertEqual(refreshed_video.ai_tags, ["smoke", "test"])


@mock.patch.dict('os.environ', {'WORKER_API_TOKEN': WORKER_TOKEN})
class WorkerHttpApiTests(TestCase):
    """Exercise the worker API over HTTP.

    The service-layer tests above pass even when every worker POST is rejected
    at the middleware layer, so these drive the real request path with a
    CSRF-enforcing client -- the way the desktop worker actually calls in.
    """

    def setUp(self):
        # enforce_csrf_checks mirrors the desktop worker, which holds no CSRF
        # cookie and sends only a bearer token.
        self.client = Client(enforce_csrf_checks=True)
        self.auth = {'HTTP_AUTHORIZATION': f'Bearer {WORKER_TOKEN}'}

    def _post(self, path, body=None, **extra):
        return self.client.post(
            path,
            data=json.dumps(body or {}),
            content_type='application/json',
            **extra,
        )

    def _ready_job(self):
        return Video.objects.create(
            owner_clerk_user_id='local-test-user',
            title='HTTP worker smoke test',
            status='ready',
            approval_status='approved',
            analysis_status='pending',
            analysis_requested_at=timezone.now(),
        )

    def test_heartbeat_succeeds_without_csrf_token(self):
        response = self._post(
            '/api/videos/worker/heartbeat/',
            {'worker_id': 'local-worker', 'worker_name': 'Local Worker', 'status': 'idle'},
            **self.auth,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['worker_id'], 'local-worker')

    def test_heartbeat_rejects_missing_token(self):
        response = self._post(
            '/api/videos/worker/heartbeat/',
            {'worker_id': 'local-worker', 'worker_name': 'Local Worker', 'status': 'idle'},
        )
        self.assertEqual(response.status_code, 401)

    def test_full_job_lifecycle_over_http(self):
        video = self._ready_job()

        next_job = self.client.get('/api/videos/worker/jobs/next/', **self.auth)
        self.assertEqual(next_job.status_code, 200)
        self.assertEqual(next_job.json()['job']['job_id'], str(video.id))

        claim = self._post(
            f'/api/videos/worker/jobs/{video.id}/claim/',
            {'worker_id': 'local-worker', 'worker_name': 'Local Worker'},
            **self.auth,
        )
        self.assertEqual(claim.status_code, 200)

        progress = self._post(
            f'/api/videos/worker/jobs/{video.id}/progress/',
            {'worker_id': 'local-worker', 'stage': 'analyzing', 'progress': 50},
            **self.auth,
        )
        self.assertEqual(progress.status_code, 200)

        complete = self._post(
            f'/api/videos/worker/jobs/{video.id}/complete/',
            {
                'worker_id': 'local-worker',
                'summary': 'HTTP smoke test summary',
                'tags': ['smoke'],
                'events': [],
                'metadata': {},
            },
            **self.auth,
        )
        self.assertEqual(complete.status_code, 200)

        video.refresh_from_db()
        self.assertEqual(video.analysis_status, 'complete')
        self.assertEqual(video.analysis_progress, 100)
        self.assertEqual(video.ai_summary, 'HTTP smoke test summary')

    def test_unuploaded_video_is_not_offered_as_a_job(self):
        Video.objects.create(
            owner_clerk_user_id='local-test-user',
            title='Never uploaded',
            status='pending',
            analysis_status='pending',
        )
        response = self.client.get('/api/videos/worker/jobs/next/', **self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()['job'])

    def test_admin_job_endpoints_require_an_admin(self):
        response = self._post('/api/videos/admin/jobs/reset-stale/')
        self.assertEqual(response.status_code, 403)
