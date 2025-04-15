# Generated by Django 5.2 on 2025-04-15 08:46

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("django_goals", "0009_goal_waiting_for_not_achieved_count"),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name="goal",
            name="goals_waiting_for_failed_idx",
        ),
        migrations.AddField(
            model_name="goal",
            name="precondition_failure_behavior",
            field=models.CharField(
                choices=[
                    ("block", "Do not proceed if preconditions fail"),
                    (
                        "proceed",
                        "Proceed with goal execution even if preconditions fail",
                    ),
                ],
                default="block",
                max_length=10,
            ),
        ),
        migrations.AddIndex(
            model_name="goal",
            index=models.Index(
                condition=models.Q(
                    ("precondition_failure_behavior", "block"),
                    ("state", "waiting_for_preconditions"),
                ),
                fields=["waiting_for_failed_count"],
                name="goals_waiting_for_failed_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="goal",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("precondition_failure_behavior__in", ["block", "proceed"])
                ),
                name="goals_precondition_failure_behavior",
            ),
        ),
    ]
