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


class UploadOwnershipTests(TestCase):
    """The file behind a video can be set only by its owner.

    It is fingerprinted as incident evidence, so a stranger replacing it would
    replace the evidence behind someone else's report.
    """

    def setUp(self):
        self.video = Video.objects.create(owner_clerk_user_id=OWNER, title='Clip', status='pending')

    def _upload(self, caller):
        from unittest.mock import patch

        from django.core.files.uploadedfile import SimpleUploadedFile

        with patch('apps.videos.views.upload_bytes_to_supabase', return_value='https://cdn.example.com/c.mp4') as store:
            response = self.client.post('/api/videos/upload/', {
                'video_id': str(self.video.id),
                'file': SimpleUploadedFile('dash.mp4', b'bytes', content_type='video/mp4'),
            }, HTTP_X_CLERK_USER_ID=caller)
        return response, store

    def test_a_stranger_cannot_upload_or_replace_the_file(self):
        response, store = self._upload(STRANGER)
        self.assertEqual(response.status_code, 403)
        store.assert_not_called()
        self.video.refresh_from_db()
        self.assertEqual(self.video.status, 'pending')

    def test_an_admin_cannot_either(self):
        # Admins moderate; they do not supply footage for other people's videos.
        AdminUser.objects.create(clerk_user_id=ADMIN)
        self.assertEqual(self._upload(ADMIN)[0].status_code, 403)

    def test_the_owner_can(self):
        response, store = self._upload(OWNER)
        self.assertEqual(response.status_code, 200)
        store.assert_called_once()

    def test_visibility_is_validated_when_the_record_is_created(self):
        for visibility, expected in (('private', 200), ('unlisted', 200), ('secret', 400)):
            with self.subTest(visibility=visibility):
                response = self.client.post(
                    '/api/videos/upload-url/', data=json.dumps({'title': 'x', 'visibility': visibility}),
                    content_type='application/json', HTTP_X_CLERK_USER_ID=OWNER)
                self.assertEqual(response.status_code, expected)
                if expected == 200:
                    self.assertEqual(response.json()['video']['visibility'], visibility)
