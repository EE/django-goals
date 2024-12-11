import datetime

import pytest

from .factories import GoalFactory
from .models import (
    GoalState, handle_unblocked_goals, schedule, unblock_retry_goal,
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
def test_schedule_updates_waiting_for_count(goal, expected_waiting_for, expected_waiting_for_failed_count):
    next_goal = schedule(noop, precondition_goals=[goal])
    assert next_goal.waiting_for_count == expected_waiting_for
    assert next_goal.waiting_for_failed_count == expected_waiting_for_failed_count


@pytest.mark.django_db
@pytest.mark.parametrize('blocked', [True, False])
def test_schedule_blocked(blocked):
    goal = GoalFactory(state=GoalState.WAITING_FOR_WORKER)
    next_goal = schedule(noop, precondition_goals=[goal], blocked=blocked)
    assert next_goal.state == (
        GoalState.BLOCKED if blocked
        else GoalState.WAITING_FOR_PRECONDITIONS
    )
