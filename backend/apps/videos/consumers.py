"""WebSocket consumer publishing analysis state as it changes.

The site previously never refreshed itself: the feed loaded on mount and after
your own actions, so watching a video get analyzed meant reloading the page.
This pushes each state change instead.

Everything here is best-effort. A browser that cannot open a WebSocket, or a
deployment still served by WSGI where the endpoint does not exist, falls back
to the behaviour the site has always had -- stale until reloaded. Analysis
itself never depends on this.
"""

from __future__ import annotations

import json
import uuid

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.layers import get_channel_layer

# One group for everything. The feed shows every video, so per-video groups
# would mean the client joining and leaving constantly as it scrolls, for no
# saving at this scale.
ANALYSIS_GROUP = 'analysis-status'


class AnalysisStatusConsumer(AsyncWebsocketConsumer):
    """Read-only stream of analysis state.

    Deliberately accepts anonymous connections and ignores anything the client
    sends. The payload is a subset of what the public feed already returns, so
    it grants no access the feed does not, and treating the socket as
    write-only removes a whole category of question about what a client may ask
    for.
    """

    async def connect(self):
        await self.channel_layer.group_add(ANALYSIS_GROUP, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(ANALYSIS_GROUP, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        # Nothing a client sends can affect anything. Ignore rather than error,
        # so a stray keepalive from some proxy does not drop the connection.
        return

    async def analysis_update(self, event):
        await self.send(text_data=json.dumps(event['payload']))


class WorkerHeartbeatConsumer(AsyncWebsocketConsumer):
    """The worker's liveness and progress channel.

    Unlike the browser stream this one is authenticated and accepts writes, so
    it is a separate consumer rather than a mode of the other -- there is no
    configuration under which a browser reaches this endpoint.

    Authentication is the same bearer token the HTTP worker API uses, read from
    the Authorization header. Browsers cannot set headers on a WebSocket, but
    the desktop worker is a native client that can, so the token stays out of
    the URL and therefore out of access logs and proxy history.

    Holding the connection open is itself the liveness signal: a worker that
    dies is known when its socket closes, rather than after the stale window
    expires. What a close does *not* do is free the job -- see
    STALE_PROCESSING_MINUTES for why.
    """

    async def connect(self):
        if not await self._authorized():
            # 4401: application-level unauthorised. The worker logs it and
            # falls back to HTTP rather than retrying a socket that will keep
            # refusing it.
            await self.close(code=4401)
            return

        self.worker_id = ''
        await self.accept()

    async def disconnect(self, close_code):
        if getattr(self, 'worker_id', ''):
            await self._mark_offline(self.worker_id)

    async def receive(self, text_data=None, bytes_data=None):
        try:
            message = json.loads(text_data or '')
        except (TypeError, ValueError):
            return  # Ignore junk rather than dropping a working connection.

        if not isinstance(message, dict):
            return

        if message.get('type') != 'heartbeat':
            # Only heartbeats for now. Claiming and result submission stay on
            # HTTP, where they are already transactional and retryable.
            return

        worker_id = str(message.get('worker_id') or '').strip()
        if not worker_id:
            return
        self.worker_id = worker_id

        await self._record(
            worker_id=worker_id,
            status=str(message.get('status') or 'idle'),
            job_id=message.get('job_id') or None,
            stage=str(message.get('stage') or ''),
            progress=message.get('progress') or 0,
        )

        # Acknowledge, so the worker can tell a delivered heartbeat from one
        # written into a socket that is open but going nowhere. Silence on a
        # heartbeat is what hid the backend rejecting every worker POST before.
        await self.send(text_data=json.dumps({'type': 'heartbeat_ack'}))

    async def _authorized(self) -> bool:
        header = ''
        for name, value in self.scope.get('headers', []):
            if name == b'authorization':
                header = value.decode('latin-1')
                break

        if not header.startswith('Bearer '):
            return False

        return await self._valid_token(header[7:])

    @database_sync_to_async
    def _valid_token(self, token: str) -> bool:
        from apps.videos.worker_auth import is_valid_worker_token

        try:
            return is_valid_worker_token(token)
        except ValueError:
            # WORKER_API_TOKEN unset. Refuse rather than fall open.
            return False

    @database_sync_to_async
    def _record(self, worker_id, status, job_id, stage, progress):
        from apps.videos.worker_services import apply_worker_heartbeat

        try:
            progress = int(progress)
        except (TypeError, ValueError):
            progress = 0

        if job_id:
            try:
                job_id = uuid.UUID(str(job_id))
            except (TypeError, ValueError):
                job_id = None

        apply_worker_heartbeat(worker_id, status, job_id, stage, progress)

    @database_sync_to_async
    def _mark_offline(self, worker_id):
        from apps.videos.models import Worker

        # Status only. last_seen_at is deliberately left where it was: it is
        # what the stale check reads, and moving it on disconnect would either
        # forgive a dead worker or condemn a reconnecting one.
        Worker.objects.filter(id=worker_id).update(status='offline')


def publish_analysis_state(video) -> None:
    """Broadcast a video's analysis state to connected browsers.

    Called from ordinary synchronous view and service code, so it bridges into
    the channel layer. Swallows everything: this is a notification, and a
    failure to notify must never fail the job that triggered it -- which is the
    same rule the progress reporting follows.
    """
    payload = {
        'type': 'analysis',
        'video_id': str(video.id),
        'approval_status': video.approval_status,
        'analysis_status': video.analysis_status,
        'analysis_stage': video.analysis_stage,
        'analysis_progress': video.analysis_progress,
        'ai_summary': video.ai_summary,
        'ai_tags': video.ai_tags,
        'tags': video.tags,
        'duration_seconds': video.duration_seconds,
        'thumbnail_url': video.thumbnail_url,
    }

    try:
        channel_layer = get_channel_layer()
        if channel_layer is None:
            return

        async_to_sync(channel_layer.group_send)(
            ANALYSIS_GROUP,
            {'type': 'analysis.update', 'payload': payload},
        )
    except Exception:
        # No channel layer configured, no event loop, or the layer is down.
        # None of that should interrupt an analysis.
        return
