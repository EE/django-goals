import datetime

import pytest

from .factories import GoalFactory
from .models import (
    GoalState, get_dependent_goal_ids, handle_waiting_for_worker_guarded,
    schedule,
)


@pytest.mark.django_db
@pytest.mark.parametrize(
    'goal',
    [{'state': GoalState.GIVEN_UP}],
    indirect=True,
)
def test_retry(goal):
    goal.retry()
    assert goal.state == GoalState.WAITING_FOR_DATE


@pytest.mark.django_db
@pytest.mark.parametrize('goal', [{'state': GoalState.GIVEN_UP}], indirect=True)
def test_retry_dependent_on(goal):
    next_goal = GoalFactory(
        state=GoalState.NOT_GOING_TO_HAPPEN_SOON,
        precondition_goals=[goal],
    )
    altered_ids = goal.retry()
    assert next_goal.id in altered_ids
    next_goal.refresh_from_db()
    assert next_goal.state == GoalState.WAITING_FOR_DATE


@pytest.mark.django_db
def test_get_dependent_goal_ids(goal):
    next_goal = GoalFactory(precondition_goals=[goal])
    assert next_goal.id in get_dependent_goal_ids([goal.pk])
    assert goal.id not in get_dependent_goal_ids([next_goal.pk])


def noop(goal):  # pylint: disable=unused-argument
    pass


@pytest.mark.django_db
@pytest.mark.parametrize('goal', [{
    'state': GoalState.WAITING_FOR_WORKER,
    'handler': 'django_goals.models_tests.noop',
}], indirect=True)
def test_handle_waiting_for_worker_guarded_updates_dependent_goals(goal):
    next_goal = GoalFactory(
        state=GoalState.WAITING_FOR_PRECONDITIONS,
        precondition_goals=[goal],
        waiting_for_count=1,
    )
    handle_waiting_for_worker_guarded()
    next_goal.refresh_from_db()
    assert next_goal.state == GoalState.WAITING_FOR_WORKER
    assert next_goal.waiting_for_count == 0


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
    ('goal', 'expected_waiting_for'),
    [
        ({'state': GoalState.WAITING_FOR_WORKER}, 1),
        ({'state': GoalState.ACHIEVED}, 0),
    ],
    indirect=['goal'],
)
def test_schedule_updates_waiting_for_count(goal, expected_waiting_for):
    next_goal = schedule(noop, precondition_goals=[goal])
    assert next_goal.waiting_for_count == expected_waiting_for
