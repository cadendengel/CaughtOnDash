"""Tests for queueing videos for analysis.

A completed upload was already implicitly claimable, because analysis_status
defaults to 'pending'. What was missing was analysis_requested_at ever being
populated, which left the worker's ordering column permanently NULL. These
tests pin the enqueue behaviour and the ordering, including the NULL handling
that differs between SQLite and Postgres.
"""

import json
from datetime import timedelta
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import AdminUser
from apps.videos.models import Video
from apps.videos.worker_services import admin_retry_job, get_next_pending_job


def _ready_video(title, *, requested_at=None, created_offset_minutes=0, **kwargs):
    video = Video.objects.create(
        owner_clerk_user_id='user_owner',
        title=title,
        status='ready',
        analysis_status='pending',
        analysis_requested_at=requested_at,
        **kwargs,
    )
    if created_offset_minutes:
        Video.objects.filter(id=video.id).update(
            created_at=timezone.now() + timedelta(minutes=created_offset_minutes))
        video.refresh_from_db()
    return video


class UploadEnqueuesAnalysisTests(TestCase):
    def _create_video_record(self):
        response = self.client.post(
            '/api/videos/upload-url/',
            data=json.dumps({'clerk_user_id': 'user_owner', 'title': 'Clip',
                             'original_filename': 'dash.mp4'}),
            content_type='application/json',
        )
        return response.json()['video']['id']

    def _upload_file(self, video_id):
        with patch('apps.videos.views.upload_bytes_to_supabase',
                   return_value='https://cdn.example.com/clip.mp4'):
            return self.client.post('/api/videos/upload/', {
                'video_id': video_id,
                'file': SimpleUploadedFile('dash.mp4', b'bytes', content_type='video/mp4'),
            })

    def test_video_is_not_claimable_before_the_file_arrives(self):
        self._create_video_record()
        self.assertIsNone(get_next_pending_job())

    def test_completed_upload_is_queued_with_a_request_timestamp(self):
        video_id = self._create_video_record()
        before = timezone.now()
        self.assertEqual(self._upload_file(video_id).status_code, 200)

        video = Video.objects.get(id=video_id)
        self.assertEqual(video.status, 'ready')
        self.assertEqual(video.analysis_status, 'pending')
        self.assertEqual(video.analysis_stage, 'queued')
        self.assertIsNotNone(video.analysis_requested_at)
        self.assertGreaterEqual(video.analysis_requested_at, before)

        job = get_next_pending_job()
        self.assertIsNotNone(job)
        self.assertEqual(str(job.id), video_id)

    def test_failed_storage_upload_does_not_queue_anything(self):
        video_id = self._create_video_record()
        with patch('apps.videos.views.upload_bytes_to_supabase',
                   side_effect=RuntimeError('storage down')):
            response = self.client.post('/api/videos/upload/', {
                'video_id': video_id,
                'file': SimpleUploadedFile('dash.mp4', b'bytes', content_type='video/mp4'),
            })

        self.assertEqual(response.status_code, 500)
        video = Video.objects.get(id=video_id)
        self.assertEqual(video.status, 'pending')
        self.assertIsNone(video.analysis_requested_at)
        self.assertIsNone(get_next_pending_job())


class QueueOrderingTests(TestCase):
    def test_queue_is_fifo_by_request_time(self):
        now = timezone.now()
        second = _ready_video('second', requested_at=now)
        _ready_video('first', requested_at=now - timedelta(minutes=5))
        self.assertEqual(get_next_pending_job().title, 'first')
        self.assertNotEqual(get_next_pending_job().id, second.id)

    def test_rows_without_a_request_time_sort_last(self):
        # Legacy rows predating the explicit enqueue have NULL here. SQLite would
        # sort them first and Postgres last; nulls_last pins both to last so the
        # local queue matches production.
        _ready_video('legacy', requested_at=None, created_offset_minutes=-60)
        _ready_video('explicit', requested_at=timezone.now())
        self.assertEqual(get_next_pending_job().title, 'explicit')

    def test_only_legacy_rows_still_fall_back_to_creation_order(self):
        _ready_video('older', requested_at=None, created_offset_minutes=-60)
        _ready_video('newer', requested_at=None)
        self.assertEqual(get_next_pending_job().title, 'older')


class RetryRequeuesTests(TestCase):
    def test_retry_restamps_the_request_time(self):
        original = timezone.now() - timedelta(hours=2)
        video = _ready_video('failed clip', requested_at=original)
        Video.objects.filter(id=video.id).update(analysis_status='failed')

        result = admin_retry_job(video.id)
        self.assertTrue(result['success'])

        video.refresh_from_db()
        self.assertEqual(video.analysis_status, 'pending')
        self.assertEqual(video.analysis_stage, 'queued')
        self.assertGreater(video.analysis_requested_at, original)

    def test_retried_video_does_not_jump_ahead_of_a_newer_upload(self):
        old = _ready_video('repeatedly failing', requested_at=timezone.now() - timedelta(hours=2))
        Video.objects.filter(id=old.id).update(analysis_status='failed')
        _ready_video('fresh upload', requested_at=timezone.now() - timedelta(minutes=1))

        admin_retry_job(old.id)
        self.assertEqual(get_next_pending_job().title, 'fresh upload')


