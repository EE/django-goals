import pytest

from .factories import TaskFactory


@pytest.fixture(name='task')
def fixture_task(request):
    return TaskFactory(
        **getattr(request, 'param', {}),
    )
