import signal
import threading

from django.core.management.base import BaseCommand

from django_goals.models import worker


class Command(BaseCommand):
    help = 'Run the worker'

    def handle(self, *args, **options):
        stop_event = threading.Event()
        signal.signal(signal.SIGINT, lambda signum, frame: stop_event.set())
        signal.signal(signal.SIGTERM, lambda signum, frame: stop_event.set())
        worker(stop_event)
