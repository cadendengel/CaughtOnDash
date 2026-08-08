"""The moderation overview.

Admins could see every post and delete it, but nothing told them which videos
were *waiting* on them. Three states need a human and each needs a different
action, so they are reported separately rather than as one undifferentiated
list.

The state worth the most attention is 'stuck': a video whose worker went quiet
mid-job shows a progress bar that will never move and raises no error, so
without something surfacing it nobody has any reason to look.
"""

from datetime import timedelta
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import AdminUser
from apps.videos.models import AnalysisRun, Video
from apps.videos.worker_services import STALE_PROCESSING_MINUTES

ADMIN = 'user_admin'
PLAIN = 'user_plain'


def _video(**overrides):
    fields = dict(
        owner_clerk_user_id='user_owner',
        title='Clip',
        status='ready',
        approval_status='approved',
        analysis_status='pending',
        analysis_requested_at=timezone.now(),
    )
    fields.update(overrides)
    return Video.objects.create(**fields)


class ModerationOverviewTests(TestCase):
    def setUp(self):
        AdminUser.objects.create(clerk_user_id=ADMIN)

    def _get(self, clerk_user_id=ADMIN):
        headers = {'HTTP_X_CLERK_USER_ID': clerk_user_id} if clerk_user_id else {}
        return self.client.get('/api/videos/admin/moderation/', **headers)

    def test_a_non_admin_is_refused(self):
        self.assertEqual(self._get(PLAIN).status_code, 403)

    def test_an_anonymous_caller_is_refused(self):
        self.assertEqual(self._get(None).status_code, 403)

    def test_awaiting_review_is_reported(self):
        _video(title='Needs a look', approval_status='pending_review')

        body = self._get().json()

        titles = [item['title'] for item in body['groups']['awaiting_review']]
        self.assertEqual(titles, ['Needs a look'])
        self.assertEqual(body['counts']['awaiting_review'], 1)

    def test_a_failed_analysis_is_reported_separately_from_a_rejection(self):
        # A rejected video was judged. A failed one was never judged at all --
        # the machinery broke before anyone could look.
        _video(title='Broke', analysis_status='failed',
               analysis_error='Could not download', analysis_failed_at=timezone.now())
        _video(title='Rejected', approval_status='rejected')

        body = self._get().json()

        self.assertEqual([i['title'] for i in body['groups']['failed']], ['Broke'])
        self.assertEqual(body['counts']['awaiting_review'], 0)

    def test_a_worker_that_went_quiet_shows_as_stuck(self):
        gone = timezone.now() - timedelta(minutes=STALE_PROCESSING_MINUTES + 1)
        _video(title='Abandoned', analysis_status='processing',
               worker_id='w1', worker_claimed_at=gone, worker_last_seen_at=gone)

        body = self._get().json()

        self.assertEqual([i['title'] for i in body['groups']['stuck']], ['Abandoned'])

    def test_a_live_worker_is_not_reported_as_stuck(self):
        _video(title='In progress', analysis_status='processing',
               worker_id='w1', worker_claimed_at=timezone.now(),
               worker_last_seen_at=timezone.now())

        self.assertEqual(self._get().json()['counts']['stuck'], 0)

    def test_a_job_claimed_with_no_timestamps_at_all_is_stuck(self):
        # Nothing is coming back for it, and with no timestamps it would slip
        # past a naive last-seen comparison.
        _video(title='No timestamps', analysis_status='processing', worker_id='w1')

        self.assertEqual([i['title'] for i in self._get().json()['groups']['stuck']],
                         ['No timestamps'])

    def test_deleted_videos_are_not_reported(self):
        _video(title='Gone', approval_status='pending_review', deleted_at=timezone.now())

        self.assertEqual(self._get().json()['counts']['total'], 0)

    def test_entries_carry_their_attempt_history(self):
        video = _video(title='Second time', approval_status='pending_review')
        AnalysisRun.objects.create(
            video=video, attempt_number=1, status='failed',
            error='ran out of memory', finished_at=timezone.now())
        AnalysisRun.objects.create(video=video, attempt_number=2, status='awaiting_approval')

        entry = self._get().json()['groups']['awaiting_review'][0]

        self.assertEqual(entry['attempt_number'], 2)
        self.assertEqual(entry['previous_attempts'], 1)

    def test_the_total_counts_every_group(self):
        _video(title='A', approval_status='pending_review')
        _video(title='B', analysis_status='failed', analysis_failed_at=timezone.now())
        gone = timezone.now() - timedelta(minutes=STALE_PROCESSING_MINUTES + 1)
        _video(title='C', analysis_status='processing', worker_id='w1',
               worker_claimed_at=gone, worker_last_seen_at=gone)

        counts = self._get().json()['counts']

        self.assertEqual(counts['awaiting_review'], 1)
        self.assertEqual(counts['failed'], 1)
        self.assertEqual(counts['stuck'], 1)
        self.assertEqual(counts['total'], 3)

    def test_listing_does_not_scale_its_queries_with_the_queue(self):
        """Query count must not grow with the number of videos.

        The worker queue pins this for one group; the overview runs three, so
        an N+1 here costs triple. Comparing two sizes proves the shape rather
        than freezing a magic number that any unrelated change would break.
        """
        def seed(count, offset):
            for index in range(count):
                video = _video(title=f'Clip {offset + index}', approval_status='pending_review')
                AnalysisRun.objects.create(
                    video=video, attempt_number=1, status='awaiting_approval')

        seed(3, 0)
        with CaptureQueriesContext(connection) as small:
            self.assertEqual(len(self._get().json()['groups']['awaiting_review']), 3)

        seed(9, 3)
        with CaptureQueriesContext(connection) as large:
            self.assertEqual(len(self._get().json()['groups']['awaiting_review']), 12)

        self.assertEqual(len(large), len(small))
