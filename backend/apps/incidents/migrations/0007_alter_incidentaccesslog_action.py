from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('incidents', '0006_alter_incidentaccesslog_action'),
    ]

    operations = [
        migrations.AlterField(
            model_name='incidentaccesslog',
            name='action',
            field=models.CharField(choices=[('view', 'Viewed'), ('update', 'Updated'), ('export', 'Exported'), ('delete', 'Deleted a photo')], max_length=10),
        ),
    ]
