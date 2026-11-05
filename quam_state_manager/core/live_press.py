"""Process-local live-write boundaries shared by web and server-side writers.

The outer boundary includes saving the draft and publishing its journal entry.
The core writer also marks itself, so a new write door is covered immediately.
Nested calls on one thread never wait for themselves.
"""
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
import inspect
import os
import threading

_CV = threading.Condition()
_INFLIGHT = {}
_REVISION = 0


def revision():
    with _CV:
        return _REVISION


def _key(wc):
    return os.path.normcase(str(Path(wc.live_folder).resolve())) if wc else None


@contextmanager
def mark(wc):
    global _REVISION
    key, tid = _key(wc), threading.get_ident()
    if key is None:
        yield
        return
    with _CV:
        _REVISION += 1
        per = _INFLIGHT.setdefault(key, {})
        per[tid] = per.get(tid, 0) + 1
    try:
        yield
    finally:
        with _CV:
            _REVISION += 1
            per = _INFLIGHT[key]
            per[tid] -= 1
            if not per[tid]:
                del per[tid]
            if not per:
                del _INFLIGHT[key]
            _CV.notify_all()


def marked(get_wc):
    def decorate(fn):
        first_name = next(iter(inspect.signature(fn).parameters))
        @wraps(fn)
        def wrapped(*args, **kwargs):
            first = args[0] if args else kwargs[first_name]
            with mark(get_wc(first)):
                return fn(*args, **kwargs)
        return wrapped
    return decorate


def wait(wc, timeout=10.0):
    key, tid = _key(wc), threading.get_ident()
    with _CV:
        return _CV.wait_for(
            lambda: not any(t != tid for t in _INFLIGHT.get(key, {})), timeout=timeout)
