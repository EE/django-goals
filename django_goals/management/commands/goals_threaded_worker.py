import logging
import signal
import threading

from django.core.management.base import BaseCommand

from django_goals.models import worker


logger = logging.getLogger(__name__)


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
        threaded_worker(
            thread_count=options['threads'],
            stop_event=stop_event,
            once=options['once'],
        )


def threaded_worker(thread_count=1, stop_event=None, **kwargs):
    threads = [WorkerThread(
        stop_event=stop_event,
        **kwargs,
    ) for _ in range(thread_count)]
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
        while (
            self.stop_event is None or
            not self.stop_event.is_set()
        ):
            try:
                worker(
                    stop_event=self.stop_event,
                    once=self.once,
                )
            except Exception as e:
                logger.exception(e)
            if self.once:
                break
