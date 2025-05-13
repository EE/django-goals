from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils.timezone import now

from django_goals.models import AllDone, GoalState, schedule


def achieve(goal):
    return AllDone()


@pytest.mark.django_db(transaction=True)
def test_achieving_goals():
    goal = schedule(achieve)
    call_command('goals_threaded_worker', threads=['2'], once=True)
    goal.refresh_from_db()
    assert goal.state == GoalState.ACHIEVED


@pytest.mark.parametrize(
    ('threads_spec', 'expected_goal_state'),
    [
        (['1:1m'], GoalState.WAITING_FOR_WORKER),
        (['1:1m', '1:2d'], GoalState.ACHIEVED),
        (['1:2d', '1:1m'], GoalState.ACHIEVED),
        (['1:0s'], GoalState.WAITING_FOR_WORKER),
    ],
)
@pytest.mark.django_db(transaction=True)
def test_deadline_horizon(threads_spec, expected_goal_state):
    """ Worker should pick up goals with deadline within the deadline horizon """
    goal = schedule(achieve, deadline=now() + timedelta(days=1))
    call_command('goals_threaded_worker', threads=threads_spec, once=True)
    goal.refresh_from_db()
    assert goal.state == expected_goal_state
