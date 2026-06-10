import time
import uuid
from collections.abc import Callable

import pytest

from .uuid7 import _uuid7_fallback, uuid7


# Cover both the public name (stdlib on python >= 3.14) and our fallback.
generators = pytest.mark.parametrize('generate', [uuid7, _uuid7_fallback])


@generators
def test_is_rfc_9562_version_7(generate: Callable[[], uuid.UUID]) -> None:
    value = generate()
    assert value.version == 7
    assert value.variant == uuid.RFC_4122


@generators
def test_is_time_ordered(generate: Callable[[], uuid.UUID]) -> None:
    earlier = generate()
    time.sleep(0.002)
    later = generate()
    assert earlier < later


@generators
def test_is_unique(generate: Callable[[], uuid.UUID]) -> None:
    values = {generate() for _ in range(1000)}
    assert len(values) == 1000
