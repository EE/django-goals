from django.core.management.base import BaseCommand

from django_goals.blocking_worker import worker


class Command(BaseCommand):
    help = 'Run the blocking worker'

    def handle(self, *args, **options):
        worker()
