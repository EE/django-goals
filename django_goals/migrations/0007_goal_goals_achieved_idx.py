# Generated by Django 5.1.4 on 2024-12-11 23:16

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('django_goals', '0006_goal_waiting_for_failed_count_and_more'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='goal',
            index=models.Index(condition=models.Q(('state', 'achieved')), fields=['created_at'], name='goals_achieved_idx'),
        ),
    ]
