from __future__ import annotations

import os
from typing import Any

import requests
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import Profile


def _looks_like_clerk_identifier(value: str | None) -> bool:
    if not value:
        return False

    text = str(value).strip().lower()
    return text.startswith('user_') or text.startswith('org_')


def _slugify_username(display_name: str, email: str) -> str:
    candidate = ''.join(character for character in display_name.lower() if character.isalnum())
    if candidate:
        return candidate

    email_local_part = email.split('@', 1)[0].strip().lower().replace(' ', '_')
    if email_local_part and not _looks_like_clerk_identifier(email_local_part):
        return email_local_part

    return 'dashuser'


def _extract_email(user_data: dict[str, Any], fallback_email: str) -> str:
    email_addresses = user_data.get('email_addresses') or []
    primary_email_address_id = user_data.get('primary_email_address_id')

    for email_address in email_addresses:
        if not isinstance(email_address, dict):
            continue

        if primary_email_address_id and email_address.get('id') == primary_email_address_id:
            return str(email_address.get('email_address') or fallback_email)

    for email_address in email_addresses:
        if not isinstance(email_address, dict):
            continue

        candidate = str(email_address.get('email_address') or '').strip()
        if candidate:
            return candidate

    return fallback_email


def _extract_display_name(user_data: dict[str, Any], email: str) -> str:
    first_name = str(user_data.get('first_name') or '').strip()
    last_name = str(user_data.get('last_name') or '').strip()
    full_name = ' '.join(part for part in [first_name, last_name] if part).strip()

    if full_name:
        return full_name

    username = str(user_data.get('username') or '').strip()
    if username and not _looks_like_clerk_identifier(username):
        return username

    email_local_part = email.split('@', 1)[0].strip().replace('.', ' ').replace('_', ' ')
    if email_local_part:
        return email_local_part.title()

    return 'Dash User'


def fetch_clerk_user(clerk_user_id: str, secret_key: str) -> dict[str, Any]:
    response = requests.get(
        f'https://api.clerk.com/v1/users/{clerk_user_id}',
        headers={
            'Authorization': f'Bearer {secret_key}',
            'Content-Type': 'application/json',
        },
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError('Unexpected Clerk response format')
    return payload


class Command(BaseCommand):
    help = 'Backfill Profile rows from Clerk user records.'

    def add_arguments(self, parser):
        parser.add_argument('--clerk-user-id', dest='clerk_user_id', help='Sync a single Clerk user id')
        parser.add_argument('--all', action='store_true', help='Sync all existing profiles')
        parser.add_argument('--force', action='store_true', help='Overwrite profile fields even if they already look valid')

    def handle(self, *args, **options):
        secret_key = os.getenv('CLERK_SECRET_KEY')
        if not secret_key:
            raise CommandError('CLERK_SECRET_KEY is required to sync profiles from Clerk.')

        clerk_user_id = options.get('clerk_user_id')
        sync_all = bool(options.get('all'))
        force = bool(options.get('force'))

        if not clerk_user_id and not sync_all:
            raise CommandError('Provide --clerk-user-id or --all.')

        profiles = Profile.objects.all().order_by('id')
        if clerk_user_id:
            profiles = profiles.filter(clerk_user_id=clerk_user_id)

        updated_count = 0
        skipped_count = 0
        errors: list[str] = []

        for profile in profiles:
            try:
                clerk_user = fetch_clerk_user(profile.clerk_user_id, secret_key)
                fallback_email = f'{profile.clerk_user_id}@example.com'
                email = _extract_email(clerk_user, fallback_email)
                display_name = _extract_display_name(clerk_user, email)
                username = str(clerk_user.get('username') or '').strip()

                if not username or _looks_like_clerk_identifier(username):
                    username = _slugify_username(display_name, email)

                should_update = force
                should_update = should_update or profile.email != email
                should_update = should_update or profile.display_name != display_name
                should_update = should_update or profile.avatar_url != str(clerk_user.get('image_url') or '')
                should_update = should_update or profile.username != username

                if not should_update:
                    skipped_count += 1
                    continue

                profile.email = email
                profile.display_name = display_name
                profile.username = username
                profile.avatar_url = str(clerk_user.get('image_url') or '')
                profile.save(update_fields=['email', 'username', 'display_name', 'avatar_url', 'updated_at'])
                updated_count += 1
            except Exception as exc:  # pragma: no cover - command error path
                errors.append(f'{profile.clerk_user_id}: {exc}')

        self.stdout.write(self.style.SUCCESS(f'Updated {updated_count} profile(s).'))
        if skipped_count:
            self.stdout.write(self.style.NOTICE(f'Skipped {skipped_count} already-current profile(s).'))
        if errors:
            raise CommandError('One or more profiles failed to sync:\n' + '\n'.join(errors))