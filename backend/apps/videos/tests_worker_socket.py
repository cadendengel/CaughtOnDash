"""The worker's heartbeat over WebSocket.

The connection itself is the liveness signal, so a worker that dies is known
when its socket closes rather than after the stale window expires. Two things
have to stay true for that to be safe, and both are pinned here: the endpoint
must be authenticated, because unlike the browser stream it accepts writes; and
a heartbeat must never resurrect a job that has already finished.
"""

import json
import os
from unittest import mock

from channels.testing import WebsocketCommunicator
from django.test import TransactionTestCase
from django.utils import timezone

from apps.videos.consumers import ANALYSIS_GROUP  # noqa: F401  (documents the pairing)
from apps.videos.models import Video, Worker
from caughtondash.asgi import application

TOKEN = 'test-worker-token'


def _auth(token=TOKEN):
    return [(b'authorization', f'Bearer {token}'.encode())]


class WorkerSocketTestCase(TransactionTestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {'WORKER_API_TOKEN': TOKEN})
        patcher.start()
        self.addCleanup(patcher.stop)

    async def _connect(self, headers=None):
        communicator = WebsocketCommunicator(
            application, '/ws/worker/', headers=headers if headers is not None else _auth())
        connected, subprotocol = await communicator.connect()
        return communicator, connected, subprotocol


class WorkerSocketAuthTests(WorkerSocketTestCase):
    async def test_a_valid_token_connects(self):
        communicator, connected, _ = await self._connect()
        self.assertTrue(connected)
        await communicator.disconnect()

    async def test_no_token_is_refused(self):
        communicator, connected, code = await self._connect(headers=[])
        self.assertFalse(connected)
        self.assertEqual(code, 4401)
        await communicator.disconnect()

    async def test_a_wrong_token_is_refused(self):
        communicator, connected, code = await self._connect(_auth('not-the-token'))
        self.assertFalse(connected)
        self.assertEqual(code, 4401)
        await communicator.disconnect()

    async def test_a_missing_server_token_refuses_rather_than_falls_open(self):
        # If WORKER_API_TOKEN is unset the HTTP layer raises. The socket must
        # deny, not accept everyone.
        with mock.patch.dict(os.environ, {}, clear=True):
            communicator, connected, code = await self._connect()
        self.assertFalse(connected)
        self.assertEqual(code, 4401)
        await communicator.disconnect()


