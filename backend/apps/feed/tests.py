from django.test import TestCase

from apps.accounts.models import Profile
from apps.videos.models import Video


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
