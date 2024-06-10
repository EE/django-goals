import pytest

from .factories import TaskFactory
from .models import TaskState, get_dependent_task_ids


@pytest.mark.django_db
@pytest.mark.parametrize(
    'task',
    [{'state': TaskState.GIVEN_UP}],
    indirect=True,
)
def test_retry(task):
    task.retry()
    assert task.state == TaskState.WAITING_FOR_DATE


@pytest.mark.django_db
@pytest.mark.parametrize('task', [{'state': TaskState.GIVEN_UP}], indirect=True)
def test_retry_dependent_on(task):
    next_task = TaskFactory(
        state=TaskState.NOT_GOING_TO_HAPPEN_SOON,
        precondition_tasks=[task],
    )
    altered_ids = task.retry()
    assert next_task.id in altered_ids
    next_task.refresh_from_db()
    assert next_task.state == TaskState.WAITING_FOR_DATE


@pytest.mark.django_db
def test_get_dependent_task_ids(task):
    next_task = TaskFactory(precondition_tasks=[task])
    assert next_task.id in get_dependent_task_ids([task.pk])
    assert task.id not in get_dependent_task_ids([next_task.pk])
