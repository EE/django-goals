import os
import sys
import time
import uuid


__all__ = ('uuid7',)


def _uuid7_fallback() -> uuid.UUID:
    """
    Generate a UUIDv7 as specified in RFC 9562.
    The leading 48 bits are a unix millisecond timestamp, so values sort
    by generation time (at millisecond precision), unlike random UUIDv4.
    """
    timestamp_ms = time.time_ns() // 1_000_000
    rand_a = int.from_bytes(os.urandom(2)) & 0x0FFF
    rand_b = int.from_bytes(os.urandom(8)) & 0x3FFF_FFFF_FFFF_FFFF
    return uuid.UUID(int=(
        ((timestamp_ms & 0xFFFF_FFFF_FFFF) << 80) |
        (0x7 << 76) |  # version
        (rand_a << 64) |
        (0b10 << 62) |  # variant
        rand_b
    ))


# TODO: drop the fallback once the minimum supported Python is 3.14
if sys.version_info >= (3, 14):
    from uuid import uuid7
else:
    uuid7 = _uuid7_fallback
