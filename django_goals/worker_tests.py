import datetime
from unittest import mock

import pytest
from django.utils import timezone

from .blocking_worker import listen_goal_waiting_for_worker
from .factories import GoalFactory
from .models import (
    Goal, GoalState, RetryMeLater, handle_waiting_for_preconditions,
    handle_waiting_for_worker, schedule, worker_turn,
)


@pytest.mark.django_db
def test_worker_turn_noop():
    now = timezone.now()
    transitions_done = worker_turn(now)
    assert transitions_done == 0


@pytest.mark.django_db
@pytest.mark.parametrize('goal', [
    {'state': GoalState.WAITING_FOR_DATE},
    {'state': GoalState.WAITING_FOR_PRECONDITIONS},
    {'state': GoalState.WAITING_FOR_WORKER},
    {'state': GoalState.ACHIEVED},
    {'state': GoalState.GIVEN_UP},
], indirect=True)
def test_handle_waiting_for_worker_return_value(goal):
    progress = handle_waiting_for_worker()
    did_a_thing = progress is not None
    assert did_a_thing is (goal.state == GoalState.WAITING_FOR_WORKER)


@pytest.mark.django_db
@pytest.mark.parametrize('goal', [{
    'state': GoalState.WAITING_FOR_WORKER,
    'handler': 'os.path.join',
    'instructions': {
        'args': [1, 2],
        'kwargs': {'a': 'b'},
    },
}], indirect=True)
def test_handle_waiting_for_worker_success(goal):
    with mock.patch('os.path.join') as func:
        func.return_value = {'aaa': 'im happy'}  # will be ignored
        handle_waiting_for_worker()

    assert func.call_count == 1
    assert func.call_args == mock.call(goal, 1, 2, a='b')

    goal.refresh_from_db()
    assert goal.state == GoalState.ACHIEVED

    progress = goal.progress.get()
    assert progress.success
    assert progress.time_taken > datetime.timedelta(0)


@pytest.mark.django_db
@pytest.mark.parametrize('goal', [{
    'state': GoalState.WAITING_FOR_WORKER,
    'precondition_date': timezone.now() - timezone.timedelta(days=1),
}], indirect=True)
def test_handle_waiting_for_worker_failure(goal):
    now = timezone.now()
    with mock.patch('django_goals.models.follow_instructions') as follow_instructions:
        follow_instructions.side_effect = Exception
        handle_waiting_for_worker()

    goal.refresh_from_db()
    assert goal.state == GoalState.WAITING_FOR_DATE
    assert goal.precondition_date > now

    progress = goal.progress.get()
    assert not progress.success


@pytest.mark.django_db
@pytest.mark.parametrize('goal', [{'state': GoalState.WAITING_FOR_WORKER}], indirect=True)
def test_handle_waiting_for_worker_retry(goal):
    other_goal = GoalFactory()
    with mock.patch('django_goals.models.follow_instructions') as follow_instructions:
        follow_instructions.return_value = RetryMeLater(
            precondition_goals=[other_goal],
        )
        handle_waiting_for_worker()

    goal.refresh_from_db()
    assert goal.state == GoalState.WAITING_FOR_DATE
    assert goal.precondition_goals.get() == other_goal

    progress = goal.progress.get()
    assert progress.success


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('goal', [{
    'state': GoalState.WAITING_FOR_PRECONDITIONS,
}], indirect=True)
@pytest.mark.parametrize(
    ('precondition_goal_states', 'expected_state'),
    [
        ([], GoalState.WAITING_FOR_WORKER),
        ([GoalState.ACHIEVED], GoalState.WAITING_FOR_WORKER),
        ([GoalState.ACHIEVED, GoalState.ACHIEVED], GoalState.WAITING_FOR_WORKER),
        ([GoalState.ACHIEVED, GoalState.GIVEN_UP], GoalState.NOT_GOING_TO_HAPPEN_SOON),
        ([GoalState.WAITING_FOR_DATE], GoalState.WAITING_FOR_PRECONDITIONS),
        ([GoalState.BLOCKED], GoalState.NOT_GOING_TO_HAPPEN_SOON),
    ],
)
def test_handle_waiting_for_preconditions(goal, precondition_goal_states, expected_state, get_notifications):
    precondition_goals = [
        GoalFactory(state=state)
        for state in precondition_goal_states
    ]
    goal.precondition_goals.set(precondition_goals)
    listen_goal_waiting_for_worker()

    handle_waiting_for_preconditions()

    goal.refresh_from_db()
    assert goal.state == expected_state

    # notification was sent accordingly
    notifications = get_notifications()
    if expected_state == GoalState.WAITING_FOR_WORKER:
        assert len(notifications) == 1
        notification = notifications[0]
        assert notification.channel == 'goal_waiting_for_worker'
        assert notification.payload == str(goal.id)
    else:
        assert not notifications


def trigger_database_error(goal):
    Goal.objects.create(id=goal.id)  # violates unique constraint


@pytest.mark.django_db(transaction=True)
def test_transaction_error_in_goal():
    goal = schedule(trigger_database_error)
    worker_turn(timezone.now())
    goal.refresh_from_db()
    assert goal.state == GoalState.CORRUPTED
