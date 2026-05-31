from django.db import migrations, models
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("videos", "0004_normalize_video_tags"),
    ]

    operations = [
        migrations.CreateModel(
            name="AIAnalysis",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("schema_version", models.CharField(default="1.0", max_length=16)),
                ("generated_by", models.CharField(blank=True, default="", max_length=255)),
                ("analysis", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "video",
                    models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="analyses", to="videos.video"),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
    ]