class OwnerRequestAnalysisTests(TestCase):
    def setUp(self):
        self.video = _ready_video('Analysed clip', requested_at=timezone.now())
        Video.objects.filter(id=self.video.id).update(
            analysis_status='complete', analysis_stage='complete', analysis_progress=100,
            ai_summary='old summary', ai_tags=['old'])
        self.video.refresh_from_db()

    def _post(self, clerk_user_id=None):
        headers = {'HTTP_X_CLERK_USER_ID': clerk_user_id} if clerk_user_id else {}
        return self.client.post(f'/api/videos/{self.video.id}/analyze/', **headers)

    def test_owner_can_requeue_a_completed_analysis(self):
        response = self._post('user_owner')
        self.assertEqual(response.status_code, 200)

        self.video.refresh_from_db()
        self.assertEqual(self.video.analysis_status, 'pending')
        self.assertEqual(self.video.analysis_stage, 'queued')
        self.assertEqual(self.video.analysis_progress, 0)
        self.assertEqual(get_next_pending_job().id, self.video.id)

    def test_previous_results_survive_until_the_rerun_overwrites_them(self):
        self._post('user_owner')
        self.video.refresh_from_db()
        self.assertEqual(self.video.ai_summary, 'old summary')
        self.assertEqual(self.video.ai_tags, ['old'])

    def test_stale_worker_assignment_is_cleared(self):
        Video.objects.filter(id=self.video.id).update(
            worker_id='old-worker', worker_name='Old Worker')
        self._post('user_owner')
        self.video.refresh_from_db()
        self.assertIsNone(self.video.worker_id)
        self.assertEqual(self.video.worker_name, '')

    def test_stranger_is_refused(self):
        response = self._post('user_stranger')
        self.assertEqual(response.status_code, 403)
        self.video.refresh_from_db()
        self.assertEqual(self.video.analysis_status, 'complete')

    def test_anonymous_is_refused(self):
        self.assertEqual(self._post().status_code, 403)

    def test_admin_is_allowed(self):
        AdminUser.objects.create(clerk_user_id='user_admin')
        self.assertEqual(self._post('user_admin').status_code, 200)

    def test_cannot_requeue_while_a_worker_is_actively_processing(self):
        Video.objects.filter(id=self.video.id).update(
            analysis_status='processing', worker_last_seen_at=timezone.now())
        response = self._post('user_owner')
        self.assertEqual(response.status_code, 409)
        self.video.refresh_from_db()
        self.assertEqual(self.video.analysis_status, 'processing')

    def test_can_requeue_a_processing_job_whose_worker_went_quiet(self):
        # The production case: a worker claimed the job, then every write-back
        # was rejected, leaving the video wedged in 'processing' indefinitely.
        Video.objects.filter(id=self.video.id).update(
            analysis_status='processing',
            worker_id='dead-worker',
            worker_last_seen_at=timezone.now() - timedelta(minutes=30))

        response = self._post('user_owner')
        self.assertEqual(response.status_code, 200)

        self.video.refresh_from_db()
        self.assertEqual(self.video.analysis_status, 'pending')
        self.assertIsNone(self.video.worker_id)
        self.assertEqual(get_next_pending_job().id, self.video.id)

    def test_can_requeue_a_processing_job_with_no_worker_timestamps(self):
        Video.objects.filter(id=self.video.id).update(
            analysis_status='processing', worker_last_seen_at=None,
            worker_claimed_at=None, analysis_started_at=None)
        self.assertEqual(self._post('user_owner').status_code, 200)
        self.video.refresh_from_db()
        self.assertEqual(self.video.analysis_status, 'pending')

    def test_cannot_requeue_something_already_queued(self):
        Video.objects.filter(id=self.video.id).update(analysis_status='pending')
        response = self._post('user_owner')
        self.assertEqual(response.status_code, 409)
        self.assertIn('already queued', response.json()['detail'])

    def test_failed_analysis_can_be_requeued(self):
        Video.objects.filter(id=self.video.id).update(analysis_status='failed',
                                                      analysis_error='boom')
        self.assertEqual(self._post('user_owner').status_code, 200)
        self.video.refresh_from_db()
        self.assertEqual(self.video.analysis_error, '')

    def test_video_without_a_file_cannot_be_analyzed(self):
        Video.objects.filter(id=self.video.id).update(status='pending')
        self.assertEqual(self._post('user_owner').status_code, 409)

    def test_deleted_video_returns_404(self):
        Video.objects.filter(id=self.video.id).update(deleted_at=timezone.now())
        self.assertEqual(self._post('user_owner').status_code, 404)

    def test_get_is_not_allowed(self):
        response = self.client.get(f'/api/videos/{self.video.id}/analyze/',
                                   HTTP_X_CLERK_USER_ID='user_owner')
        self.assertEqual(response.status_code, 405)
