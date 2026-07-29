from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('publisher', '0011_scheduledpost_processing_since'),
    ]

    operations = [
        migrations.AddField(
            model_name='scheduledpost',
            name='story_text',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='scheduledpost',
            name='story_text_color',
            field=models.CharField(blank=True, default='#ffffff', max_length=20),
        ),
        migrations.AddField(
            model_name='scheduledpost',
            name='story_text_bg',
            field=models.CharField(blank=True, default='dark', max_length=10),
        ),
        migrations.AddField(
            model_name='scheduledpost',
            name='story_text_size',
            field=models.IntegerField(default=28),
        ),
        migrations.AddField(
            model_name='scheduledpost',
            name='story_text_x',
            field=models.FloatField(default=0.5),
        ),
        migrations.AddField(
            model_name='scheduledpost',
            name='story_text_y',
            field=models.FloatField(default=0.45),
        ),
        migrations.AddField(
            model_name='scheduledpost',
            name='story_link_x',
            field=models.FloatField(default=0.5),
        ),
        migrations.AddField(
            model_name='scheduledpost',
            name='story_link_y',
            field=models.FloatField(default=0.82),
        ),
    ]