class WorkerHeartbeatTests(WorkerSocketTestCase):
    def _video(self, **overrides):
        fields = dict(
            owner_clerk_user_id='user_owner',
            title='Clip',
            status='ready',
            approval_status='approved',
            analysis_status='processing',
            analysis_stage='claimed',
            worker_id='worker-1',
            worker_claimed_at=timezone.now(),
            worker_last_seen_at=timezone.now(),
            analysis_requested_at=timezone.now(),
        )
        fields.update(overrides)
        return Video.objects.create(**fields)

    async def test_a_heartbeat_records_liveness_and_progress(self):
        from channels.db import database_sync_to_async

        video = await database_sync_to_async(self._video)()

        communicator, connected, _ = await self._connect()
        self.assertTrue(connected)

        await communicator.send_to(text_data=json.dumps({
            'type': 'heartbeat',
            'worker_id': 'worker-1',
            'status': 'processing',
            'job_id': str(video.id),
            'stage': 'analyzing',
            'progress': 70,
        }))

        ack = json.loads(await communicator.receive_from(timeout=2))
        self.assertEqual(ack['type'], 'heartbeat_ack')

        refreshed = await database_sync_to_async(Video.objects.get)(id=video.id)
        self.assertEqual(refreshed.analysis_progress, 70)
        self.assertEqual(refreshed.analysis_stage, 'analyzing')

        worker = await database_sync_to_async(Worker.objects.get)(id='worker-1')
        self.assertEqual(worker.status, 'processing')

        await communicator.disconnect()

    async def test_a_heartbeat_reaches_watching_browsers(self):
        # The point of moving the heartbeat onto the socket: progress arrives at
        # the site over the same hop instead of only on the HTTP progress calls.
        from channels.db import database_sync_to_async

        video = await database_sync_to_async(self._video)()

        browser = WebsocketCommunicator(application, '/ws/analysis/')
        await browser.connect()

        worker_socket, _, _ = await self._connect()
        await worker_socket.send_to(text_data=json.dumps({
            'type': 'heartbeat',
            'worker_id': 'worker-1',
            'status': 'processing',
            'job_id': str(video.id),
            'stage': 'analyzing',
            'progress': 55,
        }))

        pushed = json.loads(await browser.receive_from(timeout=2))
        self.assertEqual(pushed['video_id'], str(video.id))
        self.assertEqual(pushed['analysis_progress'], 55)

        await worker_socket.disconnect()
        await browser.disconnect()

    async def test_a_late_heartbeat_cannot_revert_a_finished_job(self):
        # The production bug this guards: a heartbeat in flight when the job
        # completed wrote its stale snapshot back and the site showed
        # "Processing: 100% (complete)". The socket path must be scoped the
        # same way the HTTP one is.
        from channels.db import database_sync_to_async

        video = await database_sync_to_async(self._video)(
            analysis_status='complete', analysis_stage='complete', analysis_progress=100)

        communicator, _, _ = await self._connect()
        await communicator.send_to(text_data=json.dumps({
            'type': 'heartbeat',
            'worker_id': 'worker-1',
            'status': 'processing',
            'job_id': str(video.id),
            'stage': 'analyzing',
            'progress': 70,
        }))
        await communicator.receive_from(timeout=2)

        refreshed = await database_sync_to_async(Video.objects.get)(id=video.id)
        self.assertEqual(refreshed.analysis_status, 'complete')
        self.assertEqual(refreshed.analysis_progress, 100)
        self.assertEqual(refreshed.analysis_stage, 'complete')

        await communicator.disconnect()

    async def test_a_heartbeat_for_another_workers_job_is_ignored(self):
        from channels.db import database_sync_to_async

        video = await database_sync_to_async(self._video)(analysis_progress=10)

        communicator, _, _ = await self._connect()
        await communicator.send_to(text_data=json.dumps({
            'type': 'heartbeat',
            'worker_id': 'worker-2',
            'status': 'processing',
            'job_id': str(video.id),
            'progress': 99,
        }))
        await communicator.receive_from(timeout=2)

        refreshed = await database_sync_to_async(Video.objects.get)(id=video.id)
        self.assertEqual(refreshed.analysis_progress, 10)

        await communicator.disconnect()

    async def test_junk_does_not_drop_the_connection(self):
        communicator, _, _ = await self._connect()

        await communicator.send_to(text_data='not json')
        await communicator.send_to(text_data='[]')
        await communicator.send_to(text_data=json.dumps({'type': 'something-else'}))
        await communicator.send_to(text_data=json.dumps({'type': 'heartbeat'}))  # no worker_id

        await communicator.send_to(text_data=json.dumps({
            'type': 'heartbeat', 'worker_id': 'worker-1', 'status': 'idle',
        }))
        ack = json.loads(await communicator.receive_from(timeout=2))
        self.assertEqual(ack['type'], 'heartbeat_ack')

        await communicator.disconnect()

    async def test_disconnecting_marks_the_worker_offline_without_forgiving_it(self):
        from channels.db import database_sync_to_async

        communicator, _, _ = await self._connect()
        await communicator.send_to(text_data=json.dumps({
            'type': 'heartbeat', 'worker_id': 'worker-1', 'status': 'idle',
        }))
        await communicator.receive_from(timeout=2)

        before = await database_sync_to_async(Worker.objects.get)(id='worker-1')
        seen_before = before.last_seen_at

        await communicator.disconnect()

        after = await database_sync_to_async(Worker.objects.get)(id='worker-1')
        self.assertEqual(after.status, 'offline')
        # last_seen_at is what the stale check reads. Moving it on disconnect
        # would either forgive a dead worker or condemn a reconnecting one.
        self.assertEqual(after.last_seen_at, seen_before)


class StaleWindowTests(TransactionTestCase):
    def test_one_constant_governs_every_stale_check(self):
        # Three places previously disagreed -- two minutes in claim_job and
        # reset_stale_jobs, five in _is_stale_processing -- so a job could be
        # reclaimable by one path and not another.
        import inspect

        from apps.videos import worker_services

        source = inspect.getsource(worker_services)
        self.assertEqual(worker_services.STALE_PROCESSING_MINUTES, 2)
        self.assertNotIn('minutes=2)', source)
        self.assertNotIn('timeout_minutes: int = 2', source)
