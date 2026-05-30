from django.test import TestCase, RequestFactory
from apps.accounts.models import AdminUser
from apps.accounts.auth import admin_required
from apps.store import response_envelope
from django.http import JsonResponse


class AdminUserTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_create_and_query_admin(self):
        AdminUser.objects.create(clerk_user_id='admin-1', email='admin@example.com')
        self.assertTrue(AdminUser.is_admin_for('admin-1'))
        self.assertFalse(AdminUser.is_admin_for('nonexistent'))

    def test_admin_required_decorator_blocks(self):
        def view(request):
            return JsonResponse(response_envelope('ok', {'msg': 'ok'}))

        protected = admin_required(view)
        req = self.factory.get('/admin-test', HTTP_X_CLERK_USER_ID='not-admin')
        resp = protected(req)
        self.assertEqual(resp.status_code, 403)

    def test_admin_required_decorator_allows(self):
        AdminUser.objects.create(clerk_user_id='super-1')

        def view(request):
            return JsonResponse(response_envelope('ok', {'msg': 'ok'}))

        protected = admin_required(view)
        req = self.factory.get('/admin-test', HTTP_X_CLERK_USER_ID='super-1')
        resp = protected(req)
        self.assertEqual(resp.status_code, 200)
