"""Approval gate and analysis history.

Nothing is analyzed until someone approves it, so compute is spent on videos
that were chosen rather than on everything uploaded. Rejection blocks analysis
only -- the video stays visible, because moderating content is a different
decision from moderating compute.

Every attempt is recorded as an AnalysisRun, created when analysis is requested
rather than when it finishes, so attempts that were never approved or that
failed still appear in the history.
"""

import json

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import AdminUser
from apps.videos.models import AnalysisRun, Video
from apps.videos.worker_services import (
    claim_job,
    complete_job,
    decide_approval,
    fail_job,
    get_next_pending_job,
    open_analysis_run,
)

OWNER = 'user_owner'
STRANGER = 'user_stranger'
ADMIN = 'user_admin'
WORKER = 'worker-1'


def _reviewable_video(**overrides):
    fields = dict(
        owner_clerk_user_id=OWNER,
        title='Clip',
        status='ready',
        visibility='public',
        approval_status='pending_review',
        analysis_status='cancelled',
        analysis_requested_at=timezone.now(),
    )
    fields.update(overrides)
    video = Video.objects.create(**fields)
    open_analysis_run(video, OWNER)
    return video


class ApprovalGateTests(TestCase):
    def setUp(self):
        self.video = _reviewable_video()

    def test_a_video_awaiting_review_is_not_claimable(self):
        self.assertIsNone(get_next_pending_job())

    def test_approving_makes_it_claimable(self):
        decide_approval(self.video.id, approve=True, decided_by=OWNER)
        self.assertEqual(get_next_pending_job().id, self.video.id)

    def test_approval_records_who_decided_and_when(self):
        decide_approval(self.video.id, approve=True, decided_by=ADMIN)

        self.video.refresh_from_db()
        self.assertEqual(self.video.approval_status, 'approved')
        self.assertEqual(self.video.approval_decided_by, ADMIN)
        self.assertIsNotNone(self.video.approval_decided_at)

    def test_rejection_blocks_analysis_but_leaves_the_video_alone(self):
        decide_approval(self.video.id, approve=False, decided_by=OWNER)

        self.video.refresh_from_db()
        self.assertEqual(self.video.approval_status, 'rejected')
        self.assertIsNone(get_next_pending_job())
        # Deliberately untouched: rejection is about compute, not visibility.
        self.assertEqual(self.video.visibility, 'public')
        self.assertIsNone(self.video.deleted_at)

    def test_cannot_decide_while_analysis_is_running(self):
        decide_approval(self.video.id, approve=True, decided_by=OWNER)
        claim_job(self.video.id, WORKER, 'Worker One')

        result = decide_approval(self.video.id, approve=False, decided_by=OWNER)
        self.assertFalse(result['success'])
        self.assertIn('already running', result['error'])

    def test_approving_stamps_the_queue_wait_time(self):
        # The clock that matters for FIFO starts at approval, not at upload.
        before = timezone.now()
        decide_approval(self.video.id, approve=True, decided_by=OWNER)

        self.video.refresh_from_db()
        self.assertGreaterEqual(self.video.analysis_requested_at, before)

    def test_unknown_video_is_reported_not_raised(self):
        import uuid
        result = decide_approval(uuid.uuid4(), approve=True, decided_by=OWNER)
        self.assertFalse(result['success'])


class ApprovalEndpointTests(TestCase):
    def setUp(self):
        self.video = _reviewable_video()

    def _post(self, approve, clerk_user_id=None):
        headers = {'HTTP_X_CLERK_USER_ID': clerk_user_id} if clerk_user_id else {}
        return self.client.post(
            f'/api/videos/{self.video.id}/approval/',
            data=json.dumps({'approve': approve}),
            content_type='application/json',
            **headers,
        )

    def test_owner_can_approve(self):
        self.assertEqual(self._post(True, OWNER).status_code, 200)
        self.video.refresh_from_db()
        self.assertEqual(self.video.approval_status, 'approved')

    def test_admin_can_approve(self):
        AdminUser.objects.create(clerk_user_id=ADMIN)
        self.assertEqual(self._post(True, ADMIN).status_code, 200)

    def test_stranger_cannot(self):
        self.assertEqual(self._post(True, STRANGER).status_code, 403)
        self.video.refresh_from_db()
        self.assertEqual(self.video.approval_status, 'pending_review')

    def test_anonymous_cannot(self):
        self.assertEqual(self._post(True).status_code, 403)

    def test_approve_must_be_a_boolean(self):
        response = self.client.post(
            f'/api/videos/{self.video.id}/approval/',
            data=json.dumps({'approve': 'yes'}),
            content_type='application/json',
            HTTP_X_CLERK_USER_ID=OWNER,
        )
        self.assertEqual(response.status_code, 400)

    def test_get_is_not_allowed(self):
        response = self.client.get(f'/api/videos/{self.video.id}/approval/',
                                   HTTP_X_CLERK_USER_ID=OWNER)
        self.assertEqual(response.status_code, 405)


