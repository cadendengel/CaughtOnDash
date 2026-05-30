import json

from django.test import TestCase
from apps.videos.models import Video


class VideoSearchTests(TestCase):
    def test_search_by_title_returns_video(self):
        # create a video
        create_response = self.client.post(
            '/api/videos/upload-url/',
            data=json.dumps(
                {
                    'clerk_user_id': 'search-user',
                    'title': 'Unique Brake Check Event',
                    'description': 'test description',
                    'original_filename': 'dashcam.mp4',
                    'duration_seconds': 10,
                }
            ),
            content_type='application/json',
        )

        self.assertEqual(create_response.status_code, 200)

        # mark video as ready so search includes it
        Video.objects.filter(id=create_response.json()['video']['id']).update(status='ready')

        # perform a search that should match the title
        resp = self.client.get('/api/videos/search/?q=Brake+Check')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        items = data.get('payload', {}).get('items', []) or data.get('items', [])
        self.assertTrue(any('Brake Check' in (v.get('title') or '') for v in items))

    def test_search_empty_query_returns_empty(self):
        resp = self.client.get('/api/videos/search/?q=')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        items = data.get('payload', {}).get('items', []) or data.get('items', [])
        self.assertEqual(items, [])
