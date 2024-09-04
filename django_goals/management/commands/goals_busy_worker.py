import signal
import threading

from django.core.management.base import BaseCommand

from django_goals.models import worker


class Command(BaseCommand):
    help = 'Run the worker'

    def add_arguments(self, parser):
        parser.add_argument(
            '--max-transitions',
            type=int,
            default=None,
            help='Exit when about this many goals state transitions are made by the worker',
        )

    def handle(self, *args, max_transitions=None, **options):
        stop_event = threading.Event()
        signal.signal(signal.SIGINT, lambda signum, frame: stop_event.set())
        signal.signal(signal.SIGTERM, lambda signum, frame: stop_event.set())
        worker(stop_event, max_transitions=max_transitions)
