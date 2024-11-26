import pytest
from django.core.management import call_command

from django_goals.models import AllDone, GoalState, schedule


def achieve(goal):
    return AllDone()


@pytest.mark.django_db(transaction=True)
def test_achieving_goals():
    goal = schedule(achieve)
    call_command('goals_threaded_worker', threads=2, once=True)
    goal.refresh_from_db()
    assert goal.state == GoalState.ACHIEVED
