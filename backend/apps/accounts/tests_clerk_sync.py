from unittest.mock import Mock, patch

from django.core.management import call_command
from django.test import TestCase, override_settings

from apps.accounts.models import Profile


@override_settings(CLERK_SECRET_KEY='test-secret')
class ClerkProfileSyncCommandTests(TestCase):
    @patch('apps.accounts.management.commands.sync_profiles_from_clerk.requests.get')
    def test_sync_profiles_from_clerk_updates_bad_rows(self, mock_get):
        Profile.objects.create(
            clerk_user_id='user_abc123',
            email='user_abc123@example.com',
            username='user_abc123',
            display_name='user_abc123',
            avatar_url='',
        )

        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            'id': 'user_abc123',
            'first_name': 'Caden',
            'last_name': 'Dengel',
            'username': 'user_abc123',
            'image_url': 'https://example.com/avatar.png',
            'primary_email_address_id': 'em_1',
            'email_addresses': [
                {'id': 'em_1', 'email_address': 'caden@example.com'},
            ],
        }
        mock_get.return_value = response

        call_command('sync_profiles_from_clerk', '--clerk-user-id', 'user_abc123')

        profile = Profile.objects.get(clerk_user_id='user_abc123')
        self.assertEqual(profile.email, 'caden@example.com')
        self.assertEqual(profile.username, 'cadendengel')
        self.assertEqual(profile.display_name, 'Caden Dengel')
        self.assertEqual(profile.avatar_url, 'https://example.com/avatar.png')

    def test_sync_profiles_requires_argument(self):
        with self.assertRaises(Exception):
            call_command('sync_profiles_from_clerk')