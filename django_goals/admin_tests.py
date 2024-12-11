import pytest
from django.urls import reverse

from django_goals.models import GoalState


@pytest.mark.django_db
@pytest.mark.parametrize(
    ('goal', 'action', 'expected_state'),
    [
        (
            {'state': GoalState.WAITING_FOR_WORKER},
            'block',
            GoalState.BLOCKED,
        ),
        (
            {'state': GoalState.BLOCKED},
            'unblock_retry',
            GoalState.WAITING_FOR_DATE,
        ),
        (
            {'state': GoalState.GIVEN_UP},
            'unblock_retry',
            GoalState.WAITING_FOR_DATE,
        ),
        (
            {'state': GoalState.NOT_GOING_TO_HAPPEN_SOON},
            'unblock_retry',
            GoalState.WAITING_FOR_DATE,
        ),
    ],
    indirect=['goal'],
)
def test_state_actions(admin_client, goal, action, expected_state):
    response = admin_client.post(reverse(
        'admin:django_goals_goal_actions',
        args=[goal.pk, action],
    ))
    assert response.status_code == 302
    goal.refresh_from_db()
    assert goal.state == expected_state
