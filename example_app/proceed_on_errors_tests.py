import pytest
from django.test import override_settings

from django_goals.busy_worker import worker
from django_goals.models import PreconditionFailureBehavior, schedule

from .proceed_on_errors import ErrorsBatch, do_batch


@pytest.mark.django_db
@override_settings(GOALS_GIVE_UP_AT=1)
def test_proceed_on_error():
    batch = ErrorsBatch.objects.create(
        desired=20,
        processed_goal=schedule(
            do_batch,
            precondition_failure_behavior=PreconditionFailureBehavior.PROCEED,
        ),
    )
    worker(once=True)
    batch.refresh_from_db()
    assert batch.spawned == 20
    assert batch.succeeded + batch.failed == 20
    assert 1 <= batch.succeeded <= 19
