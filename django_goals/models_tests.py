import datetime
import threading

import pytest
from django.db import connection, transaction

from .factories import GoalFactory
from .models import (
    AllDone, Goal, GoalState, PreconditionFailureBehavior, PreconditionsMode,
    block_goal, handle_deadline_propagation, handle_unblocked_goals,
    handle_waiting_for_failed_preconditions, handle_waiting_for_worker,
    schedule, unblock_retry_goal,
)
from .pickups import GoalPickup


@pytest.mark.django_db
@pytest.mark.parametrize(
    'goal',
    [{'state': GoalState.GIVEN_UP}],
    indirect=True,
)
def test_retry(goal: Goal) -> None:
    unblock_retry_goal(goal.id)
    goal.refresh_from_db()
    assert goal.state == GoalState.WAITING_FOR_DATE


@pytest.mark.django_db
@pytest.mark.parametrize('goal', [{'state': GoalState.GIVEN_UP}], indirect=True)
def test_retry_dependent_on(goal: Goal) -> None:
    next_goal = GoalFactory.create(
        state=GoalState.NOT_GOING_TO_HAPPEN_SOON,
        precondition_goals=[goal],
    )
    unblock_retry_goal(goal.id)
    handle_unblocked_goals()
    next_goal.refresh_from_db()
    assert next_goal.state == GoalState.WAITING_FOR_DATE


@pytest.mark.django_db
@pytest.mark.parametrize(
    ('mode', 'expected_waiting_for_count'),
    [
        (PreconditionsMode.ALL, 2),
        (PreconditionsMode.ANY, 0),  # a wake-up is not taken back
    ],
)
def test_unblock_precondition_proceed_mode(mode: PreconditionsMode, expected_waiting_for_count: int) -> None:
    preconds = GoalFactory.create_batch(2, state=GoalState.WAITING_FOR_DATE)
    goal = schedule(
        noop,
        precondition_goals=preconds,
        preconditions_mode=mode,
        precondition_failure_behavior=PreconditionFailureBehavior.PROCEED,
    )
    block_goal(preconds[0].id)
    unblock_retry_goal(preconds[0].id)
    goal.refresh_from_db()
    assert goal.waiting_for_count == expected_waiting_for_count
    assert goal.waiting_for_failed_count == 0


@pytest.mark.django_db
def test_preconditions_fail_and_unblock_together() -> None:
    failed_goal = GoalFactory.create(state=GoalState.GIVEN_UP)
    preconds = [schedule(noop, precondition_goals=[failed_goal]) for _ in range(2)]
    goal, other_goal = [
        schedule(
            noop,
            precondition_goals=goal_preconds,
            precondition_failure_behavior=PreconditionFailureBehavior.PROCEED,
        )
        for goal_preconds in [preconds, preconds[:1]]
    ]

    assert handle_waiting_for_failed_preconditions() == 2
    goal.refresh_from_db()
    other_goal.refresh_from_db()
    assert (goal.waiting_for_count, goal.waiting_for_failed_count) == (0, 2)
    assert (other_goal.waiting_for_count, other_goal.waiting_for_failed_count) == (0, 1)

    unblock_retry_goal(failed_goal.id)
    assert handle_unblocked_goals() == 2
    goal.refresh_from_db()
    other_goal.refresh_from_db()
    assert (goal.waiting_for_count, goal.waiting_for_failed_count) == (2, 0)
    assert (other_goal.waiting_for_count, other_goal.waiting_for_failed_count) == (1, 0)


@pytest.mark.django_db
def test_handle_waiting_for_worker_killer_task(settings: object) -> None:
    settings.GOALS_MAX_PICKUPS = 2  # type: ignore[attr-defined]
    goal = GoalFactory.create(state=GoalState.WAITING_FOR_WORKER)
    for _ in range(2):
        GoalPickup.objects.create(goal=goal)
    handle_waiting_for_worker()
    goal.refresh_from_db()
    assert goal.state == GoalState.IT_IS_A_KILLER_TASK


def noop(goal: Goal) -> AllDone:  # pylint: disable=unused-argument
    return AllDone()


@pytest.mark.django_db
def test_schedule_accepts_string_handler() -> None:
    # A string is stored verbatim; it is not imported until the goal is
    # pursued, so it need not be resolvable at schedule time.
    goal = schedule('not.importable.yet.handler')
    assert goal.handler == 'not.importable.yet.handler'


def deadline_of(goal: Goal) -> datetime.datetime:
    return Goal.objects.values_list('deadline', flat=True).get(id=goal.id)


def propagate_deadlines() -> None:
    for _ in range(10):
        if not handle_deadline_propagation():
            return
    raise AssertionError('Deadline propagation does not settle')


@pytest.mark.django_db
def test_schedule_updates_deadline() -> None:
    now = datetime.datetime(2024, 11, 6, 11, 41, 0, tzinfo=datetime.timezone.utc)
    sooner = now - datetime.timedelta(minutes=1)
    urgent = now - datetime.timedelta(minutes=2)
    goal_a = GoalFactory.create(deadline=now)
    goal_b = GoalFactory.create(deadline=now, precondition_goals=[goal_a])
    achieved_goal = GoalFactory.create(state=GoalState.ACHIEVED, deadline=now)
    urgent_goal = GoalFactory.create(deadline=urgent)
    schedule(
        noop,
        deadline=sooner,
        precondition_goals=[goal_b, achieved_goal, urgent_goal],
    )

    handle_deadline_propagation()
    assert deadline_of(goal_b) == sooner
    assert deadline_of(goal_a) == now  # one level at a time

    propagate_deadlines()
    assert deadline_of(goal_a) == sooner
    assert deadline_of(achieved_goal) == now
    assert deadline_of(urgent_goal) == urgent


