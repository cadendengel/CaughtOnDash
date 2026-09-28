"""Incident evidence: who can reach it, how it is stored, and the chain of custody."""

import hashlib
import json
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase, override_settings

from apps.accounts.models import AdminUser
from apps.incidents.crypto import generate_key
from apps.incidents.models import (
    EvidenceArtifact,
    EvidenceRecord,
    IncidentAccessLog,
    IncidentReport,
    OtherParty,
)
from apps.videos.models import Video
from apps.videos.worker_services import claim_job, complete_job

OWNER = 'user_owner'
STRANGER = 'user_stranger'
ADMIN = 'user_admin'
KEY = generate_key()


def _url(video):
    return f'/api/videos/{video.id}/incident/'


@override_settings(INCIDENT_ENCRYPTION_KEYS=[KEY])
class IncidentAccessTests(TestCase):
    def setUp(self):
        self.video = Video.objects.create(
            owner_clerk_user_id=OWNER, title='Clip', status='ready', visibility='public')

    def _patch(self, caller, body):
        return self.client.patch(
            _url(self.video), data=json.dumps(body), content_type='application/json',
            HTTP_X_CLERK_USER_ID=caller)

    def test_anonymous_is_refused(self):
        self.assertEqual(self.client.get(_url(self.video)).status_code, 401)

    def test_stranger_gets_404_even_on_a_public_video(self):
        # Public video, private evidence: visibility grants nothing here.
        response = self.client.get(_url(self.video), HTTP_X_CLERK_USER_ID=STRANGER)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self._patch(STRANGER, {'notes': 'x'}).status_code, 404)
        self.assertFalse(IncidentReport.objects.exists())

    def test_owner_reads_an_empty_report(self):
        response = self.client.get(_url(self.video), HTTP_X_CLERK_USER_ID=OWNER)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['kind'], 'incident-report')
        self.assertIsNone(body['report'])
        self.assertIsNone(body['other_party'])
        self.assertEqual(body['artifacts'], [])

    def test_owner_saves_and_partially_updates_the_other_party(self):
        self._patch(OWNER, {'notes': 'Hit on I-35', 'other_party': {
            'name': 'Jordan Example', 'vehicle_plate': 'UT S39 9CA'}})
        response = self._patch(OWNER, {'other_party': {'insurer': 'Acme Mutual'}})

        self.assertEqual(response.status_code, 200)
        other = response.json()['other_party']
        self.assertEqual(other['name'], 'Jordan Example')
        self.assertEqual(other['insurer'], 'Acme Mutual')
        self.assertEqual(response.json()['report']['notes'], 'Hit on I-35')

    def test_null_other_party_deletes_it(self):
        self._patch(OWNER, {'other_party': {'name': 'Jordan Example'}})
        response = self._patch(OWNER, {'other_party': None})
        self.assertIsNone(response.json()['other_party'])
        self.assertFalse(OtherParty.objects.exists())

    def test_unknown_fields_and_bad_bodies_are_rejected_without_writing(self):
        for body in ({'other_party': {'ssn': '1'}}, {'other_party': {'name': 5}},
                     {'notes': 'x' * 20001}, {}):
            with self.subTest(body=body):
                self.assertEqual(self._patch(OWNER, body).status_code, 400)
        self.assertFalse(IncidentReport.objects.exists())

    def test_admin_access_is_allowed_and_logged_as_admin(self):
        AdminUser.objects.create(clerk_user_id=ADMIN)
        self.assertEqual(self.client.get(_url(self.video), HTTP_X_CLERK_USER_ID=ADMIN).status_code, 200)
        self.client.get(_url(self.video), HTTP_X_CLERK_USER_ID=OWNER)

        log = list(IncidentAccessLog.objects.order_by('at').values_list('clerk_user_id', 'as_admin'))
        self.assertEqual(log, [(ADMIN, True), (OWNER, False)])

    def test_deleted_video_has_no_report(self):
        from django.utils import timezone
        self.video.deleted_at = timezone.now()
        self.video.save()
        self.assertEqual(self.client.get(_url(self.video), HTTP_X_CLERK_USER_ID=OWNER).status_code, 404)

    def test_nothing_private_reaches_the_public_video_payload(self):
        self._patch(OWNER, {'other_party': {'name': 'Jordan Example', 'vehicle_plate': 'UT S39 9CA'}})
        for url in (f'/api/videos/{self.video.id}/', '/api/feed/'):
            with self.subTest(url=url):
                text = self.client.get(url).content.decode()
                self.assertNotIn('Jordan Example', text)
                self.assertNotIn('S39 9CA', text)

    @patch('apps.incidents.views.signed_object_url', return_value='https://signed.example/f.jpg?token=t')
    def test_artifacts_are_served_by_signed_url(self, _sign):
        EvidenceArtifact.objects.create(
            video=self.video, kind='frame', storage_path=f'{self.video.id}/f.jpg', sha256='a' * 64, t_seconds=25.1)
        artifact = self.client.get(_url(self.video), HTTP_X_CLERK_USER_ID=OWNER).json()['artifacts'][0]
        self.assertEqual(artifact['url'], 'https://signed.example/f.jpg?token=t')
        self.assertEqual(artifact['t_seconds'], 25.1)


