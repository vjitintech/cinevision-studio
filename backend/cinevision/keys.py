from __future__ import annotations

from .config import settings
from .crypto import KeyRing

_ring: KeyRing | None = None
_ring_key: bytes | None = None


def keyring() -> KeyRing:
    global _ring, _ring_key
    k = settings().master_key
    if _ring is None or _ring_key != k:
        _ring, _ring_key = KeyRing(k), k
    return _ring
