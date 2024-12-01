import random

import pytest

from django_goals.models import schedule, worker
from django_goals.utils import is_goal_completed

from .merge_sort import MergeSort, ensure_sorted


@pytest.mark.django_db
def test_merge_sort():
    numbers = [random.randint(0, 100) for _ in range(10)]
    merge_sort = MergeSort.objects.create(
        numbers=numbers,
        goal=schedule(ensure_sorted),
    )
    worker(once=True)
    merge_sort.refresh_from_db()
    assert is_goal_completed(merge_sort.goal)
    assert merge_sort.sorted_numbers == sorted(numbers)
