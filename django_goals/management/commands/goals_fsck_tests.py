import pytest
from django.core.management import call_command

from django_goals.factories import GoalFactory
from django_goals.models import (
    GoalState, PreconditionFailureBehavior, PreconditionsMode,
)


@pytest.mark.django_db
def test_goals_fsck(goal):
    goal.precondition_goals.add(
        GoalFactory(state=GoalState.ACHIEVED),
        GoalFactory(state=GoalState.WAITING_FOR_WORKER),
        GoalFactory(state=GoalState.NOT_GOING_TO_HAPPEN_SOON),
    )
    call_command('goals_fsck')
    goal.refresh_from_db()
    assert goal.waiting_for_count == 2
    assert goal.waiting_for_not_achieved_count == 2
    assert goal.waiting_for_failed_count == 1


@pytest.mark.django_db
@pytest.mark.parametrize('goal', [{'preconditions_mode': PreconditionsMode.ANY}], indirect=True)
def test_goals_fsck_any_mode(goal):
    goal.precondition_goals.add(
        GoalFactory(state=GoalState.ACHIEVED),
        GoalFactory(state=GoalState.WAITING_FOR_WORKER),
        GoalFactory(state=GoalState.WAITING_FOR_WORKER),
        GoalFactory(state=GoalState.NOT_GOING_TO_HAPPEN_SOON),
    )
    call_command('goals_fsck')
    goal.refresh_from_db()
    assert goal.waiting_for_count == 1
    assert goal.waiting_for_not_achieved_count == 3
    assert goal.waiting_for_failed_count == 1


@pytest.mark.django_db
@pytest.mark.parametrize('goal', [{
    'precondition_failure_behavior': PreconditionFailureBehavior.PROCEED,
}], indirect=True)
def test_goals_fsck_proceed_mode(goal):
    goal.precondition_goals.add(
        GoalFactory(state=GoalState.ACHIEVED),
        GoalFactory(state=GoalState.WAITING_FOR_WORKER),
        GoalFactory(state=GoalState.NOT_GOING_TO_HAPPEN_SOON),
    )
    call_command('goals_fsck')
    goal.refresh_from_db()
    assert goal.waiting_for_count == 1
    assert goal.waiting_for_not_achieved_count == 2
    assert goal.waiting_for_failed_count == 1
