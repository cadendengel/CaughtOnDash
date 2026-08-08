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

from asgiref.sync import async_to_sync
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
