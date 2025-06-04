import time
from unittest import mock

import pytest
from django.db import models
from django.test import override_settings
from django.utils import timezone

from django_goals.busy_worker import worker, worker_turn
from django_goals.models import (
    AllDone, Goal, GoalState, PreconditionFailureBehavior, schedule,
    thread_local,
)
from example_app.models import GoalRelatedModel

from .factories import GoalFactory, GoalProgressFactory


@pytest.mark.django_db
def test_worker_turn_noop():
    now = timezone.now()
    transitions_done = worker_turn(now)
    assert transitions_done == (0, 0)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ('failure_mode', 'expected_state', 'progress_count'),
    [
        (PreconditionFailureBehavior.PROCEED, GoalState.ACHIEVED, 1),
        (PreconditionFailureBehavior.BLOCK, GoalState.NOT_GOING_TO_HAPPEN_SOON, 0),
    ],
)
@override_settings(GOALS_GIVE_UP_AT=1)
def test_proceed_at_failure_mode_do_work(failure_mode, expected_state, progress_count):
    precond = schedule(fail)
    goal = schedule(
        noop,
        precondition_goals=[precond],
        precondition_failure_behavior=failure_mode,
    )
    worker(once=True)
    precond.refresh_from_db()
    assert precond.state == GoalState.GIVEN_UP
    goal.refresh_from_db()
    assert goal.state == expected_state
    assert goal.progress.count() == progress_count


def noop(goal):  # pylint: disable=unused-argument
    return AllDone()


def fail(goal):  # pylint: disable=unused-argument
    raise Exception('I failed!')


def trigger_database_error(goal):
    Goal.objects.create(id=goal.id)  # violates unique constraint


@pytest.mark.django_db(transaction=True)
def test_transaction_error_in_goal():
    goal = schedule(trigger_database_error)
    trasitions_count, progress_count = worker_turn(timezone.now())
    assert trasitions_count == 1
    assert progress_count == 1  # we called a handler and we must report it
    goal.refresh_from_db()
    assert goal.state == GoalState.WAITING_FOR_DATE
    # progress record is created
    assert goal.progress.exists()
    assert thread_local.current_goal is None


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
