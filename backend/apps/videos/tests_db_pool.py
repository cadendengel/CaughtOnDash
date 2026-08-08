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
from unittest import mock

from django.test import SimpleTestCase

from caughtondash.env import get_int


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
