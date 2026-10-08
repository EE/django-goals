import io

import pytest
from django.core.management import call_command

from django_goals.models import Goal


@pytest.mark.django_db(transaction=True)
def test_no_smoke() -> None:
    call_command('goals_perftest', 'butterfly', '2', stdout=io.StringIO())
    assert not Goal.objects.exists()
