import datetime
from unittest import mock

import pytest
from django.utils import timezone

from .factories import TaskFactory
from .models import (
    RetryMeLater, Task, TaskState, handle_waiting_for_preconditions_tasks,
    handle_waiting_for_worker_tasks, schedule, worker_turn,
)


@pytest.mark.django_db
def test_worker_turn_noop():
    now = timezone.now()
    transitions_done = worker_turn(now)
    assert transitions_done == 0


@pytest.mark.django_db
@pytest.mark.parametrize('task', [
    {'state': TaskState.WAITING_FOR_DATE},
    {'state': TaskState.WAITING_FOR_PRECONDITIONS},
    {'state': TaskState.WAITING_FOR_WORKER},
    {'state': TaskState.DONE},
    {'state': TaskState.GIVEN_UP},
], indirect=True)
def test_handle_waiting_for_worker_tasks_return_value(task):
    now = timezone.now()
    did_a_thing = handle_waiting_for_worker_tasks(now)
    assert did_a_thing is (task.state == TaskState.WAITING_FOR_WORKER)


@pytest.mark.django_db
@pytest.mark.parametrize('task', [{
    'state': TaskState.WAITING_FOR_WORKER,
    'task_type': 'os.path.join',
    'instructions': {
        'args': [1, 2],
        'kwargs': {'a': 'b'},
    },
}], indirect=True)
def test_handle_waiting_for_worker_tasks_success(task):
    now = timezone.now()

    with mock.patch('os.path.join') as func:
        func.return_value = {'aaa': 'im happy'}  # will be ignored
        handle_waiting_for_worker_tasks(now)

    assert func.call_count == 1
    assert func.call_args == mock.call(task, 1, 2, a='b')

    task.refresh_from_db()
    assert task.state == TaskState.DONE

    execution = task.executions.get()
    assert execution.success
    assert execution.time_taken > datetime.timedelta(0)


@pytest.mark.django_db
@pytest.mark.parametrize('task', [{
    'state': TaskState.WAITING_FOR_WORKER,
    'precondition_date': timezone.now() - timezone.timedelta(days=1),
}], indirect=True)
def test_handle_waiting_for_worker_tasks_failure(task):
    now = timezone.now()
    with mock.patch('django_goals.models.follow_instructions') as follow_instructions:
        follow_instructions.side_effect = Exception
        handle_waiting_for_worker_tasks(now)

    task.refresh_from_db()
    assert task.state == TaskState.WAITING_FOR_DATE
    assert task.precondition_date > now

    execution = task.executions.get()
    assert not execution.success


@pytest.mark.django_db
@pytest.mark.parametrize('task', [{'state': TaskState.WAITING_FOR_WORKER}], indirect=True)
def test_handle_waiting_for_worker_tasks_retry(task):
    now = timezone.now()
    other_task = TaskFactory()
    with mock.patch('django_goals.models.follow_instructions') as follow_instructions:
        follow_instructions.return_value = RetryMeLater(
            precondition_tasks=[other_task],
        )
        handle_waiting_for_worker_tasks(now)

    task.refresh_from_db()
    assert task.state == TaskState.WAITING_FOR_DATE
    assert task.precondition_tasks.get() == other_task

    execution = task.executions.get()
    assert execution.success


@pytest.mark.django_db
@pytest.mark.parametrize('task', [{
    'state': TaskState.WAITING_FOR_PRECONDITIONS,
}], indirect=True)
@pytest.mark.parametrize(
    ('precondition_task_states', 'expected_state'),
    [
        ([], TaskState.WAITING_FOR_WORKER),
        ([TaskState.DONE], TaskState.WAITING_FOR_WORKER),
        ([TaskState.DONE, TaskState.DONE], TaskState.WAITING_FOR_WORKER),
        ([TaskState.DONE, TaskState.GIVEN_UP], TaskState.NOT_GOING_TO_HAPPEN_SOON),
        ([TaskState.WAITING_FOR_DATE], TaskState.WAITING_FOR_PRECONDITIONS),
        ([TaskState.BLOCKED], TaskState.NOT_GOING_TO_HAPPEN_SOON),
    ],
)
def test_handle_waiting_for_preconditions_tasks(task, precondition_task_states, expected_state):
    precondition_tasks = [
        TaskFactory(state=state)
        for state in precondition_task_states
    ]
    task.precondition_tasks.set(precondition_tasks)

    handle_waiting_for_preconditions_tasks()

    task.refresh_from_db()
    assert task.state == expected_state


def trigger_database_error(task):
    Task.objects.create(id=task.id)  # violates unique constraint


@pytest.mark.django_db(transaction=True)
def test_transaction_error_in_task():
    task = schedule(trigger_database_error)
    worker_turn(timezone.now())
    task.refresh_from_db()
    assert task.state == TaskState.CORRUPTED
