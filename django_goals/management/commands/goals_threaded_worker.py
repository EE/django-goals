import signal
import threading

from django.core.management.base import BaseCommand

from django_goals.models import worker


class Command(BaseCommand):
    help = 'Run the worker'

    def add_arguments(self, parser):
        parser.add_argument(
            '--threads',
            type=int,
            default=1,
        )
        parser.add_argument(
            '--once',
            action='store_true',
            help='Exit when no work is available',
        )

    def handle(self, *args, **options):
        stop_event = threading.Event()
        signal.signal(signal.SIGINT, lambda signum, frame: stop_event.set())
        signal.signal(signal.SIGTERM, lambda signum, frame: stop_event.set())
        threads = [WorkerThread(
            stop_event=stop_event,
            once=options['once'],
        ) for _ in range(options['threads'])]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()


class WorkerThread(threading.Thread):
    def __init__(self, stop_event, once):
        super().__init__()
        self.stop_event = stop_event
        self.once = once

    def run(self):
        worker(
            stop_event=self.stop_event,
            once=self.once,
        )
