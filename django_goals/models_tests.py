import datetime

import pytest

from .factories import GoalFactory
from .models import (
    GoalState, PreconditionFailureBehavior, PreconditionsMode,
    handle_unblocked_goals, schedule, unblock_retry_goal,
)


@pytest.mark.django_db
@pytest.mark.parametrize(
    'goal',
    [{'state': GoalState.GIVEN_UP}],
    indirect=True,
)
def test_retry(goal):
    unblock_retry_goal(goal.id)
    goal.refresh_from_db()
    assert goal.state == GoalState.WAITING_FOR_DATE


@pytest.mark.django_db
@pytest.mark.parametrize('goal', [{'state': GoalState.GIVEN_UP}], indirect=True)
def test_retry_dependent_on(goal):
    next_goal = GoalFactory(
        state=GoalState.NOT_GOING_TO_HAPPEN_SOON,
        precondition_goals=[goal],
    )
    unblock_retry_goal(goal.id)
    handle_unblocked_goals()
    next_goal.refresh_from_db()
    assert next_goal.state == GoalState.WAITING_FOR_DATE


def noop(goal):  # pylint: disable=unused-argument
    pass


@pytest.mark.django_db
def test_schedule_updates_deadline():
    now = datetime.datetime(2024, 11, 6, 11, 41, 0, tzinfo=datetime.timezone.utc)
    goal_a = GoalFactory(deadline=now)
    goal_b = GoalFactory(precondition_goals=[goal_a])
    schedule(
        noop,
        deadline=now - datetime.timedelta(minutes=1),
        precondition_goals=[goal_b],
    )
    goal_a.refresh_from_db()
    assert goal_a.deadline == now - datetime.timedelta(minutes=1)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ('goal', 'expected_waiting_for', 'expected_waiting_for_failed_count'),
    [
        ({'state': GoalState.WAITING_FOR_WORKER}, 1, 0),
        ({'state': GoalState.ACHIEVED}, 0, 0),
        ({'state': GoalState.GIVEN_UP}, 1, 1),
        ({'state': GoalState.NOT_GOING_TO_HAPPEN_SOON}, 1, 1),
    ],
    indirect=['goal'],
)
@pytest.mark.parametrize('mode', [PreconditionsMode.ALL, PreconditionsMode.ANY])
def test_schedule_updates_waiting_for_count(goal, expected_waiting_for, expected_waiting_for_failed_count, mode):
    next_goal = schedule(noop, precondition_goals=[goal], preconditions_mode=mode)
    assert next_goal.waiting_for_count == expected_waiting_for
    assert next_goal.waiting_for_failed_count == expected_waiting_for_failed_count
    assert next_goal.preconditions_mode == mode


@pytest.mark.django_db
@pytest.mark.parametrize(
    'failure_mode',
    [
        PreconditionFailureBehavior.PROCEED,
        PreconditionFailureBehavior.BLOCK,
    ],
)
def test_schedule_any_mode_caps_waiting_for(failure_mode):
    preconds = GoalFactory.create_batch(2, state=GoalState.WAITING_FOR_WORKER)
    next_goal = schedule(
        noop,
        precondition_goals=preconds,
        preconditions_mode=PreconditionsMode.ANY,
        precondition_failure_behavior=failure_mode,
    )
    assert next_goal.waiting_for_count == 1
    assert next_goal.precondition_goals.count() == 2


@pytest.mark.django_db
@pytest.mark.parametrize(
    ('failure_mode', 'expected_waiting_for_count'),
    [
        (PreconditionFailureBehavior.PROCEED, 0),
        (PreconditionFailureBehavior.BLOCK, 1),
    ],
)
def test_schedule_failed_precond(failure_mode, expected_waiting_for_count):
    failed_goal = GoalFactory(
        state=GoalState.GIVEN_UP,
    )
    goal = schedule(
        noop,
        precondition_goals=[failed_goal],
        precondition_failure_behavior=failure_mode,
    )
    assert goal.state == GoalState.WAITING_FOR_PRECONDITIONS
    assert goal.waiting_for_count == expected_waiting_for_count
    assert goal.waiting_for_failed_count == 1
    assert goal.waiting_for_not_achieved_count == 1


@pytest.mark.django_db
@pytest.mark.parametrize('blocked', [True, False])
def test_schedule_blocked(blocked):
    goal = GoalFactory(state=GoalState.WAITING_FOR_WORKER)
    next_goal = schedule(noop, precondition_goals=[goal], blocked=blocked)
    assert next_goal.state == (
        GoalState.BLOCKED if blocked
        else GoalState.WAITING_FOR_PRECONDITIONS
    )
