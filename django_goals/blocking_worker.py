import logging

from django.db import connection

from .models import handle_waiting_for_worker_guarded


logger = logging.getLogger(__name__)


def worker():
    logger.info("Blocking worker started, registering listener (goal_waiting_for_worker)")
    listen_goal_waiting_for_worker()

    logger.info("Executing work ready before we were listening")
    while True:
        did_a_thing = handle_waiting_for_worker_guarded()
        if not did_a_thing:
            break

    logger.info("Handling notifications")
    pg_conn = connection.connection
    for _ in pg_conn.notifies():
        # We might pick a different job than the one that was notified.
        # This is okay, because there are as many (or more) notifications as there are jobs.
        handle_waiting_for_worker_guarded()

    logger.info("Blocking worker exiting now")


def listen_goal_waiting_for_worker():
    with connection.cursor() as cursor:
        cursor.execute("LISTEN goal_waiting_for_worker")
