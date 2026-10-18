"""Can these folders be found? -- asked without stalling the caller (v1.5.1 R2).

``Path(p).exists()`` on an unreachable network path blocks for the whole SMB
timeout: measured 42.4 s for an unroutable host and 5.6 s for an unknown one.
Asked on a request thread (a chip open under its build lock, a history dir
resolve), that is a frozen page.

:func:`folders_present` asks in parallel daemon threads -- one in flight per
folder -- waits at most ``WAIT_S`` in all, and memoises every answer for
``TTL_S`` per process; a late answer is kept for the next caller. An
unanswered probe is ``None``: every caller treats it as "there" (nothing is
taken over or brought over on a guess).
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

#: At most this long, in all, for one call's probes ...
WAIT_S = 1.0
#: ... and an answer is reused for this long by every caller in this process.
TTL_S = 300.0

_PROBES: dict = {}                     # path -> (monotonic time, True/False/None)
_INFLIGHT: dict = {}                   # path -> the probe thread still asking
_LOCK = threading.Lock()


#: R3 M2: a "not there" that took longer than this is a network timeout
#: (an unreachable share answers ENOENT after 5-42 s), not a folder that is
#: gone: no answer, never memoised
SLOW_FALSE_S = WAIT_S


def _probe(path: str) -> None:
    t0 = time.monotonic()
    try:
        verdict = Path(path).exists()
    except OSError:
        verdict = None
    if verdict is False and time.monotonic() - t0 > SLOW_FALSE_S:
        verdict = None
    with _LOCK:
        _PROBES[path] = (time.monotonic(), verdict)
        _INFLIGHT.pop(path, None)


def folders_present(paths, *, wait_s: float | None = None) -> dict:
    """{path: True / False / None} -- can each folder be found? None: no
    answer within the wait (a slow share)."""
    now = time.monotonic()
    out, waits = {}, []
    with _LOCK:
        for p in dict.fromkeys(str(x) for x in paths):
            hit = _PROBES.get(p)
            if hit is not None and now - hit[0] < TTL_S and hit[1] is not None:
                out[p] = hit[1]
                continue
            t = _INFLIGHT.get(p)
            if t is None or not t.is_alive():
                t = threading.Thread(target=_probe, args=(p,), daemon=True,
                                     name="folder-probe")
                _INFLIGHT[p] = t
                t.start()
            waits.append((p, t))
    deadline = time.monotonic() + (WAIT_S if wait_s is None else wait_s)
    for p, t in waits:
        t.join(max(0.0, deadline - time.monotonic()))
        with _LOCK:
            hit = _PROBES.get(p)
        out[p] = hit[1] if hit is not None and not t.is_alive() else None
    return out


def folder_present(path) -> bool | None:
    """:func:`folders_present` for one folder."""
    return folders_present([path])[str(path)]


def forget() -> None:
    """Drop every remembered answer (tests; a user-visible rescan)."""
    with _LOCK:
        _PROBES.clear()
