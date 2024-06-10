import pytest

from .factories import GoalFactory
from .models import GoalState, get_dependent_goal_ids


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
