"""Database connection lifetime under ASGI.

Production went down with EMAXCONNSESSION: every request failing because all
15 of Supabase's session-pooler clients were taken.

The cause was a setting that had been correct under a different server.
conn_max_age=600 held each connection for ten minutes, which is cheap under
gunicorn -- one worker, a few threads, a few connections -- and ruinous under
daphne, where every synchronous ORM call runs in asgiref's thread pool and
each thread keeps its own connection. The pool defaults to min(32, cpu + 4),
so the app could hold twice the pooler's limit and hold it for ten minutes.

Nothing in the switch to daphne made this visible: it only appears under
enough concurrency to spread work across threads.
"""

import os
import subprocess
import sys
from unittest import mock

from django.test import SimpleTestCase

from caughtondash.env import get_int


class EffectiveConnMaxAgeTests(SimpleTestCase):
    """The value that actually reaches DATABASES, not just what get_int returns.

    A stale block in settings once forced CONN_MAX_AGE=600 after the Postgres
    branch set it to 0, so every connection was held for ten minutes and the
    pooler filled under load (QA ISSUE-6). The old tests only checked get_int in
    isolation, so they never saw it. This resolves settings the way production
    does -- with a Postgres DATABASE_URL -- and asserts the effective value.
    """

    def _conn_max_age(self, database_url, extra_env=None):
        env = {
            **os.environ,
            'DEBUG': 'true',
            'SECRET_KEY': 'test-only',
            'DATABASE_URL': database_url,
        }
        env.pop('DB_CONN_MAX_AGE', None)
        if extra_env:
            env.update(extra_env)
        code = (
            'import django, os; '
            "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'caughtondash.settings'); "
            'django.setup(); '
            'from django.conf import settings; '
            "print(settings.DATABASES['default']['CONN_MAX_AGE'])"
        )
        out = subprocess.run(
            [sys.executable, '-c', code], capture_output=True, text=True, timeout=60, env=env,
        )
        assert out.returncode == 0, out.stderr
        return int(out.stdout.strip().splitlines()[-1])

    def test_transaction_pooler_default_is_zero(self):
        url = 'postgresql://u:p@host.pooler.supabase.com:6543/postgres'
        self.assertEqual(self._conn_max_age(url), 0)

    def test_session_pooler_default_is_zero(self):
        url = 'postgresql://u:p@host.pooler.supabase.com:5432/postgres'
        self.assertEqual(self._conn_max_age(url), 0)

    def test_env_override_is_respected(self):
        url = 'postgresql://u:p@host.pooler.supabase.com:6543/postgres'
        self.assertEqual(self._conn_max_age(url, {'DB_CONN_MAX_AGE': '30'}), 30)


class ConnectionLifetimeTests(SimpleTestCase):
    def test_the_default_is_no_persistent_connection(self):
        """Zero means Django closes the connection at the end of each request.

        The session pooler is itself the connection pool, so holding
        Django-side connections open duplicates it while competing for the same
        fifteen slots. There is nothing to gain and a pool to exhaust.
        """
        self.assertEqual(get_int('DB_CONN_MAX_AGE', 0, environ={}), 0)

    def test_it_can_be_raised_deliberately(self):
        # Worth keeping reachable: on a direct connection, without a pooler in
        # front, persistent connections are the right choice.
        self.assertEqual(get_int('DB_CONN_MAX_AGE', 0, environ={'DB_CONN_MAX_AGE': '60'}), 60)

    def test_a_non_numeric_value_is_refused_rather_than_ignored(self):
        from django.core.exceptions import ImproperlyConfigured

        with self.assertRaises(ImproperlyConfigured):
            get_int('DB_CONN_MAX_AGE', 0, environ={'DB_CONN_MAX_AGE': 'ten minutes'})

    def test_blank_falls_back_to_the_default(self):
        # An env var set to empty string is how a platform represents "unset".
        self.assertEqual(get_int('DB_CONN_MAX_AGE', 0, environ={'DB_CONN_MAX_AGE': ''}), 0)


class AsgiThreadCapTests(SimpleTestCase):
    def test_the_asgi_entrypoint_caps_the_thread_pool(self):
        """Each thread that touches the ORM holds a connection.

        Uncapped, asgiref uses min(32, cpu + 4) threads -- more than the
        pooler allows on its own. This bounds the worst case even if
        persistent connections are re-enabled later.
        """
        import importlib

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('ASGI_THREADS', None)
            # reload, not import: the module is already in sys.modules from
            # application startup, so a plain import would return the cached
            # one without re-running the setdefault this asserts on.
            importlib.reload(importlib.import_module('caughtondash.asgi'))
            self.assertEqual(os.environ.get('ASGI_THREADS'), '8')

    def test_an_explicit_thread_count_is_respected(self):
        # setdefault, not assignment: a deployment that has tuned this keeps
        # its own value.
        import importlib

        with mock.patch.dict(os.environ, {'ASGI_THREADS': '4'}):
            importlib.reload(importlib.import_module('caughtondash.asgi'))
            self.assertEqual(os.environ.get('ASGI_THREADS'), '4')
