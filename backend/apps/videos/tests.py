import json
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from apps.accounts.models import AdminUser, Profile
from apps.videos.models import Video


class VideoUploadFlowTests(TestCase):
    def _create_admin(self, clerk_user_id='admin-user', email='admin@example.com'):
        return AdminUser.objects.create(clerk_user_id=clerk_user_id, email=email)

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
                    'tags': ['Road rage', 'near miss', 'Road rage'],
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
        self.assertEqual(payload['video']['analysis_status'], 'idle')
        self.assertEqual(payload['video']['duration_seconds'], 42)
        self.assertEqual(payload['video']['tags'][0]['text'], 'Road rage')
        self.assertEqual(payload['video']['tags'][0]['source'], 'user')
        self.assertEqual(payload['video']['tags'][1]['text'], 'near miss')
        self.assertEqual(Video.objects.count(), 1)

    def test_upload_url_rejects_videos_longer_than_sixty_seconds(self):
        response = self.client.post(
            '/api/videos/upload-url/',
            data=json.dumps(
                {
                    'clerk_user_id': 'test-user',
                    'title': 'Too long clip',
                    'description': 'demo',
                    'original_filename': 'dashcam.mp4',
                    'duration_seconds': 61,
                }
            ),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('60 seconds or shorter', response.json()['detail'])
        self.assertEqual(Video.objects.count(), 0)

    def test_admin_can_update_existing_video_tags(self):
        self._create_admin('admin-user')
        create_response = self.client.post(
            '/api/videos/upload-url/',
            data=json.dumps(
                {
                    'clerk_user_id': 'test-user',
                    'title': 'Test clip',
                    'description': 'demo',
                    'original_filename': 'dashcam.mp4',
                    'duration_seconds': 42,
                    'tags': ['user tag'],
                }
            ),
            content_type='application/json',
        )
        video_id = create_response.json()['video']['id']

        response = self.client.patch(
            f'/api/videos/admin/videos/{video_id}/tags/',
            data=json.dumps(
                {
                    'tags': [
                        {'text': 'user tag', 'source': 'user'},
                        {'text': 'scene analysis', 'source': 'admin'},
                        {'text': 'scene analysis', 'source': 'admin'},
                    ]
                }
            ),
            content_type='application/json',
            HTTP_X_CLERK_USER_ID='admin-user',
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual([tag['source'] for tag in payload['video']['tags']], ['user', 'admin'])
        self.assertEqual(payload['video']['tags'][1]['text'], 'scene analysis')

        video = Video.objects.get(id=video_id)
        self.assertEqual(video.tags[0]['source'], 'user')
        self.assertEqual(video.tags[1]['source'], 'admin')

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
                    'duration_seconds': 60,
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
        self.assertEqual(payload['video']['analysis_status'], 'idle')
        self.assertEqual(payload['video']['playback_url'], mock_upload_bytes.return_value)
        self.assertTrue(mock_upload_bytes.called)

        video = Video.objects.get(id=video_id)
        self.assertEqual(video.status, 'ready')
        self.assertEqual(video.playback_url, mock_upload_bytes.return_value)
        self.assertEqual(video.duration_seconds, 60)

    @patch('apps.videos.views.probe_uploaded_video_duration')
    @patch('apps.videos.views.upload_bytes_to_supabase')
    def test_upload_file_rejects_videos_over_sixty_seconds(self, mock_upload_bytes, mock_probe_duration):
        mock_probe_duration.return_value = 61

        create_response = self.client.post(
            '/api/videos/upload-url/',
            data=json.dumps(
                {
                    'clerk_user_id': 'test-user',
                    'title': 'Too long clip',
                    'description': 'demo',
                    'original_filename': 'dashcam.mp4',
                    'duration_seconds': 60,
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

        self.assertEqual(response.status_code, 400)
        self.assertIn('60 seconds or shorter', response.json()['detail'])
        mock_upload_bytes.assert_not_called()

        video = Video.objects.get(id=video_id)
        self.assertEqual(video.status, 'failed')
        self.assertEqual(video.analysis_status, 'failed')

    @patch('apps.videos.views.schedule_video_analysis')
    def test_reanalyze_endpoint_queues_analysis_for_admin(self, mock_schedule_analysis):
        self._create_admin('admin-user')
        create_response = self.client.post(
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
        video_id = create_response.json()['video']['id']

        response = self.client.post(
            f'/api/videos/{video_id}/analysis/reanalyze/',
            HTTP_X_CLERK_USER_ID='admin-user',
        )

        self.assertEqual(response.status_code, 202)
        payload = response.json()
        self.assertEqual(payload['kind'], 'video-reanalyze')
        self.assertEqual(payload['analysis_status'], 'queued')
        mock_schedule_analysis.assert_called_once()

        video = Video.objects.get(id=video_id)
        self.assertEqual(video.analysis_status, 'queued')

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

    def test_comments_endpoint_creates_and_lists_comments(self):
        Profile.objects.create(
            clerk_user_id='comment-user',
            email='comment@example.com',
            username='commenter',
            display_name='Comment User',
        )
        create_response = self.client.post(
            '/api/videos/upload-url/',
            data=json.dumps(
                {
                    'clerk_user_id': 'test-user',
                    'title': 'Test clip',
                    'description': 'demo',
                    'original_filename': 'dashcam.mp4',
                    'duration_seconds': 15,
                }
            ),
            content_type='application/json',
        )
        video_id = create_response.json()['video']['id']

        post_response = self.client.post(
            f'/api/videos/{video_id}/comments/',
            data=json.dumps(
                {
                    'clerk_user_id': 'comment-user',
                    'text': 'Nice catch.',
                }
            ),
            content_type='application/json',
        )

        self.assertEqual(post_response.status_code, 201)
        post_payload = post_response.json()
        self.assertEqual(post_payload['kind'], 'video-comment')
        self.assertEqual(post_payload['comment']['text'], 'Nice catch.')
        self.assertEqual(post_payload['comment']['username'], 'commenter')
        self.assertIsNone(post_payload['comment']['parent_comment_id'])

        reply_response = self.client.post(
            f'/api/videos/{video_id}/comments/',
            data=json.dumps(
                {
                    'clerk_user_id': 'reply-user',
                    'parent_comment_id': post_payload['comment']['id'],
                    'text': '@commenter Totally agree.',
                }
            ),
            content_type='application/json',
        )

        self.assertEqual(reply_response.status_code, 201)
        reply_payload = reply_response.json()
        self.assertEqual(reply_payload['comment']['parent_comment_id'], post_payload['comment']['id'])
        self.assertEqual(reply_payload['comment']['text'], '@commenter Totally agree.')

        list_response = self.client.get(f'/api/videos/{video_id}/comments/')
        self.assertEqual(list_response.status_code, 200)
        list_payload = list_response.json()
        self.assertEqual(list_payload['count'], 2)
        self.assertEqual(list_payload['items'][0]['text'], 'Nice catch.')
        self.assertEqual(list_payload['items'][0]['replies'][0]['text'], '@commenter Totally agree.')
        self.assertEqual(list_payload['items'][0]['replies'][0]['parent_comment_id'], post_payload['comment']['id'])

    def test_comment_like_endpoint_toggles_comment_like(self):
        Profile.objects.create(
            clerk_user_id='comment-user',
            email='comment@example.com',
            username='commenter',
            display_name='Comment User',
        )
        create_response = self.client.post(
            '/api/videos/upload-url/',
            data=json.dumps(
                {
                    'clerk_user_id': 'test-user',
                    'title': 'Test clip',
                    'description': 'demo',
                    'original_filename': 'dashcam.mp4',
                    'duration_seconds': 15,
                }
            ),
            content_type='application/json',
        )
        video_id = create_response.json()['video']['id']

        comment_response = self.client.post(
            f'/api/videos/{video_id}/comments/',
            data=json.dumps(
                {
                    'clerk_user_id': 'comment-user',
                    'text': 'Nice catch.',
                }
            ),
            content_type='application/json',
        )
        comment_id = comment_response.json()['comment']['id']

        like_response = self.client.post(
            f'/api/videos/comments/{comment_id}/like/',
            data=json.dumps({'clerk_user_id': 'liker-user'}),
            content_type='application/json',
        )

        self.assertEqual(like_response.status_code, 200)
        like_payload = like_response.json()
        self.assertEqual(like_payload['kind'], 'video-comment-like')
        self.assertTrue(like_payload['comment']['liked'])
        self.assertEqual(like_payload['comment']['likes_count'], 1)

        unlike_response = self.client.post(
            f'/api/videos/comments/{comment_id}/like/',
            data=json.dumps({'clerk_user_id': 'liker-user'}),
            content_type='application/json',
        )

        self.assertEqual(unlike_response.status_code, 200)
        unlike_payload = unlike_response.json()
        self.assertFalse(unlike_payload['comment']['liked'])
        self.assertEqual(unlike_payload['comment']['likes_count'], 0)

    def test_like_endpoint_toggles_like(self):
        create_response = self.client.post(
            '/api/videos/upload-url/',
            data=json.dumps(
                {
                    'clerk_user_id': 'test-user',
                    'title': 'Test clip',
                    'description': 'demo',
                    'original_filename': 'dashcam.mp4',
                    'duration_seconds': 15,
                }
            ),
            content_type='application/json',
        )
        video_id = create_response.json()['video']['id']

        like_response = self.client.post(
            f'/api/videos/{video_id}/like/',
            data=json.dumps({'clerk_user_id': 'liker-user'}),
            content_type='application/json',
        )

        self.assertEqual(like_response.status_code, 200)
        like_payload = like_response.json()
        self.assertTrue(like_payload['video']['liked'])
        self.assertEqual(like_payload['video']['likes_count'], 1)

        unlike_response = self.client.post(
            f'/api/videos/{video_id}/like/',
            data=json.dumps({'clerk_user_id': 'liker-user'}),
            content_type='application/json',
        )

        self.assertEqual(unlike_response.status_code, 200)
        unlike_payload = unlike_response.json()
        self.assertFalse(unlike_payload['video']['liked'])
        self.assertEqual(unlike_payload['video']['likes_count'], 0)

    def test_admin_can_delete_comment_and_reply(self):
        self._create_admin('admin-user')
        Profile.objects.create(
            clerk_user_id='comment-user',
            email='comment@example.com',
            username='commenter',
            display_name='Comment User',
        )
        create_response = self.client.post(
            '/api/videos/upload-url/',
            data=json.dumps(
                {
                    'clerk_user_id': 'test-user',
                    'title': 'Test clip',
                    'description': 'demo',
                    'original_filename': 'dashcam.mp4',
                    'duration_seconds': 15,
                }
            ),
            content_type='application/json',
        )
        video_id = create_response.json()['video']['id']

        comment_response = self.client.post(
            f'/api/videos/{video_id}/comments/',
            data=json.dumps(
                {
                    'clerk_user_id': 'comment-user',
                    'text': 'Nice catch.',
                }
            ),
            content_type='application/json',
        )
        comment_id = comment_response.json()['comment']['id']

        reply_response = self.client.post(
            f'/api/videos/{video_id}/comments/',
            data=json.dumps(
                {
                    'clerk_user_id': 'reply-user',
                    'parent_comment_id': comment_id,
                    'text': 'Reply text',
                }
            ),
            content_type='application/json',
        )
        reply_id = reply_response.json()['comment']['id']

        delete_reply_response = self.client.delete(
            f'/api/videos/admin/comments/{reply_id}/',
            HTTP_X_CLERK_USER_ID='admin-user',
        )
        self.assertEqual(delete_reply_response.status_code, 200)
        self.assertEqual(Video.objects.get(id=video_id).comments.count(), 1)

        delete_comment_response = self.client.delete(
            f'/api/videos/admin/comments/{comment_id}/',
            HTTP_X_CLERK_USER_ID='admin-user',
        )
        self.assertEqual(delete_comment_response.status_code, 200)
        self.assertEqual(Video.objects.get(id=video_id).comments.count(), 0)

    def test_admin_can_delete_video(self):
        self._create_admin('admin-user')
        create_response = self.client.post(
            '/api/videos/upload-url/',
            data=json.dumps(
                {
                    'clerk_user_id': 'test-user',
                    'title': 'Test clip',
                    'description': 'demo',
                    'original_filename': 'dashcam.mp4',
                    'duration_seconds': 15,
                }
            ),
            content_type='application/json',
        )
        video_id = create_response.json()['video']['id']

        delete_response = self.client.delete(
            f'/api/videos/admin/videos/{video_id}/',
            HTTP_X_CLERK_USER_ID='admin-user',
        )

        self.assertEqual(delete_response.status_code, 200)
        self.assertFalse(Video.objects.filter(id=video_id).exists())

    def test_non_admin_cannot_delete_video(self):
        create_response = self.client.post(
            '/api/videos/upload-url/',
            data=json.dumps(
                {
                    'clerk_user_id': 'test-user',
                    'title': 'Test clip',
                    'description': 'demo',
                    'original_filename': 'dashcam.mp4',
                    'duration_seconds': 15,
                }
            ),
            content_type='application/json',
        )
        video_id = create_response.json()['video']['id']

        delete_response = self.client.delete(
            f'/api/videos/admin/videos/{video_id}/',
            HTTP_X_CLERK_USER_ID='not-admin',
        )

        self.assertEqual(delete_response.status_code, 403)
        self.assertTrue(Video.objects.filter(id=video_id).exists())
