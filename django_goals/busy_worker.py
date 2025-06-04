import logging
import time

from django.utils import timezone

from django_goals.models import (
    handle_unblocked_goals, handle_waiting_for_date,
    handle_waiting_for_failed_preconditions, handle_waiting_for_preconditions,
    handle_waiting_for_worker, remove_old_goals,
)


logger = logging.getLogger(__name__)


def worker(stop_event=None, max_progress_count=float('inf'), once=False):
    """
    Worker is a busy-wait function that will keep checking for goals to pursue.
    It will keep running until stop_event is set.
    Process:
    1. Check if there are goals that are waiting for date and the date has come.
    2. Check if there are goals that are waiting for preconditions and all preconditions are achieved.
    3. Check if there are goals that are waiting for worker and pick one to pursue.
    4. If nothing could be done, sleep for a bit.
    5. Repeat until stop_event is set.
    """
    logger.info('Busy-wait worker started')
    progress_count = 0
    while (
        stop_event is None or
        not stop_event.is_set()
    ):
        if progress_count >= max_progress_count:
            logger.info('Max transitions reached, exiting')
            break

        transitions_done, local_progress_count = worker_turn(
            stop_event=stop_event,
            max_progress_count=max_progress_count - progress_count,
        )
        progress_count += local_progress_count

        if transitions_done == 0 and local_progress_count == 0:
            if once:
                logger.info('Nothing to do, exiting because of `once` flag')
                break
            # nothing could be done, let's go to sleep
            logger.debug('Nothing to do, sleeping for a bit')
            time.sleep(1)

    logger.info('Busy-wait worker exiting')


def worker_turn(now=None, stop_event=None, max_progress_count=float('inf')):
    """
    Worker turn is a single iteration of the worker.
    It will try to transition as many goals as possible.
    Returns a number of transitions done (all state changes)
    and a number of progress transitions done (real handler calls).
    """
    if now is None:
        now = timezone.now()
    transitions_done = 0
    transitions_done += handle_waiting_for_date(now)
    transitions_done += handle_waiting_for_preconditions()
    transitions_done += handle_waiting_for_failed_preconditions()
    transitions_done += handle_unblocked_goals()
    progress_count = 0
    while (
        stop_event is None or
        not stop_event.is_set()
    ):
        if progress_count >= max_progress_count:
            break
        did_a_thing = handle_waiting_for_worker()
        if not did_a_thing:
            break
        transitions_done += 1
        progress_count += 1
    remove_old_goals(now)
    return transitions_done, progress_count
