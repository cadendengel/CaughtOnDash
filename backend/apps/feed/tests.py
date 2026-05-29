from django.test import TestCase

from apps.accounts.models import Profile
from apps.videos.models import Video, VideoComment, VideoLike


class FeedViewTests(TestCase):
    def test_feed_includes_profile_username(self):
        Profile.objects.create(
            clerk_user_id='test-user',
            email='test@example.com',
            username='testdriver',
            display_name='Test Driver',
        )
        video = Video.objects.create(
            owner_clerk_user_id='test-user',
            title='Dashcam clip',
            description='demo',
            visibility='public',
            status='ready',
            playback_url='https://example.com/video.mp4',
        )

        response = self.client.get('/api/feed/')

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['count'], 1)
        self.assertEqual(payload['items'][0]['id'], str(video.id))
        self.assertEqual(payload['items'][0]['username'], 'testdriver')
        self.assertEqual(payload['items'][0]['display_name'], 'Test Driver')

    def test_feed_includes_engagement_counts_and_liked_state(self):
        Profile.objects.create(
            clerk_user_id='viewer-user',
            email='viewer@example.com',
            username='viewer',
            display_name='Viewer',
        )
        video = Video.objects.create(
            owner_clerk_user_id='owner-user',
            title='Dashcam clip',
            description='demo',
            visibility='public',
            status='ready',
            playback_url='https://example.com/video.mp4',
        )
        VideoLike.objects.create(video=video, user_clerk_user_id='viewer-user')
        VideoComment.objects.create(video=video, user_clerk_user_id='viewer-user', text='Nice catch.')

        response = self.client.get('/api/feed/', HTTP_X_CLERK_USER_ID='viewer-user')

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['items'][0]['likes_count'], 1)
        self.assertEqual(payload['items'][0]['comments_count'], 1)
        self.assertTrue(payload['items'][0]['liked'])
