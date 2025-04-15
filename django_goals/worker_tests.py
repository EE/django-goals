import datetime
import time
from unittest import mock

import pytest
from django.db import models
from django.test import override_settings
from django.utils import timezone

from example_app.models import GoalRelatedModel

from .blocking_worker import listen_goal_waiting_for_worker
from .factories import GoalFactory, GoalProgressFactory
from .models import (
    AllDone, Goal, GoalState, PreconditionFailureBehavior, PreconditionsMode,
    RetryMeLater, RetryMeLaterException, handle_waiting_for_preconditions,
    handle_waiting_for_worker, schedule, thread_local, worker, worker_turn,
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
    dependent_goal = GoalFactory(
        waiting_for_count=42,
        precondition_goals=[goal],
    )

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

    # dependent goal is updated
    dependent_goal.refresh_from_db()
    assert dependent_goal.waiting_for_count == 41


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
@pytest.mark.parametrize('goal', [{
    'state': GoalState.WAITING_FOR_WORKER,
    'waiting_for_count': 0,
}], indirect=True)
def test_handle_waiting_for_worker_retry(goal):
    other_goals = GoalFactory.create_batch(2, state=GoalState.WAITING_FOR_WORKER)
    failed_goals = GoalFactory.create_batch(1, state=GoalState.NOT_GOING_TO_HAPPEN_SOON)
    precondition_date = timezone.now() + timezone.timedelta(days=1)
    with mock.patch('django_goals.models.follow_instructions') as follow_instructions:
        follow_instructions.return_value = RetryMeLater(
            precondition_date=precondition_date,
            precondition_goals=other_goals + failed_goals,
        )
        handle_waiting_for_worker()

    goal.refresh_from_db()
    assert goal.state == GoalState.WAITING_FOR_DATE
    assert goal.precondition_date == precondition_date
    assert set(goal.precondition_goals.all()) == set(other_goals + failed_goals)
    assert goal.waiting_for_count == 3
    assert goal.waiting_for_failed_count == 1

    progress = goal.progress.get()
    assert progress.success


@pytest.mark.django_db
@pytest.mark.parametrize('goal', [{
    'state': GoalState.WAITING_FOR_WORKER,
    'waiting_for_count': 0,
}], indirect=True)
@pytest.mark.parametrize('already_present', [True, False])
def test_handle_waiting_for_worker_retry_precond_already_present(goal, already_present):
    precondition_goal = GoalFactory(state=GoalState.ACHIEVED)
    if already_present:
        goal.precondition_goals.add(precondition_goal)
    with mock.patch('django_goals.models.follow_instructions') as follow_instructions:
        follow_instructions.return_value = RetryMeLater(
            precondition_goals=[precondition_goal],
        )
        handle_waiting_for_worker()

    goal.refresh_from_db()
    assert goal.state == GoalState.WAITING_FOR_DATE
    assert goal.precondition_goals.get() == precondition_goal
    assert goal.waiting_for_count == 0


@pytest.mark.django_db
@pytest.mark.parametrize('goal', [{
    'state': GoalState.WAITING_FOR_WORKER,
    'handler': 'os.path.join',
}], indirect=True)
def test_handle_waiting_for_worker_retry_by_exception(goal):
    precondition_date = timezone.now() + timezone.timedelta(days=1)
    with mock.patch('os.path.join') as handler:
        handler.side_effect = RetryMeLaterException(
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
@pytest.mark.parametrize('goal', [{
    'state': GoalState.WAITING_FOR_WORKER,
    'handler': 'os.path.join',
    'preconditions_mode': PreconditionsMode.ANY,
    'waiting_for_count': 0,
    'waiting_for_not_achieved_count': 1,
}], indirect=True)
@pytest.mark.parametrize(
    ('precondition_goals', 'expected_waiting_for_count'),
    [
        ([], 1),
        (None, 0),  # means "retry immediately"
    ],
)
def test_handle_waiting_for_worker_any_mode_retry(goal, precondition_goals, expected_waiting_for_count):
    """
    In ANY preconditions mode, we should increment waiting_for_count
    even if RetryMeLater does not contain any new goals.
    """
    achieved_precond = GoalFactory(state=GoalState.ACHIEVED)
    waiting_precond = GoalFactory(state=GoalState.WAITING_FOR_DATE)
    goal.precondition_goals.add(achieved_precond, waiting_precond)

    with mock.patch('os.path.join') as handler:
        handler.return_value = RetryMeLater(precondition_goals=precondition_goals)
        handle_waiting_for_worker()

    goal.refresh_from_db()
    assert goal.state == GoalState.WAITING_FOR_DATE
    assert goal.waiting_for_count == expected_waiting_for_count
    assert goal.waiting_for_not_achieved_count == 1


@pytest.mark.django_db
@pytest.mark.parametrize(
    ('precond_state', 'expected_waiting_for_count'),
    [
        (GoalState.ACHIEVED, 0),
        (GoalState.WAITING_FOR_DATE, 1),
    ],
)
def test_handle_waiting_for_worker_any_mode_retry_without_goals(precond_state, expected_waiting_for_count):
    """
    In ANY mode, retry with precondition_goals=[] should wait for any not-achieved precondition.
    If all preconditions are achieved, we retry immediately.
    """
    precond = GoalFactory(state=precond_state)
    goal = GoalFactory(
        state=GoalState.WAITING_FOR_WORKER,
        handler='os.path.join',
        preconditions_mode=PreconditionsMode.ANY,
        waiting_for_count=0,
        waiting_for_not_achieved_count=1 if precond_state == GoalState.WAITING_FOR_DATE else 0,
    )
    goal.precondition_goals.add(precond)

    with mock.patch('os.path.join') as handler:
        handler.return_value = RetryMeLater(precondition_goals=[])
        handle_waiting_for_worker()

    goal.refresh_from_db()
    assert goal.state == GoalState.WAITING_FOR_DATE
    assert goal.waiting_for_count == expected_waiting_for_count


@pytest.mark.django_db
@pytest.mark.parametrize('goal', [{
    'state': GoalState.WAITING_FOR_WORKER,
    'handler': 'os.path.join',
    'preconditions_mode': PreconditionsMode.ANY,
    'waiting_for_count': 0,
    'waiting_for_not_achieved_count': 1,
}], indirect=True)
def test_handle_waiting_for_worker_any_mode_all_done(goal):
    """
    In ANY preconditions mode, we can achieve the goal even if some preconditions are not met.
    """
    achieved_precond = GoalFactory(state=GoalState.ACHIEVED)
    waiting_precond = GoalFactory(state=GoalState.WAITING_FOR_DATE)
    goal.precondition_goals.add(achieved_precond, waiting_precond)

    with mock.patch('os.path.join') as handler:
        handler.return_value = AllDone()
        handle_waiting_for_worker()

    goal.refresh_from_db()
    assert goal.state == GoalState.ACHIEVED
    assert goal.waiting_for_count == 0
    assert goal.waiting_for_not_achieved_count == 1


@pytest.mark.django_db
@pytest.mark.parametrize('goal', [{
    'state': GoalState.WAITING_FOR_WORKER,
    'handler': 'os.path.join',
    'preconditions_mode': PreconditionsMode.ANY,
    'waiting_for_count': 0,
    'waiting_for_not_achieved_count': 1,
}], indirect=True)
@pytest.mark.parametrize(
    ('local_precond_state', 'expected_waiting_for_count'),
    [
        (GoalState.ACHIEVED, 1),
        (GoalState.WAITING_FOR_DATE, 0),
    ],
)
def test_handle_waiting_for_worker_any_mode_retry_with_stale_goal_state(goal, local_precond_state, expected_waiting_for_count):
    """
    In ANY preconditions mode, we retry immediately if the precondition goal was achieved during handler execution.
    """
    old_precond = GoalFactory(state=GoalState.WAITING_FOR_DATE)
    goal.precondition_goals.add(old_precond)

    new_precond = GoalFactory(state=GoalState.ACHIEVED)
    new_precond.state = local_precond_state  # simulate we have stale state

    with mock.patch('os.path.join') as handler:
        handler.return_value = RetryMeLater(precondition_goals=[new_precond])
        handle_waiting_for_worker()

    goal.refresh_from_db()
    assert goal.state == GoalState.WAITING_FOR_DATE
    assert goal.waiting_for_count == expected_waiting_for_count
    assert goal.waiting_for_not_achieved_count == 1


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


@pytest.mark.django_db
@pytest.mark.parametrize('goal', [{'state': GoalState.WAITING_FOR_WORKER}], indirect=True)
def test_handle_waiting_for_worker_max_progress_exceeded(goal, settings):
    dependent_goal = GoalFactory(
        waiting_for_failed_count=122,
        precondition_goals=[goal],
    )

    settings.GOALS_MAX_PROGRESS_COUNT = 1
    with mock.patch('django_goals.models.follow_instructions') as follow_instructions:
        follow_instructions.return_value = RetryMeLater()
        handle_waiting_for_worker()
    goal.refresh_from_db()
    assert goal.state == GoalState.GIVEN_UP
    assert goal.progress.count() == 1

    # dependent goal is updated
    dependent_goal.refresh_from_db()
    assert dependent_goal.waiting_for_failed_count == 123


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ('goal', 'expected_state'),
    [
        ({
            'state': GoalState.WAITING_FOR_PRECONDITIONS,
            'waiting_for_count': 0,
        }, GoalState.WAITING_FOR_WORKER),
        ({
            'state': GoalState.WAITING_FOR_PRECONDITIONS,
            'waiting_for_count': 1,
        }, GoalState.WAITING_FOR_PRECONDITIONS),
        ({
            'state': GoalState.WAITING_FOR_PRECONDITIONS,
            'waiting_for_count': -1,
        }, GoalState.WAITING_FOR_WORKER),
        ({
            'state': GoalState.WAITING_FOR_DATE,
            'waiting_for_count': 0,
        }, GoalState.WAITING_FOR_DATE),
    ],
    indirect=['goal'],
)
def test_handle_waiting_for_preconditions(goal, expected_state, get_notifications):
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
