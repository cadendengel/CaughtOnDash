"""User account and profile models."""

from django.db import models


class Profile(models.Model):
    """User profile linked to Clerk auth."""

    clerk_user_id = models.CharField(
        max_length=255,
        unique=True,
        db_index=True,
        help_text="Unique identifier from Clerk auth",
    )
    email = models.EmailField(help_text="User email address")
    username = models.CharField(
        max_length=255,
        unique=True,
        db_index=True,
        help_text="Unique handle for profile URLs",
    )
    display_name = models.CharField(
        max_length=255,
        help_text="Display name visible to other users",
    )
    avatar_url = models.URLField(
        blank=True,
        default="",
        help_text="Profile picture URL from Clerk or custom source",
    )
    bio = models.TextField(
        blank=True,
        default="",
        help_text="User bio or description",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["clerk_user_id"]),
            models.Index(fields=["username"]),
        ]

    def __str__(self):
        return f"{self.username} ({self.clerk_user_id})"

    def to_dict(self):
        """Serialize to API response format."""
        return {
            "clerk_user_id": self.clerk_user_id,
            "email": self.email,
            "username": self.username,
            "display_name": self.display_name,
            "avatar_url": self.avatar_url,
            "bio": self.bio,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }
