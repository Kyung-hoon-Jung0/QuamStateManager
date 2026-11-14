"""The one process-unique identity of a ``QuamStore`` object.

``id()`` can be reused by a later store after one is collected; this never
is. The number is stored on the store (``_w7_uid``) the first time it is
asked for and never changes afterwards; every layer (the web routes' caches,
the chip copies of the gate check) asks this function, so two stores can
never share a number.
"""

from __future__ import annotations

import itertools

_UIDS = itertools.count(1)


def store_uid(store) -> int:
    uid = getattr(store, "_w7_uid", None)
    if uid is None:
        with store._lock:
            uid = getattr(store, "_w7_uid", None)
            if uid is None:
                uid = next(_UIDS)
                store._w7_uid = uid
    return uid
