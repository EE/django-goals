import pytest
from django.core.management import call_command

from django_goals.models import RetryMeLater, schedule


def pursue(goal):
    return RetryMeLater()


@pytest.mark.django_db
def test_max_transitions():
    goal = schedule(pursue)
    call_command('goals_busy_worker', max_transitions=10)
    goal.refresh_from_db()
    progress_count = goal.progress.count()
    assert 0 < progress_count
    assert progress_count <= 10
