"""Video and video interaction models."""

import uuid

from django.db import models

from apps.videos.tagging import normalize_video_tags, serialize_video_tags


class Video(models.Model):
    """Dashcam video asset."""

    STATUS_CHOICES = (
        ("pending", "Pending"),
        ("processing", "Processing"),
        ("ready", "Ready"),
        ("failed", "Failed"),
    )
    VISIBILITY_CHOICES = (
        ("public", "Public"),
        ("private", "Private"),
        ("unlisted", "Unlisted"),
    )
    ANALYSIS_STATUS_CHOICES = (
        ("pending", "Pending Analysis"),
        ("processing", "Processing"),
        ("complete", "Complete"),
        ("failed", "Failed"),
        ("cancelled", "Cancelled"),
    )
    ANALYSIS_STAGE_CHOICES = (
        ("queued", "Queued"),
        ("claimed", "Claimed"),
        ("downloading", "Downloading"),
        ("analyzing", "Analyzing"),
        ("uploading_results", "Uploading Results"),
        ("complete", "Complete"),
        ("failed", "Failed"),
        ("cancelled", "Cancelled"),
    )
    APPROVAL_STATUS_CHOICES = (
        ("pending_review", "Pending Review"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner_clerk_user_id = models.CharField(
        max_length=255,
        db_index=True,
        help_text="Clerk user ID of the video owner",
    )
    title = models.CharField(
        max_length=255,
        default="Untitled dashcam clip",
        help_text="Video title",
    )
    description = models.TextField(
        blank=True,
        default="",
        help_text="Video description",
    )
    visibility = models.CharField(
        max_length=10,
        choices=VISIBILITY_CHOICES,
        default="public",
        help_text="Video visibility setting",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
        help_text="Processing status (pending, processing, ready, failed)",
    )
    original_filename = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Original filename from upload",
    )
    upload_url = models.URLField(
        blank=True,
        default="",
        help_text="Presigned upload URL (temporary)",
    )
    playback_url = models.URLField(
        blank=True,
        default="",
        help_text="CDN/playback URL for the video",
    )
    thumbnail_url = models.URLField(
        blank=True,
        default="",
        help_text="Thumbnail image URL",
    )
    duration_seconds = models.IntegerField(
        default=0,
        help_text="Video duration in seconds",
    )
    views = models.IntegerField(
        default=0,
        help_text="Number of views",
    )
    tags = models.JSONField(
        default=list,
        blank=True,
        help_text="List of tag strings",
    )
    # Analysis state machine fields
    analysis_status = models.CharField(
        max_length=20,
        choices=ANALYSIS_STATUS_CHOICES,
        default="pending",
        db_index=True,
        help_text="AI analysis status (pending, processing, complete, failed, cancelled)",
    )
    analysis_stage = models.CharField(
        max_length=25,
        choices=ANALYSIS_STAGE_CHOICES,
        default="queued",
        help_text="Detailed stage within analysis workflow",
    )
    analysis_progress = models.IntegerField(
        default=0,
        help_text="Analysis progress 0-100",
    )
    analysis_requested_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When analysis was requested",
    )
    analysis_started_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When analysis started (worker claimed job)",
    )
    analysis_completed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When analysis completed",
    )
    analysis_failed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When analysis failed",
    )
    analysis_error = models.TextField(
        blank=True,
        default="",
        help_text="Error message if analysis failed",
    )
    # Approval gate. A video is only claimable once someone has approved it, so
    # analysis capacity is spent deliberately rather than on everything uploaded.
    # Rejection blocks analysis only -- the video stays visible in the feed.
    approval_status = models.CharField(
        max_length=20,
        choices=APPROVAL_STATUS_CHOICES,
        default="pending_review",
        db_index=True,
        help_text="Whether this video has been approved for analysis",
    )
    approval_decided_by = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Clerk user ID of whoever approved or rejected",
    )
    approval_decided_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the approval decision was made",
    )
    analysis_priority = models.IntegerField(
        default=0,
        db_index=True,
        help_text="Higher runs sooner. Ties fall back to when analysis was requested.",
    )
    # Worker assignment fields
    worker_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        db_index=True,
        help_text="ID of the worker processing this job",
    )
    worker_name = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Name of the worker processing this job",
    )
    worker_claimed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the worker claimed this job",
    )
    worker_last_seen_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Last heartbeat from the worker",
    )
    # AI analysis results
    ai_summary = models.TextField(
        blank=True,
        default="",
        help_text="AI-generated summary of video content",
    )
    ai_tags = models.JSONField(
        default=list,
        blank=True,
        help_text="AI-generated tags for the video",
    )
    ai_events = models.JSONField(
        default=list,
        blank=True,
        help_text="AI-detected events in the video with timestamps",
    )
    ai_metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Additional AI analysis metadata",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Soft delete timestamp",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["owner_clerk_user_id", "-created_at"]),
            models.Index(fields=["status"]),
            models.Index(fields=["analysis_status"]),
            models.Index(fields=["visibility"]),
            models.Index(fields=["worker_id"]),
            models.Index(fields=["-worker_last_seen_at"]),
        ]

    def __str__(self):
        return f"{self.title} ({self.id})"

    def to_dict(self):
        """Serialize to API response format."""
        return {
            "id": str(self.id),
            "owner_clerk_user_id": self.owner_clerk_user_id,
            "title": self.title,
            "description": self.description,
            "visibility": self.visibility,
            "status": self.status,
            "original_filename": self.original_filename,
            "upload_url": self.upload_url,
            "playback_url": self.playback_url,
            "thumbnail_url": self.thumbnail_url,
            "duration_seconds": self.duration_seconds,
            "views": self.views,
            "tags": serialize_video_tags(self.tags),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "deleted_at": self.deleted_at.isoformat() if self.deleted_at else None,
            # Analysis state
            "analysis_status": self.analysis_status,
            "analysis_stage": self.analysis_stage,
            "analysis_progress": self.analysis_progress,
            "analysis_requested_at": self.analysis_requested_at.isoformat() if self.analysis_requested_at else None,
            "analysis_started_at": self.analysis_started_at.isoformat() if self.analysis_started_at else None,
            "analysis_completed_at": self.analysis_completed_at.isoformat() if self.analysis_completed_at else None,
            "analysis_failed_at": self.analysis_failed_at.isoformat() if self.analysis_failed_at else None,
            "analysis_error": self.analysis_error,
            # Approval gate
            "approval_status": self.approval_status,
            "approval_decided_by": self.approval_decided_by,
            "approval_decided_at": self.approval_decided_at.isoformat() if self.approval_decided_at else None,
            # Worker info
            "worker_id": self.worker_id,
            "worker_name": self.worker_name,
            "worker_claimed_at": self.worker_claimed_at.isoformat() if self.worker_claimed_at else None,
            "worker_last_seen_at": self.worker_last_seen_at.isoformat() if self.worker_last_seen_at else None,
            # AI results
            "ai_summary": self.ai_summary,
            "ai_tags": self.ai_tags,
            "ai_events": self.ai_events,
            "ai_metadata": self.ai_metadata,
        }

    def set_tags(self, tags, default_source='user'):
        self.tags = normalize_video_tags(tags, default_source=default_source)


