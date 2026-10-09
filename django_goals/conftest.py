import threading
from contextlib import AbstractContextManager, contextmanager
from typing import Any, Callable, Iterator
from unittest import mock

import pytest
from django.db import connection, transaction

from .factories import GoalFactory
from .models import Goal


@pytest.fixture(name='goal')
def goal_fixture(request: pytest.FixtureRequest) -> Goal:
    return GoalFactory.create(
        **getattr(request, 'param', {}),
    )


@pytest.fixture(name='get_notifications')
def get_notifications_fixture() -> Callable[[], list[Any]]:
    handler = mock.Mock()
    connection.ensure_connection()
    pg_conn = connection.connection
    pg_conn.add_notify_handler(handler)

    def _get_notifications() -> list[Any]:
        return [
            call[0][0]
            for call in handler.call_args_list
        ]
    return _get_notifications


@pytest.fixture(name='pursued')
def pursued_fixture() -> Callable[[Goal], AbstractContextManager[None]]:
    """
    Keeps a goal locked like a worker pursuing it, in another transaction.
    Queries waiting for it fail instead.
    """
    @contextmanager
    def _pursued(goal: Goal) -> Iterator[None]:
        locked = threading.Event()
        release = threading.Event()

        def pursue() -> None:
            with transaction.atomic():
                Goal.objects.select_for_update(no_key=True).get(id=goal.id)
                locked.set()
                release.wait(10)
            connection.close()

        worker = threading.Thread(target=pursue)
        worker.start()
        assert locked.wait(10)
        with connection.cursor() as cursor:
            cursor.execute("SET lock_timeout = '1s'")
        try:
            yield
        finally:
            release.set()
            worker.join()
            with connection.cursor() as cursor:
                cursor.execute('RESET lock_timeout')
    return _pursued
