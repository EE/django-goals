import time

import pytest
from django.db import connection

from .factories import GoalFactory
from .pickups import GoalPickup, PickupMonitorThread


@pytest.mark.django_db(transaction=True)
def test_pickup_monitor_reconnects() -> None:
    goal = GoalFactory.create()
    monitor = PickupMonitorThread()
    monitor.start()
    monitor.pickup(goal.id)
    for _ in range(100):  # until the monitor has connected
        if GoalPickup.objects.filter(goal=goal).exists():
            break
        time.sleep(0.01)
    with connection.cursor() as cursor:  # like a database restart
        cursor.execute(
            'SELECT pg_terminate_backend(pid) FROM pg_stat_activity '
            'WHERE datname = current_database() AND pid <> pg_backend_pid()',
        )
    monitor.pickup(goal.id)  # lost
    monitor.pickup(goal.id)
    monitor.shutdown()
    monitor.join()
    assert GoalPickup.objects.filter(goal=goal).count() == 2
