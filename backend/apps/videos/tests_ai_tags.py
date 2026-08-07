"""Detected tags must reach the places users actually look.

The analyzer wrote tags into ai_tags as plain strings. The feed renders `tags`
and search indexes `tags`, so detections were invisible to both. The tag
vocabulary already allowed an 'ai' source and the frontend already styled it --
these cover connecting the two without trampling tags a person added.
"""

from django.test import TestCase
from django.utils import timezone

from apps.videos.models import Video
from apps.videos.worker_services import claim_job, complete_job, merge_ai_tags

WORKER = 'worker-1'


def _claimed_video(**overrides):
    fields = dict(
        owner_clerk_user_id='user_owner',
        title='Clip',
        status='ready',
        visibility='public',
        analysis_status='pending',
        analysis_requested_at=timezone.now(),
    )
    fields.update(overrides)
    video = Video.objects.create(**fields)
    claim_job(video.id, WORKER, 'Worker One')
    video.refresh_from_db()
    return video


class MergeAiTagsTests(TestCase):
    def test_detected_tags_become_ai_sourced(self):
        merged = merge_ai_tags([], ['car', 'person'])
        self.assertEqual(merged, [
            {'text': 'car', 'source': 'ai'},
            {'text': 'person', 'source': 'ai'},
        ])

    def test_user_tags_survive_analysis(self):
        existing = [{'text': 'my commute', 'source': 'user'}]
        merged = merge_ai_tags(existing, ['car'])
        self.assertIn({'text': 'my commute', 'source': 'user'}, merged)
        self.assertIn({'text': 'car', 'source': 'ai'}, merged)

    def test_admin_tags_survive_analysis(self):
        existing = [{'text': 'featured', 'source': 'admin'}]
        merged = merge_ai_tags(existing, ['truck'])
        self.assertIn({'text': 'featured', 'source': 'admin'}, merged)

    def test_previous_ai_tags_are_replaced_not_accumulated(self):
        existing = [
            {'text': 'bus', 'source': 'ai'},
            {'text': 'keep me', 'source': 'user'},
        ]
        merged = merge_ai_tags(existing, ['car'])

        self.assertNotIn({'text': 'bus', 'source': 'ai'}, merged)
        self.assertIn({'text': 'car', 'source': 'ai'}, merged)
        self.assertIn({'text': 'keep me', 'source': 'user'}, merged)

    def test_a_human_tag_wins_over_the_same_detected_tag(self):
        # Otherwise the same word renders twice, in two different colours.
        existing = [{'text': 'Car', 'source': 'user'}]
        merged = merge_ai_tags(existing, ['car'])

        self.assertEqual(merged, [{'text': 'Car', 'source': 'user'}])

    def test_empty_detections_clear_previous_ai_tags(self):
        existing = [
            {'text': 'car', 'source': 'ai'},
            {'text': 'mine', 'source': 'user'},
        ]
        self.assertEqual(merge_ai_tags(existing, []), [{'text': 'mine', 'source': 'user'}])

    def test_legacy_plain_string_tags_are_normalised(self):
        merged = merge_ai_tags(['old style'], ['car'])
        self.assertIn({'text': 'old style', 'source': 'user'}, merged)


class CompleteJobPublishesResultsTests(TestCase):
    def test_detected_tags_land_in_tags_and_ai_tags(self):
        video = _claimed_video()
        complete_job(video.id, WORKER, 'summary', ['car', 'person'], [],
                     {'duration_seconds': 42.4})
        video.refresh_from_db()

        self.assertEqual(video.ai_tags, ['car', 'person'])
        self.assertEqual(
            video.tags,
            [{'text': 'car', 'source': 'ai'}, {'text': 'person', 'source': 'ai'}],
        )

    def test_duration_is_taken_from_the_analyzer(self):
        video = _claimed_video()
        self.assertEqual(video.duration_seconds, 0)

        complete_job(video.id, WORKER, 's', [], [], {'duration_seconds': 42.4})
        video.refresh_from_db()
        self.assertEqual(video.duration_seconds, 42)

    def test_duration_is_left_alone_when_the_analyzer_reports_none(self):
        video = _claimed_video(duration_seconds=17)

        complete_job(video.id, WORKER, 's', [], [], {'width': 640})
        video.refresh_from_db()
        self.assertEqual(video.duration_seconds, 17)

    def test_zero_duration_does_not_overwrite_a_known_one(self):
        video = _claimed_video(duration_seconds=17)

        complete_job(video.id, WORKER, 's', [], [], {'duration_seconds': 0})
        video.refresh_from_db()
        self.assertEqual(video.duration_seconds, 17)

    def test_reanalysis_does_not_delete_user_tags(self):
        video = _claimed_video(tags=[{'text': 'my commute', 'source': 'user'}])

        complete_job(video.id, WORKER, 's', ['car'], [], {})
        video.refresh_from_db()

        texts = {tag['text'] for tag in video.tags}
        self.assertIn('my commute', texts)
        self.assertIn('car', texts)

    def test_detected_tags_are_searchable(self):
        # The point of the whole change: search indexes `tags`, so a detection
        # that only reached ai_tags could never be found.
        video = _claimed_video(title='Nondescript clip')
        complete_job(video.id, WORKER, 's', ['motorcycle'], [], {})

        response = self.client.get('/api/videos/search/?q=motorcycle')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item['id'] for item in response.json()['items']], [str(video.id)])
