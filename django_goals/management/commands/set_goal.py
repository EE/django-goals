import time

from django.core.management.base import BaseCommand

from django_goals.models import AllDone, schedule
from django_goals.notifications import wait


class Command(BaseCommand):
    help = 'Create a butterfly network of 2^n goals'

    def add_arguments(self, parser):
        parser.add_argument('-n', type=int, default=0)

    def handle(self, *args, **options):
        n = options['n']

        if not (0 <= n <= 16):
            self.stdout.write(self.style.ERROR(f'n must be between 0 and 16, got {n}'))
            return

        num_nodes = 2 ** n
        total_goals = num_nodes * (n + 1) + 2

        self.stdout.write(f'Building butterfly network: n={n}, {total_goals} total goals')

        execution_start = time.monotonic()
        start_task = schedule(noop)

        stages = []
        for stage_num in range(n + 1):
            current_stage = []
            for node in range(num_nodes):
                if stage_num == 0:
                    preconditions = [start_task]
                else:
                    preconditions = [stages[stage_num - 1][node]]
                    partner = node ^ (1 << (stage_num - 1))
                    if partner != node:
                        preconditions.append(stages[stage_num - 1][partner])

                goal = schedule(noop, precondition_goals=preconditions)
                current_stage.append(goal)

            stages.append(current_stage)

        schedule(noop, precondition_goals=stages[-1], listen=True)

        self.stdout.write('Network built')

        wait()
        execution_time = time.monotonic() - execution_start

        self.stdout.write(
            self.style.SUCCESS(
                f'Butterfly network completed in {execution_time:.3f}s '
                f'({total_goals:,} goals, {execution_time / total_goals * 1000:.2f}ms per goal, '
                f'{total_goals / execution_time:.0f} goals/sec)'
            )
        )


def noop(goal):
    return AllDone()
