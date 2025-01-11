import random

import pytest

from django_goals.models import PreconditionsMode, schedule, worker
from django_goals.utils import is_goal_completed

from .partition_sort import PartitionSort, ensure_sorted


@pytest.mark.django_db(transaction=True)
def test_partition_sort():
    numbers = [random.randint(0, 100) for _ in range(10)]
    sort = PartitionSort.objects.create(
        numbers=numbers,
        goal=schedule(ensure_sorted, preconditions_mode=PreconditionsMode.ANY),
    )
    worker(once=True)
    sort.refresh_from_db()
    assert is_goal_completed(sort.goal)
    assert sort.sorted_numbers == sorted(numbers)
