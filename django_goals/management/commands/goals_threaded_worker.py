import logging
import threading
import time
from contextlib import contextmanager

from django.core.management.base import BaseCommand

from django_goals.models import (
    handle_unblocked_goals, handle_waiting_for_date,
    handle_waiting_for_failed_preconditions, handle_waiting_for_preconditions,
    handle_waiting_for_worker, remove_old_goals,
)

from .goals_busy_worker import stop_signal_handler


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
        with stop_signal_handler() as stop_event:
            threaded_worker(
                thread_count=options['threads'],
                stop_event=stop_event,
                once=options['once'],
            )


def threaded_worker(thread_count=1, stop_event=None, once=False):
    if stop_event is None:
        stop_event = threading.Event()

    workers_state = WorkersState(thread_count + 1)  # +1 for transitions thread

    threads = [
        TransitionsThread(
            stop_event=stop_event,
            once=once,
            workers_state=workers_state,
            thread_id="transitions",
        ),
    ] + [
        HeavyLiftingThread(
            stop_event=stop_event,
            once=once,
            workers_state=workers_state,
            thread_id=f"worker_{i}",
        ) for i in range(thread_count)
    ]

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join()


class HeavyLiftingThread(threading.Thread):
    def __init__(self, stop_event, once, workers_state, thread_id):
        super().__init__()
        self.stop_event = stop_event
        self.once = once
        self.workers_state = workers_state
        self.thread_id = thread_id

    def run(self):
        logger.info('Busy-wait worker started')

        while not self.stop_event.is_set():
            with self.workers_state.work_session(self.thread_id):
                try:
                    did_work = handle_waiting_for_worker()
                except Exception as e:
                    logger.exception(e)
                    # Treat exceptions as if we didn't do work
                    did_work = False

                self.workers_state.report_work(self.thread_id, did_work)

            if self.workers_state.all_idle and self.once:
                logger.info('All threads are idle. Exiting because of `once` flag.')
                break

            if not did_work:
                logger.debug('Nothing to do, sleeping for a bit')
                time.sleep(1)

        logger.info('Busy-wait worker exiting')


class TransitionsThread(threading.Thread):
    def __init__(self, stop_event, once, workers_state, thread_id):
        super().__init__()
        self.stop_event = stop_event
        self.once = once
        self.workers_state = workers_state
        self.thread_id = thread_id

    def run(self):
        logger.info('Transitions worker started')

        while not self.stop_event.is_set():
            with self.workers_state.work_session(self.thread_id):
                did_work = self._run_handlers()
                self.workers_state.report_work(self.thread_id, did_work)

            if self.workers_state.all_idle and self.once:
                logger.info('All threads are idle. Exiting because of `once` flag.')
                break

            if not did_work:
                logger.debug('Nothing to do, sleeping for a bit')
                time.sleep(1)

        logger.info('Transitions worker exiting')

    def _run_handlers(self):
        """Run all handlers and return True if any work was done"""
        try:
            results = [
                handle_waiting_for_preconditions(),
                handle_waiting_for_failed_preconditions(),
                handle_waiting_for_date(),
                handle_unblocked_goals(),
                remove_old_goals()
            ]
            return any(r for r in results if r)
        except Exception as e:
            logger.exception(e)
            return False  # No work done if there was an exception


class WorkersState:
    def __init__(self, thread_count):
        self.lock = threading.Lock()
        self.idle_threads = set()  # Threads that are permanently idle (no more work)
        self.active_threads = set()  # Threads currently registered as active
        self.total_threads = thread_count

    @contextmanager
    def work_session(self, thread_id):
        """
        Context manager to track a thread's work session.
        A thread enters a work session to check for and perform work.
        """
        with self.lock:
            self.active_threads.add(thread_id)

        yield

        with self.lock:
            assert thread_id not in self.active_threads, "You must call report_work before exiting the session"

    def report_work(self, thread_id, did_work):
        with self.lock:
            assert thread_id in self.active_threads, "Thread must be in work session to report work"
            self.active_threads.discard(thread_id)

            if did_work:
                # reactivate all, because the work we did might have unblocked other threads
                self.idle_threads.clear()

            else:
                # This thread is permanently idle
                self.idle_threads.add(thread_id)

    @property
    def all_idle(self):
        with self.lock:
            return len(self.idle_threads) == self.total_threads
