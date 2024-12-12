import logging

from django.db import connection

from .models import handle_waiting_for_worker
from .notifications import listen_goal_waiting_for_worker


logger = logging.getLogger(__name__)


def worker():
    '''
    This worker is a blocking worker that listens for notifications on the
    goal_waiting_for_worker channel. It will then handle the waiting for worker
    jobs. This worker will run indefinitely until it is stopped.
    '''
    logger.info("Blocking worker started, registering listener (goal_waiting_for_worker)")
    listen_goal_waiting_for_worker()

    logger.info("Executing work ready before we were listening")
    while True:
        did_a_thing = handle_waiting_for_worker()
        if not did_a_thing:
            break

    logger.info("Handling notifications")
    pg_conn = connection.connection
    for _ in pg_conn.notifies():
        # We might pick a different job than the one that was notified.
        # This is okay, because there are as many (or more) notifications as there are jobs.
        handle_waiting_for_worker()

    logger.info("Blocking worker exiting now")
