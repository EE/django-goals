import pytest
from django.core.management import call_command

from django_goals.factories import GoalFactory
from django_goals.models import GoalState


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
    assert goal.waiting_for_failed_count == 1
