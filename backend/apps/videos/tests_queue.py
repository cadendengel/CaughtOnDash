"""Queue listing, priority ordering and worker-side approval.

The worker could only ever ask for one job at a time, so it could not show a
queue. These endpoints give it the whole list -- both the approved queue and the
videos still awaiting a decision -- with enough history per row that a person
can judge whether to spend compute on a video before approving it.
"""

import json
from datetime import timedelta
from unittest import mock

from django.test import Client, TestCase
from django.utils import timezone

from apps.videos.models import AnalysisRun, Video
from apps.videos.worker_services import (
    claim_job,
    complete_job,
    decide_approval,
    get_next_pending_job,
    open_analysis_run,
    reorder_queue,
)

WORKER_TOKEN = 'test-worker-token'
OWNER = 'user_owner'


def _video(title, *, approved=True, requested_at=None, priority=0, **overrides):
    fields = dict(
        owner_clerk_user_id=OWNER,
        title=title,
        status='ready',
        approval_status='approved' if approved else 'pending_review',
        analysis_status='pending' if approved else 'cancelled',
        analysis_requested_at=requested_at or timezone.now(),
        analysis_priority=priority,
    )
    fields.update(overrides)
    video = Video.objects.create(**fields)
    open_analysis_run(video, OWNER)
    return video


class QueueOrderTests(TestCase):
    def test_higher_priority_runs_first(self):
        _video('normal', priority=0)
        _video('urgent', priority=5)
        self.assertEqual(get_next_pending_job().title, 'urgent')

    def test_equal_priority_stays_fifo(self):
        now = timezone.now()
        _video('second', requested_at=now)
        _video('first', requested_at=now - timedelta(minutes=10))
        self.assertEqual(get_next_pending_job().title, 'first')

    def test_priority_beats_age(self):
        # An old video does not outrank one you deliberately moved up.
        _video('old', requested_at=timezone.now() - timedelta(days=1), priority=0)
        _video('bumped', requested_at=timezone.now(), priority=1)
        self.assertEqual(get_next_pending_job().title, 'bumped')

    def test_unapproved_videos_are_not_in_the_queue(self):
        _video('waiting', approved=False, priority=99)
        self.assertIsNone(get_next_pending_job())


class ReorderTests(TestCase):
    def setUp(self):
        self.a = _video('a')
        self.b = _video('b')
        self.c = _video('c')

    def test_reorder_applies_the_given_order(self):
        result = reorder_queue([str(self.c.id), str(self.a.id), str(self.b.id)])
        self.assertTrue(result['success'])

        order = [v.title for v in Video.objects.all().order_by('-analysis_priority')]
        self.assertEqual(order, ['c', 'a', 'b'])
        self.assertEqual(get_next_pending_job().title, 'c')

    def test_reordering_leaves_later_arrivals_behind(self):
        # New uploads default to priority 0, so an explicitly ordered queue is
        # not disturbed by something arriving mid-review.
        reorder_queue([str(self.a.id), str(self.b.id), str(self.c.id)])
        _video('newcomer')

        self.assertEqual(get_next_pending_job().title, 'a')

    def test_unknown_id_is_refused_without_partial_writes(self):
        result = reorder_queue([str(self.a.id), '00000000-0000-0000-0000-000000000000'])
        self.assertFalse(result['success'])

        self.a.refresh_from_db()
        self.assertEqual(self.a.analysis_priority, 0)

    def test_non_list_is_refused(self):
        self.assertFalse(reorder_queue('not-a-list')['success'])


