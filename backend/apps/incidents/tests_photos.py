"""Owner photos: upload, EXIF, preview, and the worker's text coming back.

Photos are built in memory with the EXIF of the 2026-09-26 case photo, so no
real photo -- and no real plate or location -- is committed.
"""

import hashlib
import io
import json
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image
from PIL.TiffImagePlugin import IFDRational as Fraction   # the rational type Pillow writes

from apps.incidents import photos
from apps.incidents.crypto import generate_key
from apps.incidents.models import EvidenceArtifact
from apps.videos.models import Video
from apps.videos.worker_services import claim_job, open_analysis_run

OWNER, STRANGER, TOKEN = 'user_owner', 'user_stranger', 'worker-token'


def _jpeg(exif: bool = True, size=(64, 48), orientation: int | None = None) -> bytes:
    image = Image.new('RGB', size, (200, 180, 40))
    data = Image.Exif()
    if exif:
        data[271], data[272] = 'Apple', 'iPhone 17 Pro'
        data[0x8769] = {36867: '2026:09:26 12:19:32', 36881: '-05:00', 41989: 177,
                        42036: 'iPhone 17 Pro back triple camera 16.891mm f/2.8'}
        data[0x8825] = {
            1: 'N', 2: (Fraction(30), Fraction(16), Fraction(4446, 100)),
            3: 'W', 4: (Fraction(97), Fraction(35), Fraction(221, 100)),
            5: b'\x00', 6: Fraction(1539, 10), 12: 'K', 13: Fraction(359825, 10000),
            16: 'T', 17: Fraction(472016, 10000), 31: Fraction(2),
        }
    if orientation:
        data[0x0112] = orientation
    buffer = io.BytesIO()
    image.save(buffer, 'JPEG', exif=data.tobytes())
    return buffer.getvalue()


class ReadExifTests(TestCase):
    def test_the_case_photo_fields(self):
        metadata = photos.process(_jpeg())['metadata']
        self.assertEqual(metadata['taken_at'], '2026-09-26T12:19:32-05:00')
        self.assertEqual(metadata['device']['model'], 'iPhone 17 Pro')
        self.assertEqual(metadata['focal_length_35mm'], 177)
        gps = metadata['gps']
        self.assertAlmostEqual(gps['lat'], 30.2790167, places=6)
        self.assertAlmostEqual(gps['lon'], -97.5839472, places=6)
        self.assertEqual((gps['accuracy_m'], gps['heading_deg'], gps['speed_kmh']), (2.0, 47.2, 36.0))
        self.assertEqual(gps['heading_ref'], 'true')

    def test_a_photo_without_exif_still_processes(self):
        metadata = photos.process(_jpeg(exif=False))['metadata']
        self.assertNotIn('gps', metadata)
        self.assertEqual((metadata['width'], metadata['height']), (64, 48))

    def test_preview_is_upright_and_bounded(self):
        # Orientation 6: stored landscape, displayed portrait -- as phones do.
        processed = photos.process(_jpeg(size=(4000, 3000), orientation=6))
        self.assertEqual((processed['width'], processed['height']), (1536, 2048))
        self.assertTrue(processed['preview'].startswith(b'\xff\xd8\xff'))

    def test_sniffing_is_by_content(self):
        self.assertEqual(photos.sniff(_jpeg())[0], 'image/jpeg')
        self.assertEqual(photos.sniff(b'\x00\x00\x00\x18ftypheic' + b'\x00' * 20), ('image/heic', 'heic'))
        self.assertIsNone(photos.sniff(b'\x00\x00\x00\x18ftypisom' + b'\x00' * 20))   # an MP4
        self.assertIsNone(photos.sniff(b'<html>'))


