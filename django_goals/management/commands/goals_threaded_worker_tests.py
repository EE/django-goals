import os
import subprocess
from datetime import timedelta
from urllib.parse import urlparse, urlunparse

import pytest
from django.core.management import call_command
from django.db import connection
from django.utils.timezone import now

from django_goals.models import AllDone, GoalState, schedule
from django_goals.pickups import GoalPickup


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


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('should_commit', [True, False])
def test_exit_goal(pytestconfig, should_commit):
    """We test (in a separate process) a goal that exits and does not commit."""
    goal = schedule(achieve if should_commit else exit_goal)
    subprocess.run(
        ['python', 'manage.py', 'goals_threaded_worker', '--once'],
        env={
            **os.environ,
            'DATABASE_URL': get_current_database_url(),
        },
        cwd=pytestconfig.rootdir,
    )
    assert GoalPickup.objects.filter(goal=goal).exists() is not should_commit


def exit_goal(goal):
    os._exit(0)  # Exit the process immediately


def get_current_database_url():
    current_database_url = os.environ.get('DATABASE_URL')
    parsed = urlparse(current_database_url)
    parsed = parsed._replace(path=connection.settings_dict['NAME'])
    return urlunparse(parsed)
