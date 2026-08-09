"""The health endpoint, /api/health/.

Lives here rather than beside the view because `manage.py test apps.videos` is
the documented command and a test nobody runs is not a test. tests_db_pool.py
sets the same precedent.

What these assert is mostly *when it must fail*. A health check that answers
200 too readily is worse than none: it converts an outage into a monitor that
says everything is fine.
"""

from unittest import mock

from django.db import OperationalError
from django.test import Client, SimpleTestCase


class HealthEndpointTests(SimpleTestCase):
    databases = {'default'}

    url = '/api/health/'

    def test_a_healthy_deployment_answers_200(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {'status': 'ok', 'database': 'ok', 'migrations': 'applied'},
        )

    def test_it_runs_a_real_query_rather_than_inspecting_the_connection(self):
        """The distinction that makes this endpoint worth having.

        EMAXCONNSESSION is the pooler refusing to hand out a backend. The
        connection object still exists, so anything short of executing a
        statement reports health throughout the outage.
        """
        with mock.patch('caughtondash.health.connection') as fake_connection:
            cursor = fake_connection.cursor.return_value.__enter__.return_value

            self.client.get(self.url)

            cursor.execute.assert_called_once_with('SELECT 1')

    def test_an_unreachable_database_is_a_503(self):
        # 503 and not 200-with-a-sad-field: uptime monitors act on status
        # codes, so a failure expressed only in JSON is a failure nobody hears.
        with mock.patch('caughtondash.health.connection') as fake_connection:
            fake_connection.cursor.side_effect = OperationalError(
                'connection failed: FATAL: MaxClientsInSessionMode: EMAXCONNSESSION'
            )

            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 503)
        body = response.json()
        self.assertEqual(body['status'], 'error')
        self.assertEqual(body['database'], 'error')
        self.assertEqual(body['migrations'], 'unknown')
        # The pooler's error code is most of the diagnosis; keep it reportable.
        self.assertIn('EMAXCONNSESSION', body['detail'])

    def test_unapplied_migrations_are_a_503_and_are_named(self):
        """This exact condition took production down for a day.

        Code shipped needing migrations nobody had run, and every video
        endpoint returned 500. If it only appeared as a field, no monitor would
        act on it, so it fails the check.
        """
        with mock.patch(
            'caughtondash.health._pending_migrations',
            return_value=['videos.0009_something'],
        ):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 503)
        body = response.json()
        self.assertEqual(body['status'], 'error')
        self.assertEqual(body['database'], 'ok', 'the database answered; only the schema is behind')
        self.assertEqual(body['migrations'], 'pending')
        self.assertEqual(body['pending'], ['videos.0009_something'])

    def test_a_broken_migration_graph_does_not_masquerade_as_an_outage(self):
        with mock.patch(
            'caughtondash.health._pending_migrations',
            side_effect=RuntimeError('inconsistent migration history'),
        ):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 503)
        body = response.json()
        self.assertEqual(body['database'], 'ok')
        self.assertEqual(body['migrations'], 'unknown')

    def test_it_is_unauthenticated(self):
        # A monitor holds no credentials, and an endpoint that returns no data
        # has nothing to protect.
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_it_is_not_cached(self):
        response = self.client.get(self.url)

        self.assertIn('no-cache', response['Cache-Control'])

    def test_other_methods_are_refused(self):
        # enforce_csrf_checks mirrors production, where the middleware would
        # otherwise answer 403 before require_GET is reached. Without it this
        # test asserts a status code the deployment never returns.
        client = Client(enforce_csrf_checks=True)

        self.assertEqual(client.post(self.url).status_code, 405)