@override_settings(INCIDENT_ENCRYPTION_KEYS=[generate_key()])
class PhotoUploadTests(TestCase):
    def setUp(self):
        self.video = Video.objects.create(owner_clerk_user_id=OWNER, status='ready', visibility='public')

    def _post(self, data, caller=OWNER, name='IMG_3110.jpg'):
        with patch('apps.incidents.views.upload_private_bytes') as store:
            response = self.client.post(
                f'/api/videos/{self.video.id}/incident/photos/',
                {'file': SimpleUploadedFile(name, data, content_type='image/jpeg')},
                HTTP_X_CLERK_USER_ID=caller)
        return response, store

    @patch('apps.incidents.views.signed_object_url', side_effect=lambda path, **kw: f'https://signed/{path}')
    def test_owner_uploads_a_photo(self, _sign):
        data = _jpeg()
        response, store = self._post(data)

        self.assertEqual(response.status_code, 201, response.content)
        artifact = EvidenceArtifact.objects.get()
        self.assertEqual(artifact.kind, 'photo')
        self.assertEqual(artifact.sha256, hashlib.sha256(data).hexdigest())
        self.assertEqual(artifact.metadata['exif']['gps']['heading_deg'], 47.2)
        # Original untouched, preview beside it, both private.
        (original_path, original, _), (preview_path, preview, preview_type) = [c.args for c in store.call_args_list]
        self.assertEqual(original, data)
        self.assertTrue(preview_path.endswith('.preview.jpg'))
        self.assertEqual(preview_type, 'image/jpeg')
        body = response.json()['artifact']
        self.assertEqual(body['url'], f'https://signed/{preview_path}')
        self.assertEqual(body['original_url'], f'https://signed/{original_path}')

    def test_strangers_and_non_photos_are_refused(self):
        response, store = self._post(_jpeg(), caller=STRANGER)
        self.assertEqual(response.status_code, 404)
        response, store = self._post(b'<html>not a photo</html>')
        self.assertEqual(response.status_code, 400)
        store.assert_not_called()
        self.assertFalse(EvidenceArtifact.objects.exists())

    def test_photo_details_stay_off_the_public_video(self):
        self._post(_jpeg())
        text = self.client.get(f'/api/videos/{self.video.id}/').content.decode()
        self.assertNotIn('97.58', text)
        self.assertNotIn('iPhone', text)

    def _post_with_preview(self, data, preview):
        with patch('apps.incidents.views.upload_private_bytes') as store, \
                patch('apps.incidents.photos.process', side_effect=AssertionError('decoded on the server')) as process:
            response = self.client.post(
                f'/api/videos/{self.video.id}/incident/photos/',
                {'file': SimpleUploadedFile('IMG_3110.jpg', data, content_type='image/jpeg'),
                 'preview': SimpleUploadedFile('preview.jpg', preview, content_type='image/jpeg')},
                HTTP_X_CLERK_USER_ID=OWNER)
        return response, store, process

    @patch('apps.incidents.views.signed_object_url', return_value='https://signed/x')
    def test_a_browser_preview_means_the_photo_is_never_decoded(self, _sign):
        # The point of the browser preview: a 24 MP photo decoded here peaked
        # at +165 MB on a 512 MB instance.
        original = _jpeg(size=(4000, 3000), orientation=6)
        preview = _jpeg(exif=False, size=(1536, 2048))
        response, store, process = self._post_with_preview(original, preview)

        self.assertEqual(response.status_code, 201, response.content)
        process.assert_not_called()
        (_, stored_original, _), (_, stored_preview, _) = [c.args for c in store.call_args_list]
        self.assertEqual((stored_original, stored_preview), (original, preview))
        artifact = EvidenceArtifact.objects.get()
        # EXIF still read, and the upright size from the orientation tag alone.
        self.assertEqual(artifact.metadata['exif']['gps']['heading_deg'], 47.2)
        self.assertEqual((artifact.width, artifact.height), (3000, 4000))

    @patch('apps.incidents.views.signed_object_url', return_value='https://signed/x')
    def test_an_unusable_preview_falls_back_to_the_server(self, _sign):
        for bad in (b'<html>', _jpeg(exif=False, size=(4000, 3000))):   # not a JPEG; too big
            with self.subTest(size=len(bad)):
                EvidenceArtifact.objects.all().delete()
                with patch('apps.incidents.views.upload_private_bytes'):
                    response = self.client.post(
                        f'/api/videos/{self.video.id}/incident/photos/',
                        {'file': SimpleUploadedFile('a.jpg', _jpeg(), content_type='image/jpeg'),
                         'preview': SimpleUploadedFile('p.jpg', bad, content_type='image/jpeg')},
                        HTTP_X_CLERK_USER_ID=OWNER)
                self.assertEqual(response.status_code, 201, response.content)
                self.assertEqual(EvidenceArtifact.objects.get().metadata['exif']['focal_length_35mm'], 177)

    def test_read_metadata_matches_the_full_decode(self):
        data = _jpeg(size=(400, 300), orientation=6)
        full = photos.process(data)['metadata']
        self.assertEqual(photos.read_metadata(data), full)


