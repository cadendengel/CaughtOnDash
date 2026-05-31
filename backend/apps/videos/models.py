"""Video and video interaction models."""

import uuid

from django.db import models

from apps.videos.tagging import normalize_video_tags, serialize_video_tags
from django.contrib.postgres.indexes import GinIndex


class AIAnalysis(models.Model):
    """Stores full AI analysis JSON for a video."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    video = models.ForeignKey(
        'Video',
        on_delete=models.CASCADE,
        related_name="analyses",
        help_text="Video this analysis belongs to",
    )
    schema_version = models.CharField(max_length=16, default="1.0")
    generated_by = models.CharField(max_length=255, blank=True, default="")
    analysis = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["video"])]

    def __str__(self):
        return f"AIAnalysis {self.id} for {self.video_id}"


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
            models.Index(fields=["visibility"]),
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
        }

    def set_tags(self, tags, default_source='user'):
        self.tags = normalize_video_tags(tags, default_source=default_source)


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
