import datetime
from typing import Any, Callable
from unittest import mock

import pytest
from django.utils import timezone

from .factories import GoalFactory
from .models import (
    AllDone, Goal, GoalState, PreconditionsMode, RetryMeLater,
    RetryMeLaterException, handle_waiting_for_preconditions,
    handle_waiting_for_worker,
)
from .notifications import listen_goal_waiting_for_worker


@pytest.mark.django_db
@pytest.mark.parametrize('goal', [
    {'state': GoalState.WAITING_FOR_DATE},
    {'state': GoalState.WAITING_FOR_PRECONDITIONS},
    {'state': GoalState.WAITING_FOR_WORKER},
    {'state': GoalState.ACHIEVED},
    {'state': GoalState.GIVEN_UP},
], indirect=True)
def test_handle_waiting_for_worker_return_value(goal: Goal) -> None:
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
def test_handle_waiting_for_worker_success(goal: Goal) -> None:
    dependent_goal = GoalFactory.create(
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
    assert progress.time_taken is not None
    assert progress.time_taken > datetime.timedelta(0)

    # dependent goal is updated
    dependent_goal.refresh_from_db()
    assert dependent_goal.waiting_for_count == 41


@pytest.mark.django_db
@pytest.mark.parametrize('goal', [{
    'state': GoalState.WAITING_FOR_WORKER,
    'precondition_date': timezone.now() - datetime.timedelta(days=1),
}], indirect=True)
def test_handle_waiting_for_worker_failure(goal: Goal) -> None:
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
def test_handle_waiting_for_worker_retry(goal: Goal) -> None:
    other_goals = GoalFactory.create_batch(2, state=GoalState.WAITING_FOR_WORKER)
    failed_goals = GoalFactory.create_batch(1, state=GoalState.NOT_GOING_TO_HAPPEN_SOON)
    precondition_date = timezone.now() + datetime.timedelta(days=1)
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
def test_handle_waiting_for_worker_retry_precond_already_present(goal: Goal, already_present: bool) -> None:
    precondition_goal = GoalFactory.create(state=GoalState.ACHIEVED)
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
def test_handle_waiting_for_worker_retry_by_exception(goal: Goal) -> None:
    precondition_date = timezone.now() + datetime.timedelta(days=1)
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
def test_handle_waiting_for_worker_any_mode_retry(goal: Goal, precondition_goals: list[Goal] | None, expected_waiting_for_count: int) -> None:
    """
    In ANY preconditions mode, we should increment waiting_for_count
    even if RetryMeLater does not contain any new goals.
    """
    achieved_precond = GoalFactory.create(state=GoalState.ACHIEVED)
    waiting_precond = GoalFactory.create(state=GoalState.WAITING_FOR_DATE)
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
def test_handle_waiting_for_worker_any_mode_retry_without_goals(precond_state: GoalState, expected_waiting_for_count: int) -> None:
    """
    In ANY mode, retry with precondition_goals=[] should wait for any not-achieved precondition.
    If all preconditions are achieved, we retry immediately.
    """
    precond = GoalFactory.create(state=precond_state)
    goal = GoalFactory.create(
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
def test_handle_waiting_for_worker_any_mode_all_done(goal: Goal) -> None:
    """
    In ANY preconditions mode, we can achieve the goal even if some preconditions are not met.
    """
    achieved_precond = GoalFactory.create(state=GoalState.ACHIEVED)
    waiting_precond = GoalFactory.create(state=GoalState.WAITING_FOR_DATE)
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
def test_handle_waiting_for_worker_any_mode_retry_with_stale_goal_state(goal: Goal, local_precond_state: GoalState, expected_waiting_for_count: int) -> None:
    """
    In ANY preconditions mode, we retry immediately if the precondition goal was achieved during handler execution.
    """
    old_precond = GoalFactory.create(state=GoalState.WAITING_FOR_DATE)
    goal.precondition_goals.add(old_precond)

    new_precond = GoalFactory.create(state=GoalState.ACHIEVED)
    new_precond.state = local_precond_state  # simulate we have stale state

    with mock.patch('os.path.join') as handler:
        handler.return_value = RetryMeLater(precondition_goals=[new_precond])
        handle_waiting_for_worker()

    goal.refresh_from_db()
    assert goal.state == GoalState.WAITING_FOR_DATE
    assert goal.waiting_for_count == expected_waiting_for_count
    assert goal.waiting_for_not_achieved_count == 1


@pytest.mark.django_db
@pytest.mark.parametrize('goal', [{'state': GoalState.WAITING_FOR_WORKER}], indirect=True)
def test_handle_waiting_for_worker_max_progress_exceeded(goal: Goal, settings: object) -> None:
    dependent_goal = GoalFactory.create(
        waiting_for_failed_count=122,
        precondition_goals=[goal],
    )

    settings.GOALS_MAX_PROGRESS_COUNT = 1  # type: ignore[attr-defined]
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
def test_handle_waiting_for_preconditions(goal: Goal, expected_state: GoalState, get_notifications: Callable[[], list[Any]]) -> None:
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
