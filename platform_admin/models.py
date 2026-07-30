import re

from django.db import models

from core.models import TimeStampedModel

_YT_ID = re.compile(
    r"(?:youtu\.be/|youtube\.com/(?:watch\?v=|embed/|shorts/|v/))([A-Za-z0-9_-]{11})"
)


class TutorialVideo(TimeStampedModel):
    """A help/tutorial video managed platform-wide by super admins and shown to
    every shop on their dashboard, ordered by ``sequence`` (serial number)."""

    title = models.CharField(max_length=200)
    youtube_url = models.URLField(help_text="Full YouTube link (watch, youtu.be, or embed).")
    sequence = models.PositiveIntegerField(
        default=1, db_index=True, help_text="Play order — lower numbers show first."
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sequence", "id"]
        indexes = [models.Index(fields=["is_active", "sequence"])]

    def __str__(self):
        return f"{self.sequence}. {self.title}"

    @property
    def video_id(self):
        """Extract the 11-char YouTube id from any common URL shape."""
        m = _YT_ID.search(self.youtube_url or "")
        return m.group(1) if m else ""

    @property
    def embed_url(self):
        vid = self.video_id
        return f"https://www.youtube.com/embed/{vid}" if vid else ""

    @property
    def thumbnail_url(self):
        vid = self.video_id
        return f"https://img.youtube.com/vi/{vid}/hqdefault.jpg" if vid else ""


class ContactMessage(TimeStampedModel):
    """Public contact-form submission. Platform-level (no shop). Read + deleted
    only by platform staff from the Super Admin dashboard."""

    name = models.CharField(max_length=150)
    email = models.EmailField()
    phone = models.CharField(max_length=30, blank=True)
    subject = models.CharField(max_length=200, blank=True)
    message = models.TextField()
    is_read = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["is_read", "created_at"])]

    def __str__(self):
        return f"{self.name} <{self.email}>"
