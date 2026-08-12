"""The all-videos table: one derived state per video, and the endpoint behind it.

The state property exists because approval_status, analysis_status and
analysis_stage are orthogonal and disagree -- a freshly uploaded video is
stored as analysis_status='cancelled' having never run. These tests pin the
derivation so a future status change cannot quietly alter what users read.
"""

from django.test import Client, TestCase
from django.utils import timezone

from apps.accounts.models import AdminUser
from apps.videos.models import Video

ADMIN_CLERK_ID = 'user_admin'


def _video(**overrides):
    fields = dict(
        owner_clerk_user_id='user_owner',
        title='Clip',
        status='ready',
        visibility='public',
        approval_status='approved',
        analysis_status='complete',
    )
    fields.update(overrides)
    return Video.objects.create(**fields)


class StateDerivationTests(TestCase):
    def test_a_fresh_upload_reads_as_not_started(self):
        video = _video(approval_status='pending_review', analysis_status='not_started')

        self.assertEqual(video.state, Video.STATE_NOT_STARTED)
        self.assertEqual(video.state_label, 'Not started')

    def test_legacy_rows_stored_as_cancelled_still_read_as_not_started(self):
        """Rows written before 'not_started' existed, and any the backfill missed."""
        video = _video(approval_status='pending_review', analysis_status='cancelled')

        self.assertEqual(video.state, Video.STATE_NOT_STARTED)

    def test_approved_and_pending_is_queued(self):
        self.assertEqual(_video(analysis_status='pending').state, Video.STATE_QUEUED)

    def test_processing_is_running(self):
        self.assertEqual(_video(analysis_status='processing').state, Video.STATE_RUNNING)

    def test_complete_is_done(self):
        self.assertEqual(_video(analysis_status='complete').state, Video.STATE_DONE)

    def test_failed_is_failed(self):
        self.assertEqual(_video(analysis_status='failed').state, Video.STATE_FAILED)

    def test_rejected_wins_over_any_analysis_history(self):
        """A skipped video is skipped even if it completed a run earlier."""
        video = _video(approval_status='rejected', analysis_status='complete')

        self.assertEqual(video.state, Video.STATE_SKIPPED)

    def test_an_abandoned_run_is_startable_again_not_finished(self):
        video = _video(approval_status='approved', analysis_status='cancelled')

        self.assertEqual(video.state, Video.STATE_NOT_STARTED)

    def test_pending_review_wins_over_analysis_status(self):
        """Nobody has started it, whatever the analysis fields claim."""
        video = _video(approval_status='pending_review', analysis_status='complete')

        self.assertEqual(video.state, Video.STATE_NOT_STARTED)

    def test_every_state_has_a_label(self):
        for state in Video.STATE_LABELS:
            self.assertTrue(Video.STATE_LABELS[state])


class NotStartedStatusTests(TestCase):
    """'cancelled' now means only what it says: a run that was abandoned."""

    def test_an_upload_is_stored_as_not_started(self):
        video = _video(approval_status='pending_review', analysis_status='not_started')

        self.assertEqual(video.analysis_status, 'not_started')
        self.assertEqual(video.state, Video.STATE_NOT_STARTED)

    def test_an_approved_cancelled_video_is_startable_not_finished(self):
        """A genuinely abandoned run still means 'cancelled', and is startable."""
        video = _video(approval_status='approved', analysis_status='cancelled')

        self.assertEqual(video.state, Video.STATE_NOT_STARTED)

    def test_not_started_is_requeueable(self):
        """Otherwise such a video would be invisible to every path that runs it."""
        from apps.videos.worker_services import REQUEUEABLE_ANALYSIS_STATUSES

        self.assertIn('not_started', REQUEUEABLE_ANALYSIS_STATUSES)
        self.assertNotIn('processing', REQUEUEABLE_ANALYSIS_STATUSES)
        self.assertNotIn('pending', REQUEUEABLE_ANALYSIS_STATUSES)

    def test_not_started_is_a_valid_choice(self):
        valid = dict(Video.ANALYSIS_STATUS_CHOICES)

        self.assertIn('not_started', valid)
        self.assertIn('cancelled', valid)

    def test_a_not_started_video_is_not_claimable(self):
        """Nothing runs until it is started, whatever the status field says."""
        from apps.videos.worker_services import claimable_jobs

        _video(approval_status='pending_review', analysis_status='not_started')

        self.assertEqual(claimable_jobs().count(), 0)

    def test_the_feed_serializer_carries_the_derived_state(self):
        video = _video(approval_status='pending_review', analysis_status='not_started')

        payload = video.to_dict()

        self.assertEqual(payload['state'], 'not_started')
        self.assertEqual(payload['state_label'], 'Not started')

    def test_the_feed_endpoint_carries_it_too(self):
        """The feed is a third serializer, and the one the cards actually read.

        It builds its payload from .values() rows in apps.feed, so adding the
        state to the model and to apps.videos left it out -- and the card falls
        back to the raw analysis_status when it is missing.
        """
        _video(approval_status='pending_review', analysis_status='not_started',
               visibility='public')

        items = Client().get('/api/feed/').json().get('items') or []

        self.assertTrue(items, 'expected the video in the feed')
        self.assertEqual(items[0]['state'], 'not_started')
        self.assertEqual(items[0]['state_label'], 'Not started')

    def test_the_raw_row_serializer_carries_it_too(self):
        """The feed has two serialization paths; the UI reads state from both."""
        from apps.videos.views import _serialize_video_row

        video = _video(approval_status='approved', analysis_status='processing')
        row = {
            'id': video.id,
            'owner_clerk_user_id': video.owner_clerk_user_id,
            'title': video.title,
            'description': '',
            'visibility': 'public',
            'status': 'ready',
            'created_at': video.created_at,
            'updated_at': video.updated_at,
            'deleted_at': None,
            'approval_status': 'approved',
            'analysis_status': 'processing',
        }

        payload = _serialize_video_row(row)

        self.assertEqual(payload['state'], 'running')
        self.assertEqual(payload['state_label'], 'Running')


