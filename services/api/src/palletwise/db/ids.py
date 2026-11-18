"""UUIDv7 generation for rows created by PalletWise services (CWM 2)."""

import secrets
from uuid import UUID

from palletwise import clock


def uuid7() -> UUID:
    """RFC 9562 UUIDv7: 48-bit Unix milliseconds, version 7, variant 10, random bits."""
    ms = int(clock.wall_now().timestamp() * 1000)
    rand = secrets.randbits(74)
    value = (ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76
    value |= (rand >> 62) << 64  # 12 random bits
    value |= 0b10 << 62
    value |= rand & ((1 << 62) - 1)
    return UUID(int=value)
