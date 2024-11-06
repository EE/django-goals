import signal
import threading

from django.core.management.base import BaseCommand

from django_goals.models import worker


class Command(BaseCommand):
    help = 'Run the worker'

    def add_arguments(self, parser):
        parser.add_argument(
            '--max-progress-count',
            type=int,
            default=float('inf'),
            help='Exit when this many progress records are created',
        )

    def handle(self, *args, max_progress_count, **options):
        stop_event = threading.Event()
        signal.signal(signal.SIGINT, lambda signum, frame: stop_event.set())
        signal.signal(signal.SIGTERM, lambda signum, frame: stop_event.set())
        worker(stop_event, max_progress_count=max_progress_count)
