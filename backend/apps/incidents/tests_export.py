"""The evidence package: what is in it, that it can be checked, who can get it."""

import hashlib
import io
import json
import zipfile
from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.accounts.models import AdminUser
from apps.incidents.crypto import generate_key
from apps.incidents.models import EvidenceArtifact, EvidenceRecord, IncidentAccessLog, IncidentReport, OtherParty
from apps.videos.models import Video

OWNER, STRANGER, ADMIN = 'user_owner', 'user_stranger', 'user_admin'
SHEET = b'\xff\xd8\xff contact sheet'
PHOTO = b'\x00\x00\x00\x18ftypheic photo bytes'
STORE = {'v/sheet.jpg': SHEET, 'v/photo.heic': PHOTO}


@override_settings(INCIDENT_ENCRYPTION_KEYS=[generate_key()])
@patch('apps.incidents.views.signed_object_url', return_value='https://signed/x')
class ExportTests(TestCase):
    def setUp(self):
        self.video = Video.objects.create(owner_clerk_user_id=OWNER, title='I-35 cut-in', status='ready')
        EvidenceRecord.objects.create(
            video=self.video, sha256='a' * 64, original_filename='2026_0926_121621_2947.MOV',
            worker_sha256='a' * 64,
            provenance={'hints': [{'code': 'apple_export', 'message': 'Exported through an iPhone.'}]},
            moments={'available': True, 'possible': [], 'moments': [{
                't_seconds': 26.1, 'score': 1.0, 'reasons': ['sharp sound at 26.1s'],
                'overlay': {'clock': '2026-09-26T12:16:45', 'speed': 80, 'speed_unit': 'mph',
                            'lat': 30.228611, 'lon': -97.619722}}]},
            overlay={'available': True, 'clock': {'start': '2026-09-26T12:16:19'}})
        EvidenceArtifact.objects.create(
            video=self.video, kind='contact_sheet', storage_path='v/sheet.jpg',
            sha256=hashlib.sha256(SHEET).hexdigest())
        EvidenceArtifact.objects.create(
            video=self.video, kind='photo', storage_path='v/photo.heic', original_filename='IMG_3110.HEIC',
            sha256=hashlib.sha256(PHOTO).hexdigest(), metadata={
                'exif': {'taken_at': '2026-09-26T12:19:32-05:00', 'focal_length_35mm': 177,
                         'gps': {'lat': 30.2790167, 'lon': -97.5839472, 'accuracy_m': 2.0, 'heading_deg': 47.2}},
                'text': {'plates': [{'text': 'S39 SCA', 'reads': 11,
                                     'uncertain': [{'index': 3, 'read': 'S', 'could_be': ['5', '8', '9']}]}],
                         'identifiers': [{'kind': 'domain', 'value': 'cotrucks.com', 'may_be_truncated': True}]}})
        report = IncidentReport.objects.create(video=self.video, notes='Truck cut into my lane.')
        OtherParty.objects.create(report=report, insurer='Acme Mutual')

    def _export(self, caller=OWNER, store=STORE):
        with patch('apps.incidents.views.download_private_bytes', side_effect=lambda path: store[path]):
            return self.client.get(f'/api/videos/{self.video.id}/incident/export/', HTTP_X_CLERK_USER_ID=caller)

    def test_package_contents_and_checksums(self, _sign):
        response = self._export()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/zip')
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        names = set(archive.namelist())
        self.assertIn('report.md', names)
        self.assertIn('evidence.json', names)
        self.assertIn('SHA256SUMS.txt', names)
        self.assertIn(SHEET, [archive.read(n) for n in names])
        self.assertIn(PHOTO, [archive.read(n) for n in names])   # the original, untouched

        # Every file is listed with a sum that matches it, as `sha256sum -c` would check.
        sums = dict(reversed(line.split('  ', 1)) for line in archive.read('SHA256SUMS.txt').decode().splitlines())
        self.assertEqual(set(sums), names - {'SHA256SUMS.txt'})
        for name, digest in sums.items():
            self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(), digest, name)

        self.assertNotIn('https://signed', archive.read('evidence.json').decode())   # expiring URLs left out

    def test_report_reads_like_the_case_summary(self, _sign):
        report = zipfile.ZipFile(io.BytesIO(self._export().content)).read('report.md').decode()
        self.assertIn('Automated readings', report)
        self.assertIn('| 26.1s | 2026-09-26T12:16:45 | 80 mph | 30.228611N 97.619722W | 1.0 |', report)
        self.assertIn('Exported through an iPhone.', report)
        self.assertIn("`S39 SCA`", report)
        self.assertIn("position 4 read 'S', could be 5/8/9", report)
        self.assertIn('cotrucks.com (may be truncated)', report)
        self.assertIn('177 mm equivalent', report)
        self.assertIn('Insurer: Acme Mutual', report)
        self.assertIn('Truck cut into my lane.', report)

    def test_a_file_that_no_longer_matches_is_left_out_and_said_so(self, _sign):
        response = self._export(store={'v/sheet.jpg': b'\xff\xd8\xff tampered', 'v/photo.heic': PHOTO})
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        self.assertNotIn(b'\xff\xd8\xff tampered', [archive.read(n) for n in archive.namelist()])
        self.assertIn('## Not included', archive.read('report.md').decode())

    def test_only_owner_and_admin_and_every_export_is_logged(self, _sign):
        self.assertEqual(self._export(caller=STRANGER).status_code, 404)
        self.assertEqual(self.client.get(f'/api/videos/{self.video.id}/incident/export/').status_code, 401)
        AdminUser.objects.create(clerk_user_id=ADMIN)
        self._export(caller=ADMIN)
        self._export()
        log = list(IncidentAccessLog.objects.filter(action='export').order_by('at')
                   .values_list('clerk_user_id', 'as_admin'))
        self.assertEqual(log, [(ADMIN, True), (OWNER, False)])