@pytest.mark.django_db(transaction=True)
def test_deadline_propagation_does_not_wait_for_goal_being_pursued() -> None:
    now = datetime.datetime(2024, 11, 6, 11, 41, 0, tzinfo=datetime.timezone.utc)
    sooner = now - datetime.timedelta(minutes=1)
    goal_a = GoalFactory.create(state=GoalState.WAITING_FOR_WORKER, deadline=now)
    goal_b = GoalFactory.create(deadline=now, precondition_goals=[goal_a])
    locked = threading.Event()
    release = threading.Event()

    def pursue_goal_a() -> None:  # worker keeps the goal locked while pursuing it
        with transaction.atomic():
            Goal.objects.select_for_update(no_key=True).get(id=goal_a.id)
            locked.set()
            release.wait(10)
        connection.close()

    worker = threading.Thread(target=pursue_goal_a)
    worker.start()
    assert locked.wait(10)
    with connection.cursor() as cursor:
        cursor.execute("SET lock_timeout = '1s'")  # fail instead of waiting
    try:
        schedule(noop, deadline=sooner, precondition_goals=[goal_b])
        propagate_deadlines()
    finally:
        release.set()
        worker.join()
        with connection.cursor() as cursor:
            cursor.execute('RESET lock_timeout')

    assert deadline_of(goal_a) == now
    propagate_deadlines()
    assert deadline_of(goal_a) == sooner


@pytest.mark.django_db
@pytest.mark.parametrize(
    ('goal', 'expected_waiting_for', 'expected_waiting_for_failed_count'),
    [
        ({'state': GoalState.WAITING_FOR_WORKER}, 1, 0),
        ({'state': GoalState.ACHIEVED}, 0, 0),
        ({'state': GoalState.GIVEN_UP}, 1, 1),
        ({'state': GoalState.NOT_GOING_TO_HAPPEN_SOON}, 1, 1),
    ],
    indirect=['goal'],
)
@pytest.mark.parametrize('mode', [PreconditionsMode.ALL, PreconditionsMode.ANY])
def test_schedule_updates_waiting_for_count(goal: Goal, expected_waiting_for: int, expected_waiting_for_failed_count: int, mode: PreconditionsMode) -> None:
    next_goal = schedule(noop, precondition_goals=[goal], preconditions_mode=mode)
    assert next_goal.waiting_for_count == expected_waiting_for
    assert next_goal.waiting_for_failed_count == expected_waiting_for_failed_count
    assert next_goal.preconditions_mode == mode


@pytest.mark.django_db
@pytest.mark.parametrize(
    'failure_mode',
    [
        PreconditionFailureBehavior.PROCEED,
        PreconditionFailureBehavior.BLOCK,
    ],
)
def test_schedule_any_mode_caps_waiting_for(failure_mode: PreconditionFailureBehavior) -> None:
    preconds = GoalFactory.create_batch(2, state=GoalState.WAITING_FOR_WORKER)
    next_goal = schedule(
        noop,
        precondition_goals=preconds,
        preconditions_mode=PreconditionsMode.ANY,
        precondition_failure_behavior=failure_mode,
    )
    assert next_goal.waiting_for_count == 1
    assert next_goal.precondition_goals.count() == 2


@pytest.mark.django_db
@pytest.mark.parametrize(
    ('failure_mode', 'expected_waiting_for_count'),
    [
        (PreconditionFailureBehavior.PROCEED, 0),
        (PreconditionFailureBehavior.BLOCK, 1),
    ],
)
@pytest.mark.parametrize('mode', [PreconditionsMode.ALL, PreconditionsMode.ANY])
def test_schedule_failed_precond(failure_mode: PreconditionFailureBehavior, expected_waiting_for_count: int, mode: PreconditionsMode) -> None:
    failed_goal = GoalFactory.create(
        state=GoalState.GIVEN_UP,
    )
    goal = schedule(
        noop,
        precondition_goals=[failed_goal],
        preconditions_mode=mode,
        precondition_failure_behavior=failure_mode,
    )
    assert goal.state == GoalState.WAITING_FOR_PRECONDITIONS
    assert goal.waiting_for_count == expected_waiting_for_count
    assert goal.waiting_for_failed_count == 1
    assert goal.waiting_for_not_achieved_count == 1


@pytest.mark.django_db
@pytest.mark.parametrize('blocked', [True, False])
def test_schedule_blocked(blocked: bool) -> None:
    goal = GoalFactory.create(state=GoalState.WAITING_FOR_WORKER)
    next_goal = schedule(noop, precondition_goals=[goal], blocked=blocked)
    assert next_goal.state == (
        GoalState.BLOCKED if blocked
        else GoalState.WAITING_FOR_PRECONDITIONS
    )
