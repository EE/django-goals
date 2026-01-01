import random

from django.contrib import admin
from django.contrib.postgres.fields import ArrayField
from django.db import models, transaction

from django_goals.models import AllDone, Goal, RetryMeLater, schedule
from django_goals.utils import is_goal_completed


class MergeSort(models.Model):
    numbers = ArrayField(models.IntegerField())
    subsort_a = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    subsort_b = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    sorted_numbers = ArrayField(models.IntegerField(), null=True, blank=True)
    goal = models.ForeignKey(Goal, null=True, blank=True, on_delete=models.SET_NULL)


def ensure_sorted(goal):
    merge_sort = MergeSort.objects.get(goal=goal)

    # already done
    if merge_sort.sorted_numbers is not None:
        return AllDone()

    # base case
    if len(merge_sort.numbers) <= 1:
        merge_sort.sorted_numbers = merge_sort.numbers
        merge_sort.save(update_fields=['sorted_numbers'])
        return AllDone()

    # delegate to subsorts
    if (
        merge_sort.subsort_a is None or
        merge_sort.subsort_b is None
    ):
        merge_sort.subsort_a = MergeSort.objects.create(
            numbers=merge_sort.numbers[:len(merge_sort.numbers) // 2],
            goal=schedule(ensure_sorted),
        )
        merge_sort.subsort_b = MergeSort.objects.create(
            numbers=merge_sort.numbers[len(merge_sort.numbers) // 2:],
            goal=schedule(ensure_sorted),
        )
        merge_sort.save(update_fields=['subsort_a', 'subsort_b'])

    # wait for subsorts to finish
    if (
        not is_goal_completed(merge_sort.subsort_a.goal) or
        not is_goal_completed(merge_sort.subsort_b.goal)
    ):
        return RetryMeLater(
            precondition_goals=[g for g in [
                merge_sort.subsort_a.goal,
                merge_sort.subsort_b.goal,
            ] if g is not None],
            message='Waiting for subsorts to finish',
        )

    # merge sorted subsorts
    a = merge_sort.subsort_a.sorted_numbers
    assert a is not None, "Subsort A must be sorted here, because its goal is completed"
    b = merge_sort.subsort_b.sorted_numbers
    assert b is not None, "Subsort B must be sorted here, because its goal is completed"
    i = j = 0
    result = []
    while i < len(a) and j < len(b):
        if a[i] < b[j]:
            result.append(a[i])
            i += 1
        else:
            result.append(b[j])
            j += 1
    result.extend(a[i:])
    result.extend(b[j:])
    merge_sort.sorted_numbers = result
    merge_sort.save(update_fields=['sorted_numbers'])

    return AllDone()


@admin.register(MergeSort)
class MergeSortAdmin(admin.ModelAdmin):  # type: ignore
    readonly_fields = (
        'goal',
        'subsort_a',
        'subsort_b',
        'sorted_numbers',
    )

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if obj.goal is None:
            with transaction.atomic():
                obj.goal = schedule(ensure_sorted)
                obj.save(update_fields=['goal'])

    def get_changeform_initial_data(self, request):
        return {
            'numbers': [random.randint(0, 100) for _ in range(10)],
        }