class AnalysisRun(models.Model):
    """One attempt at analyzing a video, from request through to outcome.

    Video keeps the latest results denormalised in its ai_* fields so the feed
    stays a single query. This is the history behind them: who asked, who
    approved, which worker ran it, and what it produced -- so a video coming
    back for review can be shown as "3rd attempt" alongside what the previous
    runs concluded.

    A run is created when analysis is *requested*, not when it completes, so an
    attempt that was never approved or that failed is still recorded.
    """

    RUN_STATUS_CHOICES = (
        ("awaiting_approval", "Awaiting Approval"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
        ("processing", "Processing"),
        ("complete", "Complete"),
        ("failed", "Failed"),
        ("cancelled", "Cancelled"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    video = models.ForeignKey(
        Video,
        on_delete=models.CASCADE,
        related_name="analysis_runs",
        help_text="The video this attempt belongs to",
    )
    attempt_number = models.PositiveIntegerField(
        help_text="1 for the first request, incrementing per re-analysis",
    )
    status = models.CharField(
        max_length=20,
        choices=RUN_STATUS_CHOICES,
        default="awaiting_approval",
        db_index=True,
    )

    requested_by = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Clerk user ID that requested this analysis, blank if automatic",
    )
    requested_at = models.DateTimeField(auto_now_add=True)

    decided_by = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Clerk user ID that approved or rejected this attempt",
    )
    decided_at = models.DateTimeField(null=True, blank=True)

    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    worker_id = models.CharField(max_length=255, blank=True, default="")
    worker_name = models.CharField(max_length=255, blank=True, default="")

    # What this attempt produced. Kept per-run so two attempts can be compared,
    # which is the useful thing when re-reviewing a video.
    summary = models.TextField(blank=True, default="")
    tags = models.JSONField(default=list, blank=True)
    events = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-requested_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["video", "attempt_number"],
                name="unique_attempt_number_per_video",
            ),
        ]
        indexes = [
            models.Index(fields=["video", "-requested_at"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"Run {self.attempt_number} of {self.video_id} ({self.status})"

    def to_dict(self):
        return {
            "id": str(self.id),
            "video_id": str(self.video_id),
            "attempt_number": self.attempt_number,
            "status": self.status,
            "requested_by": self.requested_by,
            "requested_at": self.requested_at.isoformat(),
            "decided_by": self.decided_by,
            "decided_at": self.decided_at.isoformat() if self.decided_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "worker_id": self.worker_id,
            "worker_name": self.worker_name,
            "summary": self.summary,
            "tags": self.tags,
            "events": self.events,
            "metadata": self.metadata,
            "error": self.error,
        }


class Worker(models.Model):
    """Desktop worker for video analysis processing."""

    WORKER_STATUS_CHOICES = (
        ("offline", "Offline"),
        ("idle", "Idle"),
        ("processing", "Processing"),
        ("error", "Error"),
    )

    id = models.CharField(
        max_length=255,
        primary_key=True,
        help_text="Unique worker identifier (e.g., caden-desktop-1)",
    )
    name = models.CharField(
        max_length=255,
        help_text="Human-readable worker name",
    )
    token_hash = models.CharField(
        max_length=255,
        db_index=True,
        help_text="SHA256 hash of the API token",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this worker is active",
    )
    status = models.CharField(
        max_length=20,
        choices=WORKER_STATUS_CHOICES,
        default="offline",
        help_text="Current worker status",
    )
    current_job = models.UUIDField(
        null=True,
        blank=True,
        help_text="Currently processing video ID",
    )
    last_seen_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Last heartbeat timestamp",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["is_active"]),
            models.Index(fields=["-last_seen_at"]),
        ]

    def __str__(self):
        return f"Worker<{self.id}> ({self.status})"

    def to_dict(self):
        """Serialize to API response format."""
        return {
            "worker_id": self.id,
            "name": self.name,
            "is_active": self.is_active,
            "status": self.status,
            "current_job_id": str(self.current_job) if self.current_job else None,
            "last_seen_at": self.last_seen_at.isoformat() if self.last_seen_at else None,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


class VideoLike(models.Model):
    """Like interaction on a video."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    video = models.ForeignKey(
        Video,
        on_delete=models.CASCADE,
        related_name="likes",
        help_text="The video being liked",
    )
    user_clerk_user_id = models.CharField(
        max_length=255,
        db_index=True,
        help_text="Clerk user ID of the liker",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [["video", "user_clerk_user_id"]]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user_clerk_user_id} liked {self.video.id}"


class VideoComment(models.Model):
    """Comment on a video."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    video = models.ForeignKey(
        Video,
        on_delete=models.CASCADE,
        related_name="comments",
        help_text="The video being commented on",
    )
    parent_comment = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        related_name='replies',
        on_delete=models.CASCADE,
        help_text='Top-level comment this reply belongs to',
    )
    user_clerk_user_id = models.CharField(
        max_length=255,
        db_index=True,
        help_text="Clerk user ID of the commenter",
    )
    text = models.TextField(help_text="Comment text")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["video", "-created_at"]),
            models.Index(fields=["parent_comment", "-created_at"]),
        ]

    def __str__(self):
        return f"Comment on {self.video.id} by {self.user_clerk_user_id}"

    def to_dict(self):
        """Serialize to API response format."""
        return {
            "id": str(self.id),
            "video_id": str(self.video.id),
            "parent_comment_id": str(self.parent_comment_id) if self.parent_comment_id else None,
            "user_clerk_user_id": self.user_clerk_user_id,
            "text": self.text,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


class VideoCommentLike(models.Model):
    """Like interaction on a comment."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    comment = models.ForeignKey(
        VideoComment,
        on_delete=models.CASCADE,
        related_name="likes",
        help_text="The comment being liked",
    )
    user_clerk_user_id = models.CharField(
        max_length=255,
        db_index=True,
        help_text="Clerk user ID of the liker",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [["comment", "user_clerk_user_id"]]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user_clerk_user_id} liked comment {self.comment.id}"