class AnalyzerVersionTests(TestCase):
    def test_it_reads_the_version_from_ai_metadata(self):
        video = _video(ai_metadata={'analyzer_version': 'detect-2.0'})

        self.assertEqual(video.analyzer_version, 'detect-2.0')

    def test_a_never_analyzed_video_has_no_version(self):
        self.assertEqual(_video(ai_metadata={}).analyzer_version, '')

    def test_non_dict_metadata_does_not_explode(self):
        """ai_metadata is a JSON field, so it can legally hold a non-dict."""
        self.assertEqual(_video(ai_metadata=[]).analyzer_version, '')


class AllVideosEndpointTests(TestCase):
    url = '/api/videos/admin/all/'

    def setUp(self):
        AdminUser.objects.create(clerk_user_id=ADMIN_CLERK_ID)
        self.client = Client()

    def _get(self):
        return self.client.get(self.url, HTTP_X_CLERK_USER_ID=ADMIN_CLERK_ID)

    def test_it_returns_every_video_regardless_of_state(self):
        _video(title='Done one', analysis_status='complete')
        _video(title='Queued one', analysis_status='pending')
        _video(title='Not started one', approval_status='pending_review',
               analysis_status='cancelled')
        _video(title='Skipped one', approval_status='rejected')

        body = self._get().json()

        self.assertEqual(body['total'], 4)
        self.assertEqual(body['count'], 4)
        self.assertFalse(body['truncated'])
        self.assertEqual(
            {item['title'] for item in body['items']},
            {'Done one', 'Queued one', 'Not started one', 'Skipped one'})

    def test_each_row_carries_its_derived_state_and_version(self):
        _video(title='Analyzed', analysis_status='complete',
               ai_metadata={'analyzer_version': 'detect-2.0'})

        row = self._get().json()['items'][0]

        self.assertEqual(row['state'], 'done')
        self.assertEqual(row['state_label'], 'Done')
        self.assertEqual(row['analyzer_version'], 'detect-2.0')

    def test_deleted_videos_are_excluded(self):
        _video(title='Alive')
        _video(title='Gone', deleted_at=timezone.now())

        body = self._get().json()

        self.assertEqual(body['total'], 1)
        self.assertEqual(body['items'][0]['title'], 'Alive')

    def test_a_stale_version_is_visible_next_to_a_current_one(self):
        """The rollout question: what is still on the old model?"""
        _video(title='Old', ai_metadata={'analyzer_version': 'detect-1.0'})
        _video(title='New', ai_metadata={'analyzer_version': 'detect-2.0'})

        versions = {item['title']: item['analyzer_version']
                    for item in self._get().json()['items']}

        self.assertEqual(versions, {'Old': 'detect-1.0', 'New': 'detect-2.0'})

    def test_it_requires_admin(self):
        _video()

        self.assertIn(self.client.get(self.url).status_code, (401, 403))
