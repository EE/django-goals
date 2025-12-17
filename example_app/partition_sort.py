import random

from django.contrib import admin
from django.contrib.postgres.fields import ArrayField
from django.db import models, transaction

from django_goals.models import (
    AllDone, Goal, PreconditionsMode, RetryMeLater, schedule,
)
from django_goals.utils import is_goal_completed


"""
QuickSort-like sort to showcase ANY precondition mode.
"""


class PartitionSort(models.Model):
    numbers = ArrayField(models.IntegerField())
    partition_done = models.BooleanField(default=False)
    subsort_low = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    subsort_high = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    low_done = models.BooleanField(default=False)
    high_done = models.BooleanField(default=False)
    sorted_numbers = ArrayField(models.IntegerField(null=True), null=True, blank=True)
    goal = models.ForeignKey(Goal, null=True, blank=True, on_delete=models.SET_NULL)


def ensure_sorted(goal):
    sort = PartitionSort.objects.get(goal=goal)

    # base case
    if len(sort.numbers) <= 1:
        sort.sorted_numbers = sort.numbers
        sort.save(update_fields=['sorted_numbers'])
        return AllDone()

    # First phase: partition the array
    if not sort.partition_done:
        pivot_index = 0
        pivot = sort.numbers[pivot_index]

        # Partition array
        low = []
        high = []
        for i, num in enumerate(sort.numbers):
            if i == pivot_index:
                continue
            if num <= pivot:
                low.append(num)
            else:
                high.append(num)

        # Create subsorts and schedule their goals
        sort.subsort_low = PartitionSort.objects.create(
            numbers=low,
            goal=schedule(ensure_sorted, preconditions_mode=PreconditionsMode.ANY),
        )
        sort.subsort_high = PartitionSort.objects.create(
            numbers=high,
            goal=schedule(ensure_sorted, preconditions_mode=PreconditionsMode.ANY),
        )
        sort.partition_done = True
        sort.sorted_numbers = [None] * len(low) + [pivot] + [None] * len(high)
        sort.save(update_fields=['subsort_low', 'subsort_high', 'partition_done', 'sorted_numbers'])

        # Wait for at least one subsort to complete
        return RetryMeLater(
            precondition_goals=[sort.subsort_low.goal, sort.subsort_high.goal],
            message='Waiting for at least one subsort to complete',
        )

    assert sort.subsort_low is not None, "Subsort low must be set after partitioning"
    assert sort.subsort_high is not None, "Subsort high must be set after partitioning"
    assert sort.sorted_numbers is not None, "Sorted numbers must be initialized after partitioning"

    # Copy low sort if done
    if is_goal_completed(sort.subsort_low.goal) and not sort.low_done:
        assert sort.subsort_low.sorted_numbers is not None, "Low subsort must be sorted here, because its goal is completed"
        sort.sorted_numbers[:len(sort.subsort_low.sorted_numbers)] = sort.subsort_low.sorted_numbers
        sort.low_done = True
        sort.save(update_fields=['sorted_numbers', 'low_done'])
        return RetryMeLater(message='Copied low sort')

    # Copy high sort if done
    if is_goal_completed(sort.subsort_high.goal) and not sort.high_done:
        assert sort.subsort_high.sorted_numbers is not None, "High subsort must be sorted here, because its goal is completed"
        if sort.subsort_high.sorted_numbers:  # zero length wont work with negative slicing
            sort.sorted_numbers[-len(sort.subsort_high.sorted_numbers):] = sort.subsort_high.sorted_numbers
        sort.high_done = True
        sort.save(update_fields=['sorted_numbers', 'high_done'])
        return RetryMeLater(message='Copied high sort')

    # All done
    return AllDone()


@admin.register(PartitionSort)
class PartitionSortAdmin(admin.ModelAdmin):
    readonly_fields = (
        'goal',
        'sorted_numbers',
        'partition_done',
        'subsort_low',
        'subsort_high',
        'low_done',
        'high_done',
    )

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        with transaction.atomic():
            obj.goal = schedule(ensure_sorted, preconditions_mode=PreconditionsMode.ANY)
            obj.save(update_fields=['goal'])

    def get_changeform_initial_data(self, request):
        return {
            'numbers': [random.randint(0, 100) for _ in range(10)],
        }