@mock.patch.dict('os.environ', {'WORKER_API_TOKEN': WORKER_TOKEN})
class QueueEndpointTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.auth = {'HTTP_AUTHORIZATION': f'Bearer {WORKER_TOKEN}'}

    def _get(self, path):
        return self.client.get(path, **self.auth)

    def _post(self, path, body):
        return self.client.post(
            path, data=json.dumps(body), content_type='application/json', **self.auth)

    def test_queue_lists_approved_videos_in_run_order(self):
        _video('second', priority=0)
        _video('first', priority=3)

        response = self._get('/api/videos/worker/jobs/')
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['count'], 2)
        self.assertEqual([i['title'] for i in payload['items']], ['first', 'second'])

    def test_queue_excludes_videos_awaiting_review(self):
        _video('waiting', approved=False)
        self.assertEqual(self._get('/api/videos/worker/jobs/').json()['count'], 0)

    def test_review_queue_lists_only_undecided_videos(self):
        _video('approved one')
        _video('waiting', approved=False)

        payload = self._get('/api/videos/worker/jobs/review/').json()
        self.assertEqual([i['title'] for i in payload['items']], ['waiting'])

    def test_rows_carry_what_is_needed_to_decide(self):
        _video('clip', duration_seconds=42, playback_url='https://cdn/clip.mp4')

        row = self._get('/api/videos/worker/jobs/').json()['items'][0]
        self.assertEqual(row['duration_seconds'], 42)
        self.assertEqual(row['video_url'], 'https://cdn/clip.mp4')
        self.assertEqual(row['attempt_number'], 1)
        self.assertEqual(row['previous_attempts'], 0)
        self.assertIsNone(row['last_result'])

    def test_a_returning_video_shows_its_attempt_count_and_last_result(self):
        video = _video('repeat offender')
        claim_job(video.id, 'worker-1', 'Worker One')
        complete_job(video.id, 'worker-1', 'a parking lot, no road', ['car'], [], {})

        # Sent back for another look.
        open_analysis_run(video, OWNER)
        Video.objects.filter(id=video.id).update(
            approval_status='approved', analysis_status='pending')

        row = self._get('/api/videos/worker/jobs/').json()['items'][0]
        self.assertEqual(row['attempt_number'], 2)
        self.assertEqual(row['previous_attempts'], 1)
        self.assertEqual(row['last_result']['summary'], 'a parking lot, no road')
        self.assertEqual(row['last_result']['tags'], ['car'])

    def test_worker_can_approve(self):
        video = _video('waiting', approved=False)

        response = self._post(f'/api/videos/worker/jobs/{video.id}/approval/', {'approve': True})
        self.assertEqual(response.status_code, 200)

        video.refresh_from_db()
        self.assertEqual(video.approval_status, 'approved')
        self.assertEqual(get_next_pending_job().id, video.id)

    def test_worker_can_reject(self):
        video = _video('waiting', approved=False)
        self._post(f'/api/videos/worker/jobs/{video.id}/approval/', {'approve': False})

        video.refresh_from_db()
        self.assertEqual(video.approval_status, 'rejected')

    def test_approve_must_be_a_boolean(self):
        video = _video('waiting', approved=False)
        response = self._post(
            f'/api/videos/worker/jobs/{video.id}/approval/', {'approve': 'yes'})
        self.assertEqual(response.status_code, 400)

    def test_reorder_endpoint(self):
        a, b = _video('a'), _video('b')
        response = self._post('/api/videos/worker/jobs/reorder/',
                              {'video_ids': [str(b.id), str(a.id)]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(get_next_pending_job().title, 'b')

    def test_every_queue_endpoint_needs_the_worker_token(self):
        video = _video('clip', approved=False)
        unauthenticated = Client()

        self.assertEqual(unauthenticated.get('/api/videos/worker/jobs/').status_code, 401)
        self.assertEqual(unauthenticated.get('/api/videos/worker/jobs/review/').status_code, 401)
        self.assertEqual(
            unauthenticated.post('/api/videos/worker/jobs/reorder/',
                                 data='{}', content_type='application/json').status_code, 401)
        self.assertEqual(
            unauthenticated.post(f'/api/videos/worker/jobs/{video.id}/approval/',
                                 data='{}', content_type='application/json').status_code, 401)

    def test_listing_does_not_scale_queries_with_queue_length(self):
        for index in range(12):
            _video(f'clip {index}')

        # One for the videos, one for their runs. Without the batched lookup
        # this grew with the queue.
        with self.assertNumQueries(2):
            self._get('/api/videos/worker/jobs/')
