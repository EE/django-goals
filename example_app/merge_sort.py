from django.contrib.postgres.fields import ArrayField
from django.db import models

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
        return RetryMeLater(precondition_goals=[
            merge_sort.subsort_a.goal,
            merge_sort.subsort_b.goal,
        ])

    # merge sorted subsorts
    a = merge_sort.subsort_a.sorted_numbers
    b = merge_sort.subsort_b.sorted_numbers
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