class EncryptionTests(TestCase):
    def setUp(self):
        self.video = Video.objects.create(owner_clerk_user_id=OWNER, status='ready')

    @override_settings(INCIDENT_ENCRYPTION_KEYS=[KEY])
    def test_values_are_ciphertext_in_the_database(self):
        report = IncidentReport.objects.create(video=self.video, notes='private note')
        OtherParty.objects.create(report=report, drivers_license='D1234567')

        with connection.cursor() as cursor:
            cursor.execute('SELECT drivers_license FROM incidents_otherparty')
            stored = cursor.fetchone()[0]
            cursor.execute('SELECT notes FROM incidents_incidentreport')
            stored_notes = cursor.fetchone()[0]

        self.assertNotIn('D1234567', stored)
        self.assertNotIn('private note', stored_notes)
        self.assertEqual(OtherParty.objects.get().drivers_license, 'D1234567')

    def test_rotated_keys_still_decrypt_old_rows(self):
        old_key, new_key = generate_key(), generate_key()
        with override_settings(INCIDENT_ENCRYPTION_KEYS=[old_key]):
            IncidentReport.objects.create(video=self.video, notes='before rotation')
        with override_settings(INCIDENT_ENCRYPTION_KEYS=[new_key, old_key]):
            self.assertEqual(IncidentReport.objects.get().notes, 'before rotation')

    @override_settings(INCIDENT_ENCRYPTION_KEYS=[])
    def test_no_key_refuses_to_store_rather_than_writing_plaintext(self):
        response = self.client.patch(
            _url(self.video), data=json.dumps({'other_party': {'name': 'Jordan Example'}}),
            content_type='application/json', HTTP_X_CLERK_USER_ID=OWNER)

        self.assertEqual(response.status_code, 503)
        self.assertFalse(OtherParty.objects.exists())

    @override_settings(INCIDENT_ENCRYPTION_KEYS=[])
    def test_no_key_still_serves_a_report_without_encrypted_values(self):
        # Blank fields are stored blank, so fingerprints stay readable.
        IncidentReport.objects.create(video=self.video)
        response = self.client.get(_url(self.video), HTTP_X_CLERK_USER_ID=OWNER)
        self.assertEqual(response.status_code, 200)


class ChainOfCustodyTests(TestCase):
    CONTENT = b'dashcam-bytes'
    DIGEST = hashlib.sha256(CONTENT).hexdigest()

    def setUp(self):
        self.video = Video.objects.create(owner_clerk_user_id=OWNER, status='pending')

    def _upload(self, content):
        with patch('apps.videos.views.upload_bytes_to_supabase', return_value='https://cdn.example.com/c.mp4'):
            return self.client.post('/api/videos/upload/', {
                'video_id': str(self.video.id),
                'file': SimpleUploadedFile('dash.mp4', content, content_type='video/mp4'),
            }, HTTP_X_CLERK_USER_ID=OWNER)

    def _complete(self, source_sha256):
        self.video.refresh_from_db()
        self.video.approval_status = 'approved'
        self.video.analysis_status = 'pending'
        self.video.save()
        claim_job(self.video.id, 'w1', 'Worker')
        return complete_job(self.video.id, 'w1', 'summary', [], [], {}, source_sha256=source_sha256)

    def test_upload_is_fingerprinted(self):
        self.assertEqual(self._upload(self.CONTENT).status_code, 200)
        record = EvidenceRecord.objects.get(video=self.video)
        self.assertEqual(record.sha256, self.DIGEST)
        self.assertEqual(record.size_bytes, len(self.CONTENT))
        self.assertEqual(record.custody_status, 'unverified')

    def test_failed_storage_records_nothing(self):
        with patch('apps.videos.views.upload_bytes_to_supabase', side_effect=RuntimeError('down')):
            self.client.post('/api/videos/upload/', {
                'video_id': str(self.video.id),
                'file': SimpleUploadedFile('dash.mp4', self.CONTENT, content_type='video/mp4'),
            }, HTTP_X_CLERK_USER_ID=OWNER)
        self.assertFalse(EvidenceRecord.objects.exists())

    def test_matching_worker_hash_verifies(self):
        self._upload(self.CONTENT)
        self.assertTrue(self._complete(self.DIGEST.upper())['success'])
        self.assertEqual(EvidenceRecord.objects.get().custody_status, 'verified')

    def test_different_worker_hash_is_a_mismatch_but_the_job_completes(self):
        self._upload(self.CONTENT)
        self.assertTrue(self._complete('0' * 64)['success'])
        self.assertEqual(EvidenceRecord.objects.get().custody_status, 'mismatch')

    def test_older_worker_without_a_hash_leaves_it_unverified(self):
        self._upload(self.CONTENT)
        self.assertTrue(self._complete('')['success'])
        self.assertEqual(EvidenceRecord.objects.get().custody_status, 'unverified')

    def test_replacing_the_file_keeps_the_old_fingerprint_and_resets_verification(self):
        self._upload(self.CONTENT)
        self._complete(self.DIGEST)
        self._upload(b'other-bytes')

        record = EvidenceRecord.objects.get()
        self.assertEqual(record.history[0]['sha256'], self.DIGEST)
        self.assertEqual(record.sha256, hashlib.sha256(b'other-bytes').hexdigest())
        self.assertEqual(record.custody_status, 'unverified')

    def test_worker_hash_goes_through_the_complete_endpoint(self):
        self._upload(self.CONTENT)
        self.video.refresh_from_db()
        self.video.approval_status = 'approved'
        self.video.analysis_status = 'pending'
        self.video.save()
        claim_job(self.video.id, 'w1', 'Worker')

        with patch.dict('os.environ', {'WORKER_API_TOKEN': 'token'}):
            response = self.client.post(
                f'/api/videos/worker/jobs/{self.video.id}/complete/',
                data=json.dumps({'worker_id': 'w1', 'summary': 's', 'source_sha256': self.DIGEST}),
                content_type='application/json', HTTP_AUTHORIZATION='Bearer token')

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(EvidenceRecord.objects.get().custody_status, 'verified')
        # And none of it leaked into the public metadata.
        self.video.refresh_from_db()
        self.assertNotIn(self.DIGEST, json.dumps(self.video.ai_metadata))
