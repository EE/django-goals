import pytest

from .blocking_worker import listen_goal_waiting_for_worker
from .models import schedule


def noop():
    pass


@pytest.mark.django_db(transaction=True)
def test_schedule_notifies(get_notifications):
    listen_goal_waiting_for_worker()

    goal = schedule(noop)

    notifications = get_notifications()
    assert len(notifications) == 1
    notification = notifications[0]
    assert notification.channel == 'goal_waiting_for_worker'
    assert notification.payload == str(goal.id)
