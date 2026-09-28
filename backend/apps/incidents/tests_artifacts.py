"""The worker's private channel: evidence images and file provenance."""

import hashlib
import json
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from apps.incidents.crypto import generate_key
from apps.incidents.models import EvidenceArtifact, EvidenceRecord
from apps.videos.models import Video
from apps.videos.worker_services import claim_job, open_analysis_run

TOKEN = 'worker-token'
JPEG = b'\xff\xd8\xff\xe0' + b'jpeg-body' * 10
PROVENANCE = {
    'available': True,
    'location': {'latitude': 30.279, 'longitude': -97.5839},
    'hints': [{'code': 'apple_export', 'message': 'exported'}],
}


@patch.dict('os.environ', {'WORKER_API_TOKEN': TOKEN})
@override_settings(INCIDENT_ENCRYPTION_KEYS=[generate_key()])
class ArtifactUploadTests(TestCase):
    def setUp(self):
        self.video = Video.objects.create(
            owner_clerk_user_id='user_owner', status='ready', approval_status='approved',
            analysis_status='pending', playback_url='https://cdn.example.com/c.mp4')
        self.run = open_analysis_run(self.video, 'user_owner')
        claim_job(self.video.id, 'w1', 'Worker')
        self.auth = {'HTTP_AUTHORIZATION': f'Bearer {TOKEN}'}

    def _upload(self, data=JPEG, **fields):
        form = {'worker_id': 'w1', 'kind': 'contact_sheet', 'label': '24 frames',
                'width': '1920', 'height': '720', 'analyzer_version': 'detect-4.1',
                'sha256': hashlib.sha256(data).hexdigest()}
        form.update(fields)
        form['file'] = SimpleUploadedFile('contact_sheet.jpg', data, content_type='image/jpeg')
        with patch('apps.incidents.worker_views.upload_private_bytes') as store:
            response = self.client.post(f'/api/videos/worker/jobs/{self.video.id}/artifacts/', form, **self.auth)
        return response, store

    def test_image_is_stored_privately_under_its_run(self):
        response, store = self._upload()

        self.assertEqual(response.status_code, 200, response.content)
        artifact = EvidenceArtifact.objects.get()
        self.assertEqual(artifact.run, self.run)
        self.assertEqual(artifact.sha256, hashlib.sha256(JPEG).hexdigest())
        self.assertEqual((artifact.width, artifact.height), (1920, 720))
        path, data, content_type = store.call_args.args
        self.assertTrue(path.startswith(f'{self.video.id}/{self.run.id}/'))
        self.assertEqual((data, content_type), (JPEG, 'image/jpeg'))

    def test_needs_the_worker_token(self):
        self.auth = {}
        response, store = self._upload()
        self.assertEqual(response.status_code, 401)
        store.assert_not_called()

    def test_refused_unless_this_worker_is_processing_the_job(self):
        response, store = self._upload(worker_id='someone-else')
        self.assertEqual(response.status_code, 400)

        Video.objects.filter(id=self.video.id).update(analysis_status='complete')
        response, store = self._upload()
        self.assertEqual(response.status_code, 400)
        store.assert_not_called()
        self.assertFalse(EvidenceArtifact.objects.exists())

    def test_hash_mismatch_is_refused(self):
        response, store = self._upload(sha256='0' * 64)
        self.assertEqual(response.status_code, 400)
        store.assert_not_called()

    def test_only_real_images_are_accepted(self):
        # Judged by content: an HTML file named .jpg must not reach a bucket
        # whose contents are shown in a browser.
        response, store = self._upload(data=b'<html><script>alert(1)</script></html>')
        self.assertEqual(response.status_code, 400)
        store.assert_not_called()

    def test_unknown_kind_is_refused(self):
        response, _ = self._upload(kind='face_crop')
        self.assertEqual(response.status_code, 400)

    def test_storage_failure_records_nothing(self):
        with patch('apps.incidents.worker_views.upload_private_bytes', side_effect=RuntimeError('down')):
            response = self.client.post(
                f'/api/videos/worker/jobs/{self.video.id}/artifacts/',
                {'worker_id': 'w1', 'kind': 'frame',
                 'file': SimpleUploadedFile('f.jpg', JPEG, content_type='image/jpeg')}, **self.auth)
        self.assertEqual(response.status_code, 502)
        self.assertFalse(EvidenceArtifact.objects.exists())

    def test_provenance_is_stored_privately_on_complete(self):
        response = self.client.post(
            f'/api/videos/worker/jobs/{self.video.id}/complete/',
            data=json.dumps({'worker_id': 'w1', 'summary': 's',
                             'metadata': {'analyzer_version': 'detect-4.1'},
                             'private': {'provenance': PROVENANCE, 'junk': 'x' * 10}}),
            content_type='application/json', **self.auth)

        self.assertEqual(response.status_code, 200, response.content)
        record = EvidenceRecord.objects.get(video=self.video)
        self.assertEqual(record.provenance, PROVENANCE)
        self.assertIsNotNone(record.provenance_at)

        # GPS must reach neither the public video nor the feed.
        for url in (f'/api/videos/{self.video.id}/', '/api/feed/'):
            with self.subTest(url=url):
                self.assertNotIn('97.5839', self.client.get(url).content.decode())

        # The owner sees it in the report.
        report = self.client.get(f'/api/videos/{self.video.id}/incident/', HTTP_X_CLERK_USER_ID='user_owner').json()
        self.assertEqual(report['evidence']['provenance']['location']['longitude'], -97.5839)

    def test_old_video_without_upload_fingerprint_still_records_the_worker_hash(self):
        self.client.post(
            f'/api/videos/worker/jobs/{self.video.id}/complete/',
            data=json.dumps({'worker_id': 'w1', 'summary': 's', 'source_sha256': 'a' * 64}),
            content_type='application/json', **self.auth)
        record = EvidenceRecord.objects.get(video=self.video)
        self.assertEqual(record.worker_sha256, 'a' * 64)
        self.assertEqual(record.custody_status, 'unverified')

    def test_crop_bounding_box_is_kept_and_bad_ones_dropped(self):
        self._upload(kind='vehicle_crop', bbox='[1480, 310, 420, 390]', t_seconds='25.7')
        self._upload(kind='vehicle_crop', bbox='[1, 2, "x", 4]')
        boxes = list(EvidenceArtifact.objects.order_by('created_at').values_list('bbox', 't_seconds'))
        self.assertEqual(boxes, [([1480, 310, 420, 390], 25.7), (None, None)])

    def test_moments_are_stored_privately_on_complete(self):
        moments = {'available': True, 'moments': [{'t_seconds': 26.1, 'score': 1.0,
                                                   'reasons': ['sharp sound at 26.1s']}], 'possible': []}
        self.client.post(
            f'/api/videos/worker/jobs/{self.video.id}/complete/',
            data=json.dumps({'worker_id': 'w1', 'summary': 's', 'private': {'moments': moments}}),
            content_type='application/json', **self.auth)

        record = EvidenceRecord.objects.get(video=self.video)
        self.assertEqual(record.moments, moments)
        self.assertEqual(record.provenance, {})  # a key that was not sent is left alone
        self.assertNotIn('sharp sound', self.client.get(f'/api/videos/{self.video.id}/').content.decode())

    @patch('apps.incidents.views.signed_object_url', return_value='https://signed.example/x')
    def test_report_lists_images_with_their_attempt(self, _sign):
        self._upload()
        artifact = self.client.get(
            f'/api/videos/{self.video.id}/incident/', HTTP_X_CLERK_USER_ID='user_owner').json()['artifacts'][0]
        self.assertEqual(artifact['kind'], 'contact_sheet')
        self.assertEqual(artifact['attempt_number'], self.run.attempt_number)
        self.assertEqual(artifact['url'], 'https://signed.example/x')