class AnalysisHistoryTests(TestCase):
    def setUp(self):
        self.video = _reviewable_video()

    def _run_to_completion(self):
        decide_approval(self.video.id, approve=True, decided_by=OWNER)
        claim_job(self.video.id, WORKER, 'Worker One')
        complete_job(self.video.id, WORKER, 'a summary', ['car'], [], {'width': 640})

    def test_a_run_is_opened_when_analysis_is_requested(self):
        run = AnalysisRun.objects.get(video=self.video)
        self.assertEqual(run.attempt_number, 1)
        self.assertEqual(run.status, 'awaiting_approval')
        self.assertEqual(run.requested_by, OWNER)

    def test_approval_is_recorded_on_the_run(self):
        decide_approval(self.video.id, approve=True, decided_by=ADMIN)

        run = AnalysisRun.objects.get(video=self.video)
        self.assertEqual(run.status, 'approved')
        self.assertEqual(run.decided_by, ADMIN)
        self.assertIsNotNone(run.decided_at)

    def test_a_rejected_attempt_is_still_history(self):
        decide_approval(self.video.id, approve=False, decided_by=OWNER)

        run = AnalysisRun.objects.get(video=self.video)
        self.assertEqual(run.status, 'rejected')
        self.assertIsNotNone(run.finished_at)

    def test_claiming_records_the_worker(self):
        decide_approval(self.video.id, approve=True, decided_by=OWNER)
        claim_job(self.video.id, WORKER, 'Worker One')

        run = AnalysisRun.objects.get(video=self.video)
        self.assertEqual(run.status, 'processing')
        self.assertEqual(run.worker_id, WORKER)
        self.assertIsNotNone(run.started_at)

    def test_completion_records_what_the_run_produced(self):
        self._run_to_completion()

        run = AnalysisRun.objects.get(video=self.video)
        self.assertEqual(run.status, 'complete')
        self.assertEqual(run.summary, 'a summary')
        self.assertEqual(run.tags, ['car'])
        self.assertEqual(run.metadata, {'width': 640})
        self.assertIsNotNone(run.finished_at)

    def test_failure_records_the_error(self):
        decide_approval(self.video.id, approve=True, decided_by=OWNER)
        claim_job(self.video.id, WORKER, 'Worker One')
        fail_job(self.video.id, WORKER, 'disk full', 'analyzing')

        run = AnalysisRun.objects.get(video=self.video)
        self.assertEqual(run.status, 'failed')
        self.assertEqual(run.error, 'disk full')

    def test_each_attempt_keeps_its_own_results(self):
        # The point of history: comparing what two runs concluded.
        self._run_to_completion()

        second = open_analysis_run(self.video, OWNER)
        decide_approval(self.video.id, approve=True, decided_by=OWNER)
        claim_job(self.video.id, WORKER, 'Worker One')
        complete_job(self.video.id, WORKER, 'a different summary', ['truck'], [], {})

        runs = AnalysisRun.objects.filter(video=self.video).order_by('attempt_number')
        self.assertEqual([r.attempt_number for r in runs], [1, 2])
        self.assertEqual(runs[0].summary, 'a summary')
        self.assertEqual(runs[0].tags, ['car'])
        self.assertEqual(runs[1].id, second.id)
        self.assertEqual(runs[1].summary, 'a different summary')
        self.assertEqual(runs[1].tags, ['truck'])

    def test_attempt_numbers_do_not_collide(self):
        for _ in range(3):
            open_analysis_run(self.video, OWNER)

        numbers = list(
            AnalysisRun.objects.filter(video=self.video)
            .order_by('attempt_number')
            .values_list('attempt_number', flat=True)
        )
        self.assertEqual(numbers, [1, 2, 3, 4])
