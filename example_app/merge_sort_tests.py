import random

import pytest

from django_goals.busy_worker import worker
from django_goals.management.commands.goals_threaded_worker import (
    threaded_worker,
)
from django_goals.models import schedule
from django_goals.utils import is_goal_completed

from .merge_sort import MergeSort, ensure_sorted


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('worker_function', [
    worker,
    lambda **kwargs: threaded_worker(worker_specs=[(4, None)], **kwargs),
])
def test_merge_sort(worker_function):
    numbers = [random.randint(0, 100) for _ in range(10)]
    merge_sort = MergeSort.objects.create(
        numbers=numbers,
        goal=schedule(ensure_sorted),
    )
    worker_function(once=True)
    merge_sort.refresh_from_db()
    assert is_goal_completed(merge_sort.goal)
    assert merge_sort.sorted_numbers == sorted(numbers)
