import time

from django.core.management.base import BaseCommand

from django_goals.models import AllDone, schedule, wait


def pursue(goal):
    return AllDone()


class Command(BaseCommand):
    help = 'Set an example goal'

    def handle(self, *args, **options):
        start_time = time.monotonic()
        goal = schedule(pursue, listen=True)
        print('Goal scheduled', goal.id)
        notification = wait()
        end_time = time.monotonic()
        print('Goal done', notification, 'in', end_time - start_time, 'seconds')
