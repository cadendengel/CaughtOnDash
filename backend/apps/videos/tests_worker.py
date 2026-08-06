from django.test import TestCase
from django.utils import timezone

from apps.videos.models import Video
from apps.videos.worker_services import claim_job, complete_job, get_next_pending_job


class WorkerServiceTests(TestCase):
    def test_pending_video_can_be_claimed_and_completed(self):
        video = Video.objects.create(
            owner_clerk_user_id="local-test-user",
            title="Local worker smoke test",
            analysis_status="pending",
            analysis_requested_at=timezone.now(),
        )

        pending_job = get_next_pending_job()
        self.assertIsNotNone(pending_job)
        self.assertEqual(pending_job.id, video.id)

        claim_result = claim_job(video.id, "local-worker", "Local Worker")
        self.assertTrue(claim_result["success"])

        complete_result = complete_job(
            video.id,
            "local-worker",
            "Smoke test summary",
            ["smoke", "test"],
            [],
            {"source": "test"},
        )
        self.assertTrue(complete_result["success"])

        refreshed_video = Video.objects.get(id=video.id)
        self.assertEqual(refreshed_video.analysis_status, "complete")
        self.assertEqual(refreshed_video.ai_summary, "Smoke test summary")
        self.assertEqual(refreshed_video.ai_tags, ["smoke", "test"])
