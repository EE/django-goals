import uuid

from django.core.management.base import BaseCommand
from django.db import transaction

from django_goals.models import (
    NOT_GOING_TO_HAPPEN_SOON_STATES, Goal, GoalState,
    PreconditionFailureBehavior, PreconditionsMode,
)


class Command(BaseCommand):
    """
    Check and fix the integrity of the goals system.
    """
    def handle(self, *args, **options):
        check_fix_all()


def check_fix_all():
    goal_id = uuid.UUID(int=0)
    i = 0
    while True:
        goal_id = check_fix_goal(goal_id)
        if not goal_id:
            break

        i += 1
        if i >= 1000:
            print(i, goal_id)
            i = 0

        goal_id = uuid.UUID(int=goal_id.int + 1)


@transaction.atomic
def check_fix_goal(goal_id):
    goal = Goal.objects.filter(id__gte=goal_id).order_by('id').select_for_update(
        no_key=True,
        skip_locked=True,
    ).first()
    if not goal:
        return None

    preconditions = list(goal.precondition_goals.all().select_for_update(
        no_key=True,
    ))
    waiting_for_count = 0
    waiting_for_failed_count = 0
    for pre in preconditions:
        if pre.state != GoalState.ACHIEVED:
            waiting_for_count += 1
        if pre.state in NOT_GOING_TO_HAPPEN_SOON_STATES:
            waiting_for_failed_count += 1
    waiting_for_not_achieved_count = waiting_for_count
    if goal.precondition_failure_behavior == PreconditionFailureBehavior.PROCEED:
        waiting_for_count -= waiting_for_failed_count
    if goal.preconditions_mode == PreconditionsMode.ANY:
        waiting_for_count = min(1, waiting_for_count)

    if waiting_for_count != goal.waiting_for_count:
        print(f"Goal {goal_id} waiting_for count, DB={goal.waiting_for_count}, recalculated={waiting_for_count}")
        goal.waiting_for_count = waiting_for_count
        goal.save(update_fields=['waiting_for_count'])

    if waiting_for_not_achieved_count != goal.waiting_for_not_achieved_count:
        print(f"Goal {goal_id} waiting_for_not_achieved count, DB={goal.waiting_for_not_achieved_count}, recalculated={waiting_for_not_achieved_count}")
        goal.waiting_for_not_achieved_count = waiting_for_not_achieved_count
        goal.save(update_fields=['waiting_for_not_achieved_count'])

    if waiting_for_failed_count != goal.waiting_for_failed_count:
        print(f"Goal {goal_id} waiting_for_failed count, DB={goal.waiting_for_failed_count}, recalculated={waiting_for_failed_count}")
        goal.waiting_for_failed_count = waiting_for_failed_count
        goal.save(update_fields=['waiting_for_failed_count'])

    return goal.id
