import json
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from apps.videos.models import Video


class VideoUploadFlowTests(TestCase):
    def test_upload_url_creates_video_and_returns_upload_endpoint(self):
        response = self.client.post(
            '/api/videos/upload-url/',
            data=json.dumps(
                {
                    'clerk_user_id': 'test-user',
                    'title': 'Test clip',
                    'description': 'demo',
                    'original_filename': 'dashcam.mp4',
                    'duration_seconds': 42,
                }
            ),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertEqual(payload['kind'], 'video-upload-url')
        self.assertEqual(payload['upload']['method'], 'POST')
        self.assertEqual(payload['upload']['url'], '/api/videos/upload/')
        self.assertEqual(payload['video']['title'], 'Test clip')
        self.assertEqual(payload['video']['status'], 'pending')
        self.assertEqual(payload['video']['duration_seconds'], 42)
        self.assertEqual(Video.objects.count(), 1)

    @patch('apps.videos.views.upload_bytes_to_supabase')
    def test_upload_file_stores_video_to_supabase_and_marks_ready(self, mock_upload_bytes):
        mock_upload_bytes.return_value = 'https://example.supabase.co/storage/v1/object/public/videos/test-video/file.mp4'

        create_response = self.client.post(
            '/api/videos/upload-url/',
            data=json.dumps(
                {
                    'clerk_user_id': 'test-user',
                    'title': 'Test clip',
                    'description': 'demo',
                    'original_filename': 'dashcam.mp4',
                    'duration_seconds': 99,
                }
            ),
            content_type='application/json',
        )
        video_id = create_response.json()['video']['id']

        upload_file = SimpleUploadedFile('dashcam.mp4', b'fake-video-bytes', content_type='video/mp4')
        response = self.client.post(
            '/api/videos/upload/',
            data={'video_id': video_id, 'file': upload_file},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertEqual(payload['kind'], 'video-uploaded')
        self.assertEqual(payload['video']['id'], video_id)
        self.assertEqual(payload['video']['status'], 'ready')
        self.assertEqual(payload['video']['playback_url'], mock_upload_bytes.return_value)
        self.assertTrue(mock_upload_bytes.called)

        video = Video.objects.get(id=video_id)
        self.assertEqual(video.status, 'ready')
        self.assertEqual(video.playback_url, mock_upload_bytes.return_value)
        self.assertEqual(video.duration_seconds, 99)

    def test_view_endpoint_increments_views(self):
        create_response = self.client.post(
            '/api/videos/upload-url/',
            data=json.dumps(
                {
                    'clerk_user_id': 'test-user',
                    'title': 'Test clip',
                    'description': 'demo',
                    'original_filename': 'dashcam.mp4',
                    'duration_seconds': 12,
                }
            ),
            content_type='application/json',
        )
        video_id = create_response.json()['video']['id']

        response = self.client.post(f'/api/videos/{video_id}/view/')

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['kind'], 'video-view')
        self.assertEqual(payload['video']['id'], video_id)
        self.assertEqual(payload['video']['views'], 1)

        video = Video.objects.get(id=video_id)
        self.assertEqual(video.views, 1)
