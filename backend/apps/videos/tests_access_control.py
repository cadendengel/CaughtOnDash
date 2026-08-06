"""Access-control tests for video visibility, ownership and method handling.

These cover holes that were reachable without authentication: private videos
readable by anyone with the id, edit/delete with no ownership check, and an
unsupported method returning 500 instead of 405.
"""

import json

from django.test import TestCase

from apps.accounts.models import AdminUser
from apps.videos.models import Video, VideoComment

OWNER = 'user_owner'
STRANGER = 'user_stranger'
ADMIN = 'user_admin'


class PrivateVideoVisibilityTests(TestCase):
    def setUp(self):
        self.private = Video.objects.create(
            owner_clerk_user_id=OWNER, title='Private clip', status='ready', visibility='private')
        self.public = Video.objects.create(
            owner_clerk_user_id=OWNER, title='Public clip', status='ready', visibility='public')
        self.unlisted = Video.objects.create(
            owner_clerk_user_id=OWNER, title='Unlisted clip', status='ready', visibility='unlisted')

    def test_anonymous_cannot_read_private_video(self):
        response = self.client.get(f'/api/videos/{self.private.id}/')
        self.assertEqual(response.status_code, 404)

    def test_stranger_cannot_read_private_video(self):
        response = self.client.get(f'/api/videos/{self.private.id}/', HTTP_X_CLERK_USER_ID=STRANGER)
        self.assertEqual(response.status_code, 404)

    def test_owner_can_read_own_private_video(self):
        response = self.client.get(f'/api/videos/{self.private.id}/', HTTP_X_CLERK_USER_ID=OWNER)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['video']['title'], 'Private clip')

    def test_admin_can_read_private_video(self):
        AdminUser.objects.create(clerk_user_id=ADMIN)
        response = self.client.get(f'/api/videos/{self.private.id}/', HTTP_X_CLERK_USER_ID=ADMIN)
        self.assertEqual(response.status_code, 200)

    def test_public_and_unlisted_remain_readable_by_anyone(self):
        # Unlisted is reachable by direct link by design; only private is gated.
        for video in (self.public, self.unlisted):
            with self.subTest(visibility=video.visibility):
                response = self.client.get(f'/api/videos/{video.id}/')
                self.assertEqual(response.status_code, 200)

    def test_blocked_request_does_not_increment_view_count(self):
        self.client.get(f'/api/videos/{self.private.id}/', HTTP_X_CLERK_USER_ID=STRANGER)
        self.private.refresh_from_db()
        self.assertEqual(self.private.views, 0)


class VideoOwnershipTests(TestCase):
    """The manage endpoint is still CSRF-protected, so these call the view directly.

    That isolates the authorization decision from the transport question, which
    is deliberately unchanged on this branch.
    """

    def setUp(self):
        self.video = Video.objects.create(
            owner_clerk_user_id=OWNER, title='Original title', status='ready', visibility='public')

    def _patch_as(self, clerk_user_id, payload):
        from django.test import RequestFactory

        from apps.videos.views import video_update_delete_view

        request = RequestFactory().patch(
            f'/api/videos/{self.video.id}/manage/',
            data=json.dumps(payload),
            content_type='application/json',
            HTTP_X_CLERK_USER_ID=clerk_user_id,
        )
        return video_update_delete_view(request, self.video.id)

    def _delete_as(self, clerk_user_id):
        from django.test import RequestFactory

        from apps.videos.views import video_update_delete_view

        request = RequestFactory().delete(
            f'/api/videos/{self.video.id}/manage/', HTTP_X_CLERK_USER_ID=clerk_user_id)
        return video_update_delete_view(request, self.video.id)

    def test_stranger_cannot_edit_video(self):
        response = self._patch_as(STRANGER, {'title': 'PWNED'})
        self.assertEqual(response.status_code, 403)
        self.video.refresh_from_db()
        self.assertEqual(self.video.title, 'Original title')

    def test_stranger_cannot_delete_video(self):
        response = self._delete_as(STRANGER)
        self.assertEqual(response.status_code, 403)
        self.video.refresh_from_db()
        self.assertIsNone(self.video.deleted_at)

    def test_owner_can_edit_video(self):
        response = self._patch_as(OWNER, {'title': 'Renamed by owner'})
        self.assertEqual(response.status_code, 200)
        self.video.refresh_from_db()
        self.assertEqual(self.video.title, 'Renamed by owner')

    def test_owner_can_soft_delete_video(self):
        response = self._delete_as(OWNER)
        self.assertEqual(response.status_code, 200)
        self.video.refresh_from_db()
        self.assertIsNotNone(self.video.deleted_at)

    def test_admin_is_not_granted_an_implicit_bypass(self):
        # Admins act through the dedicated admin endpoints, not this one.
        AdminUser.objects.create(clerk_user_id=ADMIN)
        response = self._patch_as(ADMIN, {'title': 'Admin edit'})
        self.assertEqual(response.status_code, 403)


class CommentsMethodHandlingTests(TestCase):
    def setUp(self):
        self.video = Video.objects.create(
            owner_clerk_user_id=OWNER, title='Clip', status='ready', visibility='public')

    def test_unsupported_method_returns_405_not_500(self):
        response = self.client.put(
            f'/api/videos/{self.video.id}/comments/', data='{}', content_type='application/json')
        self.assertEqual(response.status_code, 405)
        self.assertEqual(sorted(response.json()['allowed']), ['GET', 'POST'])

    def test_get_and_post_still_work(self):
        post = self.client.post(
            f'/api/videos/{self.video.id}/comments/',
            data=json.dumps({'text': 'hello', 'clerk_user_id': OWNER}),
            content_type='application/json',
        )
        self.assertEqual(post.status_code, 201)

        get = self.client.get(f'/api/videos/{self.video.id}/comments/')
        self.assertEqual(get.status_code, 200)
        self.assertEqual(get.json()['count'], 1)
        self.assertEqual(VideoComment.objects.count(), 1)
