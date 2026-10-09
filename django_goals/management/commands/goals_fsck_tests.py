from contextlib import AbstractContextManager
from typing import Callable

import pytest
from django.core.management import call_command

from django_goals.factories import GoalFactory
from django_goals.models import (
    Goal, GoalState, PreconditionFailureBehavior, PreconditionsMode,
)


@pytest.mark.django_db
def test_goals_fsck(goal: Goal) -> None:
    goal.precondition_goals.add(
        GoalFactory.create(state=GoalState.ACHIEVED),
        GoalFactory.create(state=GoalState.WAITING_FOR_WORKER),
        GoalFactory.create(state=GoalState.NOT_GOING_TO_HAPPEN_SOON),
    )
    call_command('goals_fsck')
    goal.refresh_from_db()
    assert goal.waiting_for_count == 2
    assert goal.waiting_for_not_achieved_count == 2
    assert goal.waiting_for_failed_count == 1


@pytest.mark.django_db
@pytest.mark.parametrize(
    ('precondition_states', 'waiting_for_count', 'expected_waiting_for_count'),
    [
        ([GoalState.WAITING_FOR_WORKER, GoalState.NOT_GOING_TO_HAPPEN_SOON], 1, 1),
        # woken by the achieved precondition, the goal doesn't wait for the others again
        ([GoalState.ACHIEVED, GoalState.WAITING_FOR_WORKER, GoalState.NOT_GOING_TO_HAPPEN_SOON], 0, 0),
        # nothing to wait for
        ([GoalState.ACHIEVED], 1, 0),
    ],
)
def test_goals_fsck_any_mode(precondition_states: list[GoalState], waiting_for_count: int, expected_waiting_for_count: int) -> None:
    goal = GoalFactory.create(
        preconditions_mode=PreconditionsMode.ANY,
        waiting_for_count=waiting_for_count,
        precondition_goals=[GoalFactory.create(state=state) for state in precondition_states],
    )
    call_command('goals_fsck')
    goal.refresh_from_db()
    assert goal.waiting_for_count == expected_waiting_for_count


@pytest.mark.django_db
@pytest.mark.parametrize('goal', [{
    'precondition_failure_behavior': PreconditionFailureBehavior.PROCEED,
}], indirect=True)
def test_goals_fsck_proceed_mode(goal: Goal) -> None:
    goal.precondition_goals.add(
        GoalFactory.create(state=GoalState.ACHIEVED),
        GoalFactory.create(state=GoalState.WAITING_FOR_WORKER),
        GoalFactory.create(state=GoalState.NOT_GOING_TO_HAPPEN_SOON),
    )
    call_command('goals_fsck')
    goal.refresh_from_db()
    assert goal.waiting_for_count == 1
    assert goal.waiting_for_not_achieved_count == 2
    assert goal.waiting_for_failed_count == 1


@pytest.mark.django_db(transaction=True)
def test_goals_fsck_does_not_wait_for_preconditions(pursued: Callable[[Goal], AbstractContextManager[None]]) -> None:
    precondition = GoalFactory.create()
    goal = GoalFactory.create(precondition_goals=[precondition])
    with pursued(precondition):
        call_command('goals_fsck')
    goal.refresh_from_db()
    assert goal.waiting_for_not_achieved_count == 1
