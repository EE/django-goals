import gc
import logging
import resource
from contextlib import contextmanager

from django.conf import settings


logger = logging.getLogger(__name__)


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
        logger.exception('Memory limit exceeded')
        gc.collect()
        raise
    finally:
        resource.setrlimit(resource.RLIMIT_AS, (original_limit_soft, original_limit_hard))