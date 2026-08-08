"""Surfacing the analyzer's dashcam verdict.

The classifier has run on every analysis since M3 and its verdict was never
read back out, so a viewer could not tell an actual dashcam clip from a
portrait video of a living room.

The distinction these pin hardest is between a *negative* verdict and *no
opinion*. "This does not look like dashcam footage" is a judgement; "this has
not been analyzed" is not, and rendering them the same way would put words in
the analyzer's mouth.
"""

from django.test import TestCase
from django.utils import timezone

from apps.videos.classification import dashcam_classification
from apps.videos.models import Video

DASHCAM = {
    'looks_like_dashcam': True,
    'reason': 'road objects across most frames, landscape orientation',
    'orientation': 'landscape',
    'road_classes_detected': ['car', 'traffic light', 'truck'],
    'strongest_road_class_share': 0.889,
}

NOT_DASHCAM = {
    'looks_like_dashcam': False,
    'reason': 'road objects present but orientation is portrait',
    'orientation': 'portrait',
    'road_classes_detected': ['car'],
    'strongest_road_class_share': 0.4,
}


class DashcamClassificationTests(TestCase):
    def test_a_positive_verdict_carries_its_reasoning(self):
        result = dashcam_classification({'classification': DASHCAM})

        self.assertTrue(result['looks_like_dashcam'])
        self.assertEqual(result['reason'],
                         'road objects across most frames, landscape orientation')
        self.assertEqual(result['orientation'], 'landscape')

    def test_a_negative_verdict_is_a_verdict_not_an_absence(self):
        result = dashcam_classification({'classification': NOT_DASHCAM})

        self.assertIsNotNone(result)
        self.assertFalse(result['looks_like_dashcam'])
        self.assertIn('portrait', result['reason'])

    def test_no_metadata_means_no_opinion(self):
        # Not analyzed, or analyzed before the classifier existed. Must not
        # read as "not a dashcam".
        self.assertIsNone(dashcam_classification(None))
        self.assertIsNone(dashcam_classification({}))
        self.assertIsNone(dashcam_classification({'duration_seconds': 8}))

    def test_detection_skipped_means_no_opinion(self):
        # --no-detect produces metadata with no classification key at all.
        self.assertIsNone(dashcam_classification({'width': 1920, 'height': 1080}))

    def test_a_malformed_verdict_is_no_opinion_rather_than_coerced(self):
        # An older or hand-edited record. Coercing "yes" to True would invent
        # a judgement the analyzer never made.
        self.assertIsNone(dashcam_classification({'classification': {'looks_like_dashcam': 'yes'}}))
        self.assertIsNone(dashcam_classification({'classification': 'dashcam'}))
        self.assertIsNone(dashcam_classification({'classification': None}))

    def test_a_verdict_missing_its_reason_still_reports_the_verdict(self):
        result = dashcam_classification({'classification': {'looks_like_dashcam': True}})

        self.assertTrue(result['looks_like_dashcam'])
        self.assertEqual(result['reason'], '')

    def test_the_payload_carries_no_internal_detection_detail(self):
        # ai_metadata holds model names, device, frame counts and thresholds.
        # None of that belongs in a public response.
        result = dashcam_classification({'classification': DASHCAM})

        self.assertEqual(set(result), {'looks_like_dashcam', 'reason', 'orientation'})


class VideoPayloadTests(TestCase):
    def _video(self, **overrides):
        fields = dict(
            owner_clerk_user_id='user_owner',
            title='Clip',
            status='ready',
            visibility='public',
            approval_status='approved',
            analysis_status='complete',
            analysis_requested_at=timezone.now(),
        )
        fields.update(overrides)
        return Video.objects.create(**fields)

    def test_the_verdict_reaches_a_serialized_video(self):
        video = self._video(ai_metadata={'classification': DASHCAM})

        payload = video.to_dict()

        self.assertTrue(payload['dashcam_classification']['looks_like_dashcam'])

    def test_an_unanalyzed_video_reports_no_verdict(self):
        video = self._video(analysis_status='pending', ai_metadata={})

        self.assertIsNone(video.to_dict()['dashcam_classification'])

    def test_the_feed_reports_the_verdict(self):
        self._video(title='Real dashcam', ai_metadata={'classification': DASHCAM})
        self._video(title='Not a dashcam', ai_metadata={'classification': NOT_DASHCAM})

        response = self.client.get('/api/feed/')
        self.assertEqual(response.status_code, 200)

        body = response.json()
        items = body if isinstance(body, list) else (body.get('items') or body.get('results') or [])
        verdicts = {
            item['title']: (item.get('dashcam_classification') or {}).get('looks_like_dashcam')
            for item in items
        }

        self.assertIs(verdicts.get('Real dashcam'), True)
        self.assertIs(verdicts.get('Not a dashcam'), False)

    def test_the_feed_does_not_leak_raw_analysis_metadata(self):
        # ai_metadata is selected only to derive the verdict from.
        self._video(ai_metadata={'classification': DASHCAM, 'detection': {'model': 'yolov8n.pt'}})

        body = self.client.get('/api/feed/').json()
        items = body if isinstance(body, list) else (body.get('items') or body.get('results') or [])

        self.assertTrue(items)
        for item in items:
            self.assertNotIn('ai_metadata', item)
