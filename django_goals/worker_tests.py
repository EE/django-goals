import datetime
import time
from unittest import mock

import pytest
from django.db import models
from django.utils import timezone

from example_app.models import GoalRelatedModel

from .blocking_worker import listen_goal_waiting_for_worker
from .factories import GoalFactory, GoalProgressFactory
from .models import (
    AllDone, Goal, GoalState, RetryMeLater, RetryMeLaterException,
    handle_waiting_for_preconditions, handle_waiting_for_worker, schedule,
    worker_turn,
)


@pytest.mark.django_db
def test_worker_turn_noop():
    now = timezone.now()
    transitions_done = worker_turn(now)
    assert transitions_done == (0, 0)


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
    precondition_date = timezone.now() + timezone.timedelta(days=1)
    with mock.patch('django_goals.models.follow_instructions') as follow_instructions:
        follow_instructions.return_value = RetryMeLater(
            precondition_date=precondition_date,
            precondition_goals=[other_goal],
        )
        handle_waiting_for_worker()

    goal.refresh_from_db()
    assert goal.state == GoalState.WAITING_FOR_DATE
    assert goal.precondition_date == precondition_date
    assert goal.precondition_goals.get() == other_goal

    progress = goal.progress.get()
    assert progress.success


@pytest.mark.django_db
@pytest.mark.parametrize('goal', [{'state': GoalState.WAITING_FOR_WORKER}], indirect=True)
def test_handle_waiting_for_worker_retry_by_exception(goal):
    precondition_date = timezone.now() + timezone.timedelta(days=1)
    with mock.patch('django_goals.models.follow_instructions') as follow_instructions:
        follow_instructions.side_effect = RetryMeLaterException(
            precondition_date=precondition_date,
            message='asdf',
        )
        handle_waiting_for_worker()

    goal.refresh_from_db()
    assert goal.state == GoalState.WAITING_FOR_DATE
    assert goal.precondition_date == precondition_date

    progress = goal.progress.get()
    assert progress.success
    assert progress.message == 'asdf'


@pytest.mark.django_db
@pytest.mark.parametrize('goal', [{'state': GoalState.WAITING_FOR_WORKER}], indirect=True)
def test_handle_waiting_for_worker_max_progress_exceeded(goal, settings):
    settings.GOALS_MAX_PROGRESS_COUNT = 1
    with mock.patch('django_goals.models.follow_instructions') as follow_instructions:
        follow_instructions.return_value = RetryMeLater()
        handle_waiting_for_worker()
    goal.refresh_from_db()
    assert goal.state == GoalState.GIVEN_UP
    assert goal.progress.count() == 1


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
    trasitions_count, progress_count = worker_turn(timezone.now())
    assert trasitions_count == 1
    assert progress_count == 1  # we called a handler and we must report it
    goal.refresh_from_db()
    assert goal.state == GoalState.CORRUPTED
    # progress record is not created
    assert not goal.progress.exists()


def use_lots_of_memory(goal):  # pylint: disable=unused-argument
    _unused = b'x' * 1024 * 1024 * 128  # 128 MiB  # noqa
    return AllDone()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ('memory_limit', 'expected_success'),
    [
        (None, True),
        (1, False),
        (128, False),
        (256, True),
    ],
)
def test_memory_limit(settings, memory_limit, expected_success):
    settings.GOALS_MEMORY_LIMIT_MIB = memory_limit
    # simulate we have some memory allocated outside of the goal handler
    _unused = b'x' * 1024 * 1024 * 2  # 2 MiB  # noqa
    goal = schedule(use_lots_of_memory)
    worker_turn(timezone.now())
    goal.refresh_from_db()
    expected_state = GoalState.ACHIEVED if expected_success else GoalState.WAITING_FOR_DATE
    assert goal.state == expected_state
    progress = goal.progress.get()
    assert progress.success == expected_success


def take_too_long(goal):  # pylint: disable=unused-argument
    time.sleep(2)
    return AllDone()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ('time_limit', 'expected_success'),
    [
        (None, True),
        (1, False),
        (3, True),
    ],
)
def test_time_limit(settings, time_limit, expected_success):
    settings.GOALS_TIME_LIMIT_SECONDS = time_limit
    goal = schedule(take_too_long)
    worker_turn(timezone.now())
    goal.refresh_from_db()
    expected_state = GoalState.ACHIEVED if expected_success else GoalState.WAITING_FOR_DATE
    assert goal.state == expected_state
    progress = goal.progress.get()
    assert progress.success == expected_success


@pytest.mark.django_db
@pytest.mark.parametrize(
    ('days_ago', 'state', 'expect_deleted'),
    [
        (31, GoalState.ACHIEVED, True),
        (1, GoalState.ACHIEVED, False),
        (31, GoalState.WAITING_FOR_WORKER, False),
        (31, GoalState.GIVEN_UP, False),
    ],
)
def test_old_achieved_goal_is_deleted(days_ago, state, expect_deleted):
    now = timezone.now()
    goal = GoalFactory(
        state=state,
        created_at=now - timezone.timedelta(days=days_ago),
    )
    GoalProgressFactory(goal=goal)
    dependent_goal = GoalFactory(precondition_goals=[goal])

    worker_turn(now)

    exists_after = Goal.objects.filter(id=goal.id).exists()
    assert exists_after is not expect_deleted

    # dependecy is removed
    dependent_goal.refresh_from_db()
    assert dependent_goal.precondition_goals.exists() is not expect_deleted


@pytest.mark.django_db
def test_protected_old_achieved_goal():
    now = timezone.now()
    goal = GoalFactory(
        state=GoalState.ACHIEVED,
        created_at=now - timezone.timedelta(days=31),
    )
    GoalRelatedModel.objects.create(goal=goal)

    # check we can't delete the goal
    with pytest.raises(models.ProtectedError):
        goal.delete()

    # worker turn doesn't crash, but emits a warning
    with mock.patch('django_goals.models.logger.warning') as warning:
        worker_turn(now)
    assert warning.call_count == 1
    assert 'old goals' in warning.call_args[0][0]
    assert 'protected' in str(warning.call_args[0][1])


def schedule_another(goal):
    schedule(schedule_another, blocked=True)
    return AllDone()


@pytest.mark.django_db
def test_deadline_is_inherited():
    now = timezone.now()
    goal = schedule(schedule_another, deadline=now + timezone.timedelta(days=1))
    worker_turn(now)
    another_goal = Goal.objects.exclude(id=goal.id).get()
    assert another_goal.deadline == goal.deadline
