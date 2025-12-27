import random

from django.contrib import admin
from django.db import models

from django_goals.models import (
    AllDone, GoalState, PreconditionFailureBehavior, RetryMeLater, schedule,
)
from django_goals.utils import GoalRelatedMixin


class ErrorsBatch(GoalRelatedMixin, models.Model):
    desired = models.PositiveIntegerField(default=10)
    spawned = models.PositiveIntegerField(default=0)
    succeeded = models.PositiveIntegerField(default=0)
    failed = models.PositiveIntegerField(default=0)


@admin.register(ErrorsBatch)
class ErrorsBatchAdmin(admin.ModelAdmin):  # type: ignore
    list_display = ('id', 'desired', 'spawned', 'succeeded', 'failed')
    readonly_fields = ('id', 'spawned', 'succeeded', 'failed', 'processed_goal')

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        obj.processed_goal = schedule(
            do_batch,
            precondition_failure_behavior=PreconditionFailureBehavior.PROCEED,
        )
        super().save_model(request, obj, form, change)


def do_batch(goal):
    batch = ErrorsBatch.objects.get(processed_goal=goal)

    missing = batch.desired - batch.spawned
    if missing > 0:
        batch.spawned += missing
        batch.save(update_fields=['spawned'])
        return RetryMeLater(
            precondition_goals=[
                schedule(fail_sometimes)
                for _ in range(missing)
            ],
            message=f'Spawned {missing} tasks',
        )

    for precond in goal.precondition_goals.all():
        if precond.state == GoalState.ACHIEVED:
            batch.succeeded += 1
        elif precond.state in GoalState.GIVEN_UP:
            batch.failed += 1
    batch.save(update_fields=['succeeded', 'failed'])

    return AllDone()


def fail_sometimes(goal):
    if random.random() > 0.5:
        raise Exception('Failed')
    return AllDone()
