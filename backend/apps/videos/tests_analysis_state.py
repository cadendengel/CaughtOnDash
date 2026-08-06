"""The API must report real analysis state, not serializer defaults.

video_detail_view selected its columns with .values() and omitted every
analysis field, so _serialize_video_row filled in its defaults and each video
reported analysis_status='pending' with empty AI results whatever the database
said. The feed omitted the fields entirely. Both fed the UI, so the analysis
badge and the owner Re-analyze button were driven by fabricated data.
"""

from django.test import TestCase
from django.utils import timezone

from apps.videos.models import Video

AI_TAGS = ['dashcam', 'highway']
AI_EVENTS = [{'timestamp_seconds': 1.5, 'label': 'event', 'description': 'd', 'confidence': 0.9}]


def _analysed_video(**overrides):
    fields = dict(
        owner_clerk_user_id='user_owner',
        title='Analysed clip',
        status='ready',
        visibility='public',
        analysis_status='complete',
        analysis_stage='complete',
        analysis_progress=100,
        analysis_completed_at=timezone.now(),
        ai_summary='A real summary from the worker.',
        ai_tags=AI_TAGS,
        ai_events=AI_EVENTS,
        ai_metadata={'analyzer_version': 'test-1'},
        worker_id='worker-1',
        worker_name='Worker One',
    )
    fields.update(overrides)
    return Video.objects.create(**fields)


class VideoDetailAnalysisStateTests(TestCase):
    def setUp(self):
        self.video = _analysed_video()

    def _detail(self):
        response = self.client.get(f'/api/videos/{self.video.id}/', HTTP_X_SKIP_VIEW_COUNT='1')
        self.assertEqual(response.status_code, 200)
        return response.json()['video']

    def test_detail_reports_the_real_analysis_status(self):
        self.assertEqual(self._detail()['analysis_status'], 'complete')

    def test_detail_reports_the_real_stage_and_progress(self):
        payload = self._detail()
        self.assertEqual(payload['analysis_stage'], 'complete')
        self.assertEqual(payload['analysis_progress'], 100)

    def test_detail_exposes_ai_results(self):
        payload = self._detail()
        self.assertEqual(payload['ai_summary'], 'A real summary from the worker.')
        self.assertEqual(payload['ai_tags'], AI_TAGS)
        self.assertEqual(payload['ai_events'], AI_EVENTS)
        self.assertEqual(payload['ai_metadata'], {'analyzer_version': 'test-1'})

    def test_detail_reports_worker_assignment(self):
        payload = self._detail()
        self.assertEqual(payload['worker_id'], 'worker-1')
        self.assertEqual(payload['worker_name'], 'Worker One')

    def test_detail_reports_a_processing_video_as_processing(self):
        # The stuck-job case: previously this read back as 'pending', so the UI
        # could not tell a queued video from one wedged mid-run.
        Video.objects.filter(id=self.video.id).update(
            analysis_status='processing', analysis_stage='analyzing', analysis_progress=40)
        payload = self._detail()
        self.assertEqual(payload['analysis_status'], 'processing')
        self.assertEqual(payload['analysis_stage'], 'analyzing')
        self.assertEqual(payload['analysis_progress'], 40)

    def test_detail_reports_a_failed_video_with_its_error(self):
        Video.objects.filter(id=self.video.id).update(
            analysis_status='failed', analysis_error='disk full')
        payload = self._detail()
        self.assertEqual(payload['analysis_status'], 'failed')
        self.assertEqual(payload['analysis_error'], 'disk full')

    def test_detail_agrees_with_search_for_the_same_video(self):
        # These disagreed in production: search read model instances and told the
        # truth, detail used .values() and invented 'pending'.
        Video.objects.filter(id=self.video.id).update(analysis_status='processing')
        detail = self._detail()
        search = self.client.get('/api/videos/search/?q=Analysed').json()['items'][0]
        self.assertEqual(detail['analysis_status'], search['analysis_status'])
        self.assertEqual(detail['ai_summary'], search['ai_summary'])
        self.assertEqual(detail['ai_tags'], search['ai_tags'])
