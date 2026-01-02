import contextlib
import logging
import signal
import threading
from argparse import ArgumentParser
from typing import Any, Callable, Iterator

from django.core.management.base import BaseCommand

from django_goals.busy_worker import worker


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Run the worker'

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument(
            '--max-progress-count',
            type=int,
            default=float('inf'),
            help='Exit when this many progress records are created',
        )

    def handle(self, *args, max_progress_count: int | float, **options) -> None:  # type: ignore
        with stop_signal_handler() as stop_event:
            worker(stop_event, max_progress_count=max_progress_count)


@contextlib.contextmanager
def stop_signal_handler() -> Iterator[threading.Event]:
    stop_event = threading.Event()

    def handler(signum: int, frame: Any) -> None:
        signal_str = signal.Signals(signum).name
        logger.info('Received signal %s, stopping', signal_str)
        stop_event.set()

    with (
        set_signal_handler(signal.SIGINT, handler),
        set_signal_handler(signal.SIGTERM, handler),
    ):
        yield stop_event


@contextlib.contextmanager
def set_signal_handler(signum: int, handler: Callable[[int, Any], None]) -> Iterator[None]:
    old_handler = signal.signal(signum, handler)
    try:
        yield
    finally:
        signal.signal(signum, old_handler)