@override_settings(INCIDENT_ENCRYPTION_KEYS=[generate_key()])
class PhotoDeleteTests(TestCase):
    def setUp(self):
        self.video = Video.objects.create(owner_clerk_user_id=OWNER, status='ready')
        self.photo = EvidenceArtifact.objects.create(
            video=self.video, kind='photo', storage_path='v/p.heic', preview_path='v/p.preview.jpg', sha256='b' * 64)
        self.crop = EvidenceArtifact.objects.create(
            video=self.video, kind='plate_crop', storage_path='v/crop.jpg', sha256='c' * 64,
            metadata={'source_artifact_id': str(self.photo.id)})
        self.frame = EvidenceArtifact.objects.create(
            video=self.video, kind='frame', storage_path='v/frame.jpg', sha256='d' * 64)

    def _delete(self, artifact, caller=OWNER, fail=False):
        with patch('apps.incidents.views.delete_private_objects',
                   side_effect=RuntimeError('storage down') if fail else None) as remove:
            response = self.client.delete(
                f'/api/videos/{self.video.id}/incident/photos/{artifact.id}/', HTTP_X_CLERK_USER_ID=caller)
        return response, remove

    def test_owner_removes_a_photo_with_its_crops_and_it_is_logged(self):
        from apps.incidents.models import IncidentAccessLog

        response, remove = self._delete(self.photo)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(sorted(remove.call_args.args[0]), sorted(['v/p.heic', 'v/p.preview.jpg', 'v/crop.jpg']))
        self.assertEqual(list(EvidenceArtifact.objects.values_list('kind', flat=True)), ['frame'])
        self.assertTrue(IncidentAccessLog.objects.filter(action='delete', clerk_user_id=OWNER).exists())

    def test_the_analysis_evidence_cannot_be_deleted(self):
        response, remove = self._delete(self.frame)
        self.assertEqual(response.status_code, 404)
        remove.assert_not_called()

    def test_strangers_cannot_delete(self):
        response, remove = self._delete(self.photo, caller=STRANGER)
        self.assertEqual(response.status_code, 404)
        remove.assert_not_called()
        self.assertEqual(EvidenceArtifact.objects.count(), 3)

    def test_a_storage_failure_removes_nothing(self):
        response, _ = self._delete(self.photo, fail=True)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(EvidenceArtifact.objects.count(), 3)


@patch.dict('os.environ', {'WORKER_API_TOKEN': TOKEN})
@override_settings(INCIDENT_ENCRYPTION_KEYS=[generate_key()])
class PhotoWorkerTests(TestCase):
    def setUp(self):
        self.video = Video.objects.create(
            owner_clerk_user_id=OWNER, status='ready', approval_status='approved',
            analysis_status='pending', playback_url='https://cdn.example.com/c.mp4')
        open_analysis_run(self.video, OWNER)
        self.photo = EvidenceArtifact.objects.create(
            video=self.video, kind='photo', storage_path='p.heic', sha256='b' * 64, content_type='image/heic')
        self.auth = {'HTTP_AUTHORIZATION': f'Bearer {TOKEN}'}

    @patch('apps.storage.signed_object_url', return_value='https://signed/p.heic?t=1')
    def test_next_job_carries_the_photos(self, _sign):
        job = self.client.get('/api/videos/worker/jobs/next/', **self.auth).json()['job']
        self.assertEqual(job['photos'], [{'artifact_id': str(self.photo.id), 'url': 'https://signed/p.heic?t=1',
                                          'sha256': 'b' * 64, 'content_type': 'image/heic'}])

    def test_text_read_from_a_photo_is_stored_on_it_and_nowhere_else(self):
        other_video = Video.objects.create(owner_clerk_user_id='someone', status='ready')
        other_photo = EvidenceArtifact.objects.create(
            video=other_video, kind='photo', storage_path='o.jpg', sha256='c' * 64)
        claim_job(self.video.id, 'w1', 'Worker')
        text = {'identifiers': [{'kind': 'domain', 'value': 'barcotrucks.com'}],
                'plates': [{'text': 'S39 9CA', 'ambiguous': [4]}]}

        response = self.client.post(
            f'/api/videos/worker/jobs/{self.video.id}/complete/',
            data=json.dumps({'worker_id': 'w1', 'summary': 's', 'private': {'photo_text': {
                str(self.photo.id): text, str(other_photo.id): text, 'not-a-uuid': text}}}),
            content_type='application/json', **self.auth)

        self.assertEqual(response.status_code, 200, response.content)
        self.photo.refresh_from_db()
        other_photo.refresh_from_db()
        self.assertEqual(self.photo.metadata['text'], text)
        self.assertNotIn('text', other_photo.metadata)     # not this video's photo
        self.assertNotIn('S39', self.client.get(f'/api/videos/{self.video.id}/').content.decode())

    def test_crop_links_back_to_its_photo_only_within_the_video(self):
        claim_job(self.video.id, 'w1', 'Worker')
        other = EvidenceArtifact.objects.create(
            video=Video.objects.create(owner_clerk_user_id='x', status='ready'),
            kind='photo', storage_path='o.jpg', sha256='c' * 64)
        jpeg = _jpeg(exif=False)
        with patch('apps.incidents.worker_views.upload_private_bytes'):
            for source in (self.photo.id, other.id):
                self.client.post(f'/api/videos/worker/jobs/{self.video.id}/artifacts/', {
                    'worker_id': 'w1', 'kind': 'plate_crop', 'source_artifact_id': str(source),
                    'file': SimpleUploadedFile('plate.jpg', jpeg, content_type='image/jpeg')}, **self.auth)

        crops = EvidenceArtifact.objects.filter(kind='plate_crop').order_by('created_at')
        self.assertEqual([c.metadata for c in crops], [{'source_artifact_id': str(self.photo.id)}, {}])
