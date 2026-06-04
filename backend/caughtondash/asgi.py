"""ASGI config for caughtondash project."""

import os

from django.core.management import call_command
from django.core.asgi import get_asgi_application


def _apply_pending_migrations() -> None:
	if os.getenv('SKIP_AUTO_MIGRATE', '').lower() in {'1', 'true', 'yes'}:
		return

	if not os.getenv('DATABASE_URL'):
		return

	call_command('migrate', interactive=False, run_syncdb=True, verbosity=0)


os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'caughtondash.settings')
_apply_pending_migrations()

application = get_asgi_application()
