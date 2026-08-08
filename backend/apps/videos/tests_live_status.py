"""Live analysis status over WebSocket.

The site never refreshed itself, so watching a video get analyzed meant
reloading. State changes are now pushed.

Publishing is best-effort by design: a missing channel layer or a failing one
must never interrupt an analysis. That makes it exactly the kind of code that
rots unnoticed, so these pin both that it works and that it stays quiet when it
cannot.
"""

import json
from unittest import mock

from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from apps.videos.consumers import ANALYSIS_GROUP, publish_analysis_state
from apps.videos.models import Video
from caughtondash.asgi import application


def _video(**overrides):
    fields = dict(
        owner_clerk_user_id='user_owner',
        title='Clip',
        status='ready',
        approval_status='approved',
        analysis_status='pending',
        analysis_requested_at=timezone.now(),
    )
    fields.update(overrides)
    return Video.objects.create(**fields)


class PublishAnalysisStateTests(TestCase):
    def test_publishing_sends_the_video_state_to_the_group(self):
        video = _video(analysis_status='processing', analysis_progress=40)

        with mock.patch('apps.videos.consumers.get_channel_layer') as get_layer:
            layer = mock.MagicMock()
            get_layer.return_value = layer
            publish_analysis_state(video)

        self.assertTrue(layer.group_send.called)
        group, message = layer.group_send.call_args[0]
        self.assertEqual(group, ANALYSIS_GROUP)
        self.assertEqual(message['type'], 'analysis.update')

        payload = message['payload']
        self.assertEqual(payload['video_id'], str(video.id))
        self.assertEqual(payload['analysis_status'], 'processing')
        self.assertEqual(payload['analysis_progress'], 40)

    def test_a_missing_channel_layer_is_not_an_error(self):
        # Under WSGI, or before CHANNEL_LAYERS is configured, there is nothing
        # to publish to. Analysis must carry on regardless.
        video = _video()
        with mock.patch('apps.videos.consumers.get_channel_layer', return_value=None):
            publish_analysis_state(video)

    def test_a_failing_channel_layer_is_not_an_error(self):
        video = _video()
        with mock.patch('apps.videos.consumers.get_channel_layer') as get_layer:
            get_layer.side_effect = RuntimeError('layer is down')
            publish_analysis_state(video)

    def test_the_payload_carries_no_more_than_the_public_feed(self):
        # The socket is unauthenticated, so it must not expose anything the
        # feed does not already return to anonymous callers.
        video = _video(ai_summary='a summary', ai_tags=['car'])

        with mock.patch('apps.videos.consumers.get_channel_layer') as get_layer:
            layer = mock.MagicMock()
            get_layer.return_value = layer
            publish_analysis_state(video)

        payload = layer.group_send.call_args[0][1]['payload']
        allowed = {
            'type', 'video_id', 'approval_status', 'analysis_status',
            'analysis_stage', 'analysis_progress', 'ai_summary', 'ai_tags',
            'tags', 'duration_seconds', 'thumbnail_url',
        }
        self.assertEqual(set(payload), allowed)
        self.assertNotIn('owner_clerk_user_id', payload)


class PublishedStateIsFinalTests(TestCase):
    """What gets pushed must be what was saved, not a half-updated row.

    The first version published from inside complete_job before the tag merge
    and the save, so browsers were told a finished video had no tags. Caught by
    watching a real socket during a real job.
    """

    def test_completion_pushes_the_merged_tags(self):
        from apps.videos.worker_services import claim_job, complete_job

        video = _video(tags=[{'text': 'my commute', 'source': 'user'}])
        claim_job(video.id, 'worker-1', 'Worker One')

        with mock.patch('apps.videos.consumers.get_channel_layer') as get_layer:
            layer = mock.MagicMock()
            get_layer.return_value = layer
            complete_job(video.id, 'worker-1', 'a summary', ['car'], [],
                         {'duration_seconds': 12})

        # The last push is the completion; earlier ones are the claim.
        payload = layer.group_send.call_args_list[-1][0][1]['payload']

        self.assertEqual(payload['analysis_status'], 'complete')
        self.assertEqual(payload['ai_tags'], ['car'])
        self.assertEqual(
            [tag['text'] for tag in payload['tags']], ['my commute', 'car'])
        self.assertEqual(payload['duration_seconds'], 12)


class AnalysisSocketTests(TransactionTestCase):
    """End-to-end through the real consumer and channel layer."""

    async def _connect(self):
        communicator = WebsocketCommunicator(application, '/ws/analysis/')
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        return communicator

    async def test_a_browser_receives_state_changes(self):
        communicator = await self._connect()

        layer = get_channel_layer()
        await layer.group_send(ANALYSIS_GROUP, {
            'type': 'analysis.update',
            'payload': {'type': 'analysis', 'video_id': 'abc', 'analysis_status': 'complete'},
        })

        message = json.loads(await communicator.receive_from(timeout=2))
        self.assertEqual(message['video_id'], 'abc')
        self.assertEqual(message['analysis_status'], 'complete')

        await communicator.disconnect()

    async def test_anything_the_client_sends_is_ignored(self):
        # The stream is read-only. A stray keepalive from a proxy must not drop
        # the connection or raise.
        communicator = await self._connect()

        await communicator.send_to(text_data='{"please": "stop"}')
        await communicator.send_to(text_data='not even json')

        layer = get_channel_layer()
        await layer.group_send(ANALYSIS_GROUP, {
            'type': 'analysis.update',
            'payload': {'type': 'analysis', 'video_id': 'still-here'},
        })

        message = json.loads(await communicator.receive_from(timeout=2))
        self.assertEqual(message['video_id'], 'still-here')

        await communicator.disconnect()

    async def test_a_disconnected_browser_stops_receiving(self):
        communicator = await self._connect()
        await communicator.disconnect()

        layer = get_channel_layer()
        await layer.group_send(ANALYSIS_GROUP, {
            'type': 'analysis.update',
            'payload': {'type': 'analysis', 'video_id': 'nobody-listening'},
        })
        # Nothing to assert beyond it not raising: a send to an empty group is
        # a no-op, which is what keeps a closed tab from breaking a job.
