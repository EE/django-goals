from unittest import mock

import pytest
from django.db import connection

from .factories import GoalFactory


@pytest.fixture(name='goal')
def goal_fixture(request):
    return GoalFactory(
        **getattr(request, 'param', {}),
    )


@pytest.fixture(name='get_notifications')
def get_notifications_fixture():
    handler = mock.Mock()
    connection.ensure_connection()
    pg_conn = connection.connection
    pg_conn.add_notify_handler(handler)

    def _get_notifications():
        return [
            call[0][0]
            for call in handler.call_args_list
        ]
    return _get_notifications
