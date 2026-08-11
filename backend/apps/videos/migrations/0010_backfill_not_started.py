"""Correct videos stored as 'cancelled' that were never actually run.

Uploads used to be written as analysis_status='cancelled' while they waited to
be started, which was simply false -- there was no run to cancel. The new
'not_started' value says what is true. This moves the existing rows over.

Scoped to videos still waiting to be started (approval_status='pending_review').
An approved video sitting at 'cancelled' really did have a run abandoned, and
must keep saying so.
"""

from django.db import migrations


def to_not_started(apps, schema_editor):
    Video = apps.get_model('videos', 'Video')
    Video.objects.filter(
        approval_status='pending_review',
        analysis_status='cancelled',
    ).update(analysis_status='not_started')


def back_to_cancelled(apps, schema_editor):
    Video = apps.get_model('videos', 'Video')
    Video.objects.filter(analysis_status='not_started').update(analysis_status='cancelled')


class Migration(migrations.Migration):

    dependencies = [
        ('videos', '0009_alter_video_analysis_status'),
    ]

    operations = [
        migrations.RunPython(to_not_started, back_to_cancelled),
    ]
