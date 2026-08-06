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


class FeedAnalysisStateTests(TestCase):
    """The feed omitted analysis state entirely.

    Feed cards could not show an analysis badge, and the owner Re-analyze
    button never rendered because analysis_status came back undefined.
    """

    AI_TAGS = ['dashcam', 'highway']

    def setUp(self):
        from django.utils import timezone

        self.video = Video.objects.create(
            owner_clerk_user_id='user_owner',
            title='Analysed clip',
            visibility='public',
            status='ready',
            analysis_status='complete',
            analysis_stage='complete',
            analysis_progress=100,
            analysis_completed_at=timezone.now(),
            ai_summary='A real summary from the worker.',
            ai_tags=self.AI_TAGS,
        )

    def _item(self):
        response = self.client.get('/api/feed/')
        self.assertEqual(response.status_code, 200)
        return response.json()['items'][0]

    def test_feed_reports_analysis_state(self):
        item = self._item()
        self.assertEqual(item['analysis_status'], 'complete')
        self.assertEqual(item['analysis_stage'], 'complete')
        self.assertEqual(item['analysis_progress'], 100)

    def test_feed_exposes_ai_results(self):
        item = self._item()
        self.assertEqual(item['ai_summary'], 'A real summary from the worker.')
        self.assertEqual(item['ai_tags'], self.AI_TAGS)

    def test_feed_includes_views_and_avatar_url(self):
        Video.objects.filter(id=self.video.id).update(views=17)
        item = self._item()
        self.assertEqual(item['views'], 17)
        self.assertIn('avatar_url', item)

    def test_feed_exposes_worker_last_seen_for_staleness_checks(self):
        # The UI uses this to decide whether a 'processing' job is wedged and
        # should offer its owner a re-analyze option.
        from django.utils import timezone

        seen = timezone.now()
        Video.objects.filter(id=self.video.id).update(
            analysis_status='processing', worker_last_seen_at=seen)
        self.assertIsNotNone(self._item()['worker_last_seen_at'])

    def test_feed_serializes_tags_like_the_other_endpoints(self):
        Video.objects.filter(id=self.video.id).update(
            tags=[{'text': 'Highway', 'source': 'user'}])
        feed_tags = self._item()['tags']
        detail = self.client.get(
            f'/api/videos/{self.video.id}/', HTTP_X_SKIP_VIEW_COUNT='1').json()['video']
        self.assertEqual(feed_tags, detail['tags'])
        self.assertEqual(feed_tags, [{'text': 'Highway', 'source': 'user'}])
