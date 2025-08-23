import contextlib
import os
import subprocess

import pytest
from django.core.management import call_command

from .goals_threaded_worker_tests import get_current_database_url


@pytest.mark.django_db(transaction=True)
def test_no_smoke():
    with worker_subprocess():
        call_command('set_goal')


@contextlib.contextmanager
def worker_subprocess():
    with subprocess.Popen(
        ['python', 'manage.py', 'goals_threaded_worker'],
        env={
            **os.environ,
            'DATABASE_URL': get_current_database_url(),
        },
    ) as p:
        try:
            yield
        finally:
            p.terminate()
            pass
