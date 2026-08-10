"""Regression tests for the QA findings fixed in this branch.

Each test names the ISSUE it locks down. The gates only bite when a real Clerk
JWT is required (production); with REQUIRE_CLERK_JWT off, get_identity yields the
non-blank 'demo-user' and writes proceed as before -- so these override the
setting to exercise the production path.
"""

import json

from django.test import Client, TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import Profile
from apps.accounts.views import upsert_profile
from apps.videos.models import Video, VideoComment
from apps.videos.worker_services import claim_job, open_analysis_run


def _ready_video(**overrides):
    fields = dict(
        owner_clerk_user_id='user_owner',
        title='Clip',
        status='ready',
        visibility='public',
        approval_status='approved',
        analysis_status='pending',
        analysis_requested_at=timezone.now(),
    )
    fields.update(overrides)
    return Video.objects.create(**fields)


class ProfileClobberTests(TestCase):
    """ISSUE-3: posting a video must not overwrite the poster's name/handle."""

    def test_upsert_does_not_overwrite_a_real_name_with_an_id_placeholder(self):
        real = {
            'clerk_user_id': 'user_abc123',
            'email': 'nova@example.com',
            'username': 'nova_lanes',
            'display_name': 'Nova Reyes',
            'avatar_url': '',
        }
        upsert_profile(real, {'bio': 'hi'})

        # A later content-endpoint upsert whose identity fell back to id-derived
        # placeholders: username = id with separators stripped ('userabc123'),
        # display_name = id verbatim ('user_abc123'). This is exactly the shape
        # that clobbered profiles in production.
        derived = {
            'clerk_user_id': 'user_abc123',
            'email': 'nova@example.com',
            'username': 'userabc123',
            'display_name': 'user_abc123',
            'avatar_url': '',
        }
        upsert_profile(derived, None)

        p = Profile.objects.get(clerk_user_id='user_abc123')
        self.assertEqual(p.username, 'nova_lanes')
        self.assertEqual(p.display_name, 'Nova Reyes')

    def test_upsert_still_applies_a_real_new_name(self):
        upsert_profile({'clerk_user_id': 'u1', 'email': '', 'username': 'old',
                        'display_name': 'Old', 'avatar_url': ''}, None)
        upsert_profile({'clerk_user_id': 'u1', 'email': '', 'username': 'new_handle',
                        'display_name': 'New Name', 'avatar_url': ''}, None)
        p = Profile.objects.get(clerk_user_id='u1')
        self.assertEqual(p.username, 'new_handle')
        self.assertEqual(p.display_name, 'New Name')

    @override_settings(REQUIRE_CLERK_JWT=True)
    def test_posting_a_video_anonymously_is_rejected_not_clobbering(self):
        # Also covers ISSUE-1/7: no identity -> 401 on the write path.
        c = Client()
        r = c.post('/api/videos/upload-url/', data=json.dumps({'title': 'x'}),
                   content_type='application/json')
        self.assertEqual(r.status_code, 401)


@override_settings(REQUIRE_CLERK_JWT=True)
class AnonymousWriteTests(TestCase):
    """ISSUE-1 / ISSUE-7: writes require a real identity in production."""

    def setUp(self):
        self.client = Client()
        self.video = _ready_video()

    def test_bootstrap_anonymous_is_401(self):
        r = self.client.post('/api/auth/bootstrap/', data=json.dumps({'bio': 'x'}),
                             content_type='application/json')
        self.assertEqual(r.status_code, 401)

    def test_like_anonymous_is_401(self):
        r = self.client.post(f'/api/videos/{self.video.id}/like/')
        self.assertEqual(r.status_code, 401)

    def test_comment_anonymous_is_401(self):
        r = self.client.post(f'/api/videos/{self.video.id}/comments/',
                             data=json.dumps({'text': 'hi'}), content_type='application/json')
        self.assertEqual(r.status_code, 401)
        self.assertEqual(VideoComment.objects.count(), 0)


class MalformedJsonTests(TestCase):
    """ISSUE-8: a non-empty unparseable body is a 400, not a silent success."""

    def test_bootstrap_malformed_json_is_400(self):
        r = Client().post('/api/auth/bootstrap/', data=b'{not json',
                          content_type='application/json')
        self.assertEqual(r.status_code, 400)


class WorkerNotReadyTests(TestCase):
    """ISSUE-4: a not-ready (upload-incomplete) video cannot be claimed."""

    def test_claim_refuses_a_pending_video(self):
        pending = _ready_video(status='pending', playback_url='')
        result = claim_job(pending.id, 'worker-1', 'Worker One')
        self.assertFalse(result['success'])
        self.assertIn('not ready', result['error'].lower())

    def test_claim_allows_a_ready_video(self):
        ready = _ready_video()
        result = claim_job(ready.id, 'worker-1', 'Worker One')
        self.assertTrue(result['success'])


class ManageCsrfTests(TestCase):
    """ISSUE-9: the owner edit/delete endpoint must be CSRF-exempt like every
    other write view, since auth is a Bearer JWT with no CSRF token. Without the
    exemption the CSRF middleware 403s every PATCH/DELETE and an owner can never
    edit or delete their own video through the API.
    """

    def test_owner_can_delete_through_the_csrf_enforcing_client(self):
        # enforce_csrf_checks mirrors production, where the middleware would
        # otherwise 403 before the view runs. With REQUIRE_CLERK_JWT off the
        # caller resolves to 'demo-user', which owns this video.
        video = _ready_video(owner_clerk_user_id='demo-user')
        client = Client(enforce_csrf_checks=True)

        response = client.delete(f'/api/videos/{video.id}/manage/')

        self.assertEqual(response.status_code, 200)
        video.refresh_from_db()
        self.assertIsNotNone(video.deleted_at)

    def test_owner_can_patch_through_the_csrf_enforcing_client(self):
        video = _ready_video(owner_clerk_user_id='demo-user')
        client = Client(enforce_csrf_checks=True)

        response = client.patch(
            f'/api/videos/{video.id}/manage/',
            data=json.dumps({'title': 'Renamed'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        video.refresh_from_db()
        self.assertEqual(video.title, 'Renamed')
