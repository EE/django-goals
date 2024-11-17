import gc
import resource
import signal
from contextlib import contextmanager

from django.conf import settings


@contextmanager
def limit_memory():
    limit_mib = getattr(settings, 'GOALS_MEMORY_LIMIT_MIB', None)
    if limit_mib is None:
        yield
        return
    original_limit_soft, original_limit_hard = resource.getrlimit(resource.RLIMIT_AS)
    try:
        resource.setrlimit(resource.RLIMIT_AS, (limit_mib * 1024 * 1024, original_limit_hard))
        yield
    except MemoryError:
        gc.collect()
        raise
    finally:
        resource.setrlimit(resource.RLIMIT_AS, (original_limit_soft, original_limit_hard))


class TimesUp(Exception):
    pass


def sigalrm_handler(signum, frame):
    raise TimesUp()


@contextmanager
def limit_time():
    seconds = getattr(settings, 'GOALS_TIME_LIMIT_SECONDS', None)
    if seconds is None:
        yield
        return
    previous_handler = signal.signal(signal.SIGALRM, sigalrm_handler)
    assert previous_handler in (signal.SIG_DFL, sigalrm_handler)
    try:
        signal.alarm(seconds)
        yield
    finally:
        signal.alarm(0)
