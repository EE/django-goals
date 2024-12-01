# Generated by Django 5.0.7 on 2024-12-01 13:57

import django.contrib.postgres.fields
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('django_goals', '0003_remove_goal_goals_waiting_for_worker_idx_and_more'),
        ('example_app', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='MergeSort',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('numbers', django.contrib.postgres.fields.ArrayField(base_field=models.IntegerField(), size=None)),
                ('sorted_numbers', django.contrib.postgres.fields.ArrayField(base_field=models.IntegerField(), blank=True, null=True, size=None)),
                ('goal', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='django_goals.goal')),
                ('subsort_a', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='example_app.mergesort')),
                ('subsort_b', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='example_app.mergesort')),
            ],
        ),
    ]
