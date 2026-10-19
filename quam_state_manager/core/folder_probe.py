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


def _probe(path: str) -> None:
    try:
        verdict = Path(path).exists()
    except OSError:
        verdict = None
    with _LOCK:
        _PROBES[path] = (time.monotonic(), verdict)
        _INFLIGHT.pop(path, None)


def folders_present(paths, *, wait_s: float | None = None, ttl_s: float | None = None,
                    reuse_present: bool = True) -> dict:
    """{path: True / False / None} -- can each folder be found? None: no
    answer within the wait (a slow share). *ttl_s* (default ``TTL_S``): how
    old a remembered answer may be to be reused (0: always ask again -- a
    probe still in flight is still not doubled); *reuse_present* False reuses
    only a remembered "not found" (a found answer is asked again)."""
    now = time.monotonic()
    ttl = TTL_S if ttl_s is None else ttl_s
    out, waits = {}, []
    with _LOCK:
        for p in dict.fromkeys(str(x) for x in paths):
            hit = _PROBES.get(p)
            if (hit is not None and now - hit[0] < ttl and hit[1] is not None
                    and (reuse_present or hit[1] is False)):
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


def folder_states(paths, *, wait_s: float | None = None) -> dict:
    """R4 B3: ``{path: "ok" | "gone" | "unreachable"}`` -- THE rule for a
    data folder, stated alike by the sidebar, the Datasets page and the run
    index (one rule, so the note and the rows cannot disagree).

    * ``"ok"``: the folder is found.
    * ``"gone"``: it is not found on a drive or share that answers (moved,
      renamed, deleted) -- its runs leave the lists.
    * ``"unreachable"``: it is not found and its drive or share root does not
      answer -- Windows raises the same not-found error for a moved folder,
      an unreachable share (WinError 53/67) and a disconnected mapped drive
      (WinError 3/15) -- or no answer came within the wait, or it cannot be
      read now: its runs stay as last read.

    Bounded (R4 b5): every folder and every drive or share root is asked
    through :func:`folders_present` -- in parallel, at most 1.5 x *wait_s*
    (``WAIT_S``) in all, never two probes of one path in flight. A folder is
    asked afresh each time (a moved folder, or one that is back, is seen at
    once); a root that did not answer is remembered for ``TTL_S``, so an
    outage costs one probe per root per ``TTL_S``, not one per call."""
    paths = [str(p) for p in dict.fromkeys(str(x) for x in paths)]
    if not paths:
        return {}
    budget = WAIT_S if wait_s is None else wait_s
    t0 = time.monotonic()
    here = folders_present(paths, wait_s=budget, ttl_s=0.0)
    out: dict = {}
    roots: dict = {}
    for p in paths:
        v = here.get(p)
        if v is True:
            out[p] = "ok"
        elif v is None:
            out[p] = "unreachable"
        else:
            anchor = Path(p).anchor
            if anchor:
                roots[p] = anchor
            else:
                out[p] = "gone"
    if roots:
        # the roots get what is left of the wait, and never less than half of
        # it (a folder answer that came late must not leave the root none)
        left = max(budget / 2, budget - (time.monotonic() - t0))
        up = folders_present(roots.values(), wait_s=left, reuse_present=False)
        for p, anchor in roots.items():
            out[p] = "gone" if up.get(anchor) is True else "unreachable"
    return out


def folder_state(path, *, wait_s: float | None = None) -> str:
    """:func:`folder_states` for one folder."""
    return folder_states([path], wait_s=wait_s)[str(path)]


def forget() -> None:
    """Drop every remembered answer (tests; a user-visible rescan)."""
    with _LOCK:
        _PROBES.clear()
