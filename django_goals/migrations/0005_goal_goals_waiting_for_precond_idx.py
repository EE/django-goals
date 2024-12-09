# Generated by Django 5.1.3 on 2024-12-08 22:54

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('django_goals', '0004_goal_waiting_for_count'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='goal',
            index=models.Index(condition=models.Q(('state', 'waiting_for_preconditions')), fields=['waiting_for_count'], name='goals_waiting_for_precond_idx'),
        ),
    ]