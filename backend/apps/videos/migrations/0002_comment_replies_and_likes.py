from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('videos', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='videocomment',
            name='parent_comment',
            field=models.ForeignKey(blank=True, help_text='Top-level comment this reply belongs to', null=True, on_delete=django.db.models.deletion.CASCADE, related_name='replies', to='videos.videocomment'),
        ),
        migrations.AddIndex(
            model_name='videocomment',
            index=models.Index(fields=['parent_comment', '-created_at'], name='videos_video_parent_c3e4c2_idx'),
        ),
        migrations.CreateModel(
            name='VideoCommentLike',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('user_clerk_user_id', models.CharField(db_index=True, help_text='Clerk user ID of the liker', max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('comment', models.ForeignKey(help_text='The comment being liked', on_delete=django.db.models.deletion.CASCADE, related_name='likes', to='videos.videocomment')),
            ],
            options={
                'ordering': ['-created_at'],
                'unique_together': {('comment', 'user_clerk_user_id')},
            },
        ),
    ]
