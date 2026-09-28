"""Private incident evidence, kept apart from the public video record.

Nothing here is reachable through Video.to_dict, the feed, or the public
storage bucket. Video's ai_* fields are served to anyone who can see the video,
so anything identifying -- a plate, a location, another driver's details --
lives in these tables instead and is read only through the incident endpoint,
which admits the video's owner and admins and logs every access.
"""

import uuid

from django.db import models

from apps.incidents.crypto import EncryptedTextField
from apps.videos.models import AnalysisRun, Video


class EvidenceRecord(models.Model):
    """What was uploaded, fingerprinted as it arrived, and whether the worker
    analyzed those same bytes.

    One per video, created by the upload itself rather than on request: a
    fingerprint taken later proves nothing about what arrived.
    """

    CUSTODY_UNVERIFIED = 'unverified'
    CUSTODY_VERIFIED = 'verified'
    CUSTODY_MISMATCH = 'mismatch'

    video = models.OneToOneField(Video, on_delete=models.CASCADE, related_name='evidence_record')
    # Blank only for videos uploaded before fingerprinting existed: their
    # record is created by the first worker check, with nothing to compare to.
    sha256 = models.CharField(max_length=64, blank=True, default='', help_text='SHA-256 of the bytes received at upload')
    size_bytes = models.BigIntegerField(default=0)
    original_filename = models.CharField(max_length=255, blank=True, default='')
    content_type = models.CharField(max_length=255, blank=True, default='')
    uploaded_by = models.CharField(max_length=255, blank=True, default='')
    uploaded_at = models.DateTimeField(null=True, blank=True)
    # Earlier fingerprints, if the file was ever replaced. The upload endpoint
    # accepts a second file for the same video, so a replacement is recorded
    # rather than silently overwriting the evidence it replaced.
    history = models.JSONField(default=list, blank=True)

    worker_sha256 = models.CharField(max_length=64, blank=True, default='')
    worker_id = models.CharField(max_length=255, blank=True, default='')
    worker_checked_at = models.DateTimeField(null=True, blank=True)

    # Where the file came from, read by ffprobe on the worker: container tags,
    # codecs, and hints such as "this is an iPhone export, not the original".
    # Here rather than in ai_metadata because it can include the uploader's GPS.
    provenance = models.JSONField(default=dict, blank=True)
    provenance_at = models.DateTimeField(null=True, blank=True)

    @property
    def custody_status(self) -> str:
        if not self.worker_sha256 or not self.sha256:
            return self.CUSTODY_UNVERIFIED
        if self.worker_sha256 == self.sha256:
            return self.CUSTODY_VERIFIED
        return self.CUSTODY_MISMATCH

    def to_dict(self) -> dict:
        return {
            'sha256': self.sha256,
            'size_bytes': self.size_bytes,
            'original_filename': self.original_filename,
            'content_type': self.content_type,
            'uploaded_at': self.uploaded_at.isoformat() if self.uploaded_at else None,
            'history': self.history,
            'custody_status': self.custody_status,
            'worker_sha256': self.worker_sha256,
            'worker_id': self.worker_id,
            'worker_checked_at': self.worker_checked_at.isoformat() if self.worker_checked_at else None,
            'provenance': self.provenance,
            'provenance_at': self.provenance_at.isoformat() if self.provenance_at else None,
        }


class IncidentReport(models.Model):
    """The owner's case file for a video. Created when they first save to it."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    video = models.OneToOneField(Video, on_delete=models.CASCADE, related_name='incident_report')
    notes = EncryptedTextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def to_dict(self) -> dict:
        return {
            'id': str(self.id),
            'notes': self.notes,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
        }


class OtherParty(models.Model):
    """Details of the other driver or vehicle, as the owner obtained them --
    exchanged at the scene, or from the police report or an insurer.

    Every field is encrypted. The owner types these in; nothing here is looked
    up automatically.
    """

    FIELDS = (
        'name', 'phone', 'email', 'address', 'drivers_license',
        'vehicle_plate', 'vehicle_description', 'insurer', 'policy_number',
        'claim_number', 'police_report_number', 'notes',
    )
    MAX_LENGTH = 2000

    report = models.OneToOneField(IncidentReport, on_delete=models.CASCADE, related_name='other_party')
    name = EncryptedTextField(blank=True, default='')
    phone = EncryptedTextField(blank=True, default='')
    email = EncryptedTextField(blank=True, default='')
    address = EncryptedTextField(blank=True, default='')
    drivers_license = EncryptedTextField(blank=True, default='')
    vehicle_plate = EncryptedTextField(blank=True, default='')
    vehicle_description = EncryptedTextField(blank=True, default='')
    insurer = EncryptedTextField(blank=True, default='')
    policy_number = EncryptedTextField(blank=True, default='')
    claim_number = EncryptedTextField(blank=True, default='')
    police_report_number = EncryptedTextField(blank=True, default='')
    notes = EncryptedTextField(blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)

    def to_dict(self) -> dict:
        data = {field: getattr(self, field) for field in self.FIELDS}
        data['updated_at'] = self.updated_at.isoformat()
        return data


class EvidenceArtifact(models.Model):
    """A file derived from the video -- a frame, a crop, a contact sheet.

    Stored in the private evidence bucket and served only through short-lived
    signed URLs. Keyed to the video rather than the report because the worker
    produces these whether or not the owner has opened a report yet.
    """

    KIND_CHOICES = (
        ('contact_sheet', 'Contact sheet'),
        ('frame', 'Frame'),
        ('burst', 'Burst frame'),
        ('vehicle_crop', 'Vehicle crop'),
        ('plate_crop', 'Plate crop'),
        ('text_crop', 'Text crop'),
        ('photo', 'Supplementary photo'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    video = models.ForeignKey(Video, on_delete=models.CASCADE, related_name='evidence_artifacts')
    # Which analysis attempt produced it. Earlier runs' images are kept, not
    # replaced -- evidence is not deleted because a newer analyzer ran.
    run = models.ForeignKey(
        AnalysisRun, on_delete=models.SET_NULL, null=True, blank=True, related_name='evidence_artifacts')
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    storage_path = models.CharField(max_length=512)
    content_type = models.CharField(max_length=100, default='image/jpeg')
    sha256 = models.CharField(max_length=64)
    t_seconds = models.FloatField(null=True, blank=True, help_text='Position in the video, if any')
    bbox = models.JSONField(null=True, blank=True, help_text='[x, y, w, h] in source pixels, if a crop')
    width = models.IntegerField(default=0)
    height = models.IntegerField(default=0)
    label = models.CharField(max_length=255, blank=True, default='')
    analyzer_version = models.CharField(max_length=50, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['t_seconds', 'created_at']


class IncidentAccessLog(models.Model):
    """Who read or changed a video's incident data, and when.

    Written on every access, including by admins -- the point is that nobody,
    the operator included, reads another driver's details unrecorded.
    """

    ACTION_CHOICES = (
        ('view', 'Viewed'),
        ('update', 'Updated'),
    )

    video = models.ForeignKey(Video, on_delete=models.CASCADE, related_name='incident_access_log')
    clerk_user_id = models.CharField(max_length=255)
    action = models.CharField(max_length=10, choices=ACTION_CHOICES)
    as_admin = models.BooleanField(default=False)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-at']
        indexes = [models.Index(fields=['video', '-at'])]

    def to_dict(self) -> dict:
        return {
            'clerk_user_id': self.clerk_user_id,
            'action': self.action,
            'as_admin': self.as_admin,
            'at': self.at.isoformat(),
        }
