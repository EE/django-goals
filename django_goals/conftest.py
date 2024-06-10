import pytest

from .factories import GoalFactory


@pytest.fixture(name='goal')
def goal_fixture(request):
    return GoalFactory(
        **getattr(request, 'param', {}),
    )
