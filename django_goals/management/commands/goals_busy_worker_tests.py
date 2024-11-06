import pytest
from django.core.management import call_command

from django_goals.models import (
    AllDone, Goal, GoalProgress, GoalState, RetryMeLater, schedule,
)


def pursue(goal):
    return RetryMeLater()


@pytest.mark.django_db
def test_max_transitions():
    goal = schedule(pursue)
    call_command('goals_busy_worker', max_progress_count=10)
    goal.refresh_from_db()
    progress_count = goal.progress.count()
    assert progress_count == 10


def achieve(goal):
    return AllDone()


@pytest.mark.django_db
def test_max_transitions_in_single_turn():
    for _ in range(10):
        schedule(achieve)
    call_command('goals_busy_worker', max_progress_count=5)
    assert Goal.objects.filter(state=GoalState.ACHIEVED).count() == 5
    assert GoalProgress.objects.count() == 5
