"""Backfill approval state and analysis history for videos that predate them.

Existing videos were uploaded and analyzed before the approval gate existed.
Leaving them at the new 'pending_review' default would retroactively strand
them: already-analyzed videos would show as awaiting review, and anything
queued would silently stop being picked up. They are approved instead, so the
gate applies to what comes next rather than rewriting what already happened.

Videos that have been analyzed also get a synthetic first run, so their history
is not empty and the UI does not have to special-case "no runs but has results".
"""

from django.db import migrations


def backfill(apps, schema_editor):
    Video = apps.get_model('videos', 'Video')
    AnalysisRun = apps.get_model('videos', 'AnalysisRun')

    Video.objects.all().update(approval_status='approved')

    runs = []
    for video in Video.objects.all().iterator():
        # Only videos that actually produced something get a history entry.
        # A record with no results has nothing to describe.
        has_results = bool(video.ai_summary or video.ai_tags or video.ai_events)
        if not has_results:
            continue

        runs.append(AnalysisRun(
            video=video,
            attempt_number=1,
            status='complete',
            requested_by=video.owner_clerk_user_id or '',
            decided_by='',
            decided_at=video.analysis_requested_at,
            started_at=video.analysis_started_at,
            finished_at=video.analysis_completed_at,
            worker_id=video.worker_id or '',
            worker_name=video.worker_name or '',
            summary=video.ai_summary or '',
            tags=video.ai_tags or [],
            events=video.ai_events or [],
            metadata=video.ai_metadata or {},
        ))

    if runs:
        AnalysisRun.objects.bulk_create(runs)


def unbackfill(apps, schema_editor):
    # The synthetic runs are the only thing worth undoing; approval_status goes
    # away with the field itself if this is rolled back further.
    AnalysisRun = apps.get_model('videos', 'AnalysisRun')
    AnalysisRun.objects.filter(attempt_number=1, decided_by='').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('videos', '0006_video_approval_decided_at_video_approval_decided_by_and_more'),
    ]

    operations = [
        migrations.RunPython(backfill, unbackfill),
    ]
