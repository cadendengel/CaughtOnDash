from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("videos", "0005_add_aianalysis"),
    ]

    operations = [
        migrations.AddField(
            model_name="video",
            name="analysis_status",
            field=models.CharField(default="idle", help_text="AI analysis status (idle, queued, processing, ready, failed)", max_length=20),
        ),
        migrations.AddField(
            model_name="video",
            name="analysis_error",
            field=models.TextField(blank=True, default="", help_text="Most recent AI analysis error message"),
        ),
    ]