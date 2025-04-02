import uuid

from django.core.management.base import BaseCommand
from django.db import transaction

from django_goals.models import Goal, GoalState, unblock_retry_goal


class Command(BaseCommand):
    """
    Retry goals that were previously given up due to multiple failures.
    """
    help = 'Retry all goals that were previously marked as GIVEN_UP'

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit',
            type=int,
            help='Maximum number of goals to retry (default: no limit)'
        )

    def handle(self, *args, **options):
        limit = options.get('limit')
        count = retry_all_given_up(self, limit)
        self.stdout.write(self.style.SUCCESS(f'Retried {count} goals'))


def retry_all_given_up(command, limit=None):
    """Retry all given up goals, optionally limited to a maximum count."""
    goal_id = uuid.UUID(int=0)
    count = 0

    while True:
        if limit is not None and count >= limit:
            command.stdout.write(f'Reached limit of {limit} goals')
            break

        goal_id = retry_next_given_up_goal(goal_id, command)

        if not goal_id:
            break

        count += 1

        # Move to the next goal ID
        goal_id = uuid.UUID(int=goal_id.int + 1)

    return count


@transaction.atomic
def retry_next_given_up_goal(goal_id, command):
    """
    Find and retry the next given up goal with ID >= goal_id.
    Returns the ID of the processed goal, or None if no eligible goal was found.
    """
    goal = Goal.objects.filter(
        id__gte=goal_id,
        state=GoalState.GIVEN_UP,
    ).order_by('id').select_for_update(
        no_key=True,
        skip_locked=True,
    ).first()

    if not goal:
        return None

    unblock_retry_goal(goal.id)
    command.stdout.write(f'Retried goal {goal.id}')

    return goal.id
