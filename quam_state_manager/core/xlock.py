"""Cross-process exclusive locks on a lock file (docs/271 review).

``safe_io.path_lock`` is process-local by design. Two things SM does are not:
two SM windows (two processes) appending to ONE chip journal, and two SM
windows writing ONE live chip. Both need a lock every process honours, so
this one is an OS lock on a small lock file (``msvcrt.locking`` on Windows,
``fcntl.flock`` elsewhere), taken behind a per-process lock so threads of one
process queue in memory instead of polling the OS. Re-entrant per thread.

A lock that cannot be had in ``timeout`` seconds raises :class:`LockTimeout`
(an ``OSError``): every caller takes it BEFORE it changes anything.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Iterator

if os.name == "nt":
    import msvcrt

    def _try(fd: int) -> None:
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _release(fd: int) -> None:
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:  # pragma: no cover -- the CI of record is Windows
    import fcntl

    def _try(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _release(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


class LockTimeout(OSError):
    """The lock stayed taken for the whole timeout; nothing was done."""


_GUARD = threading.Lock()
_RLOCKS: dict[str, threading.RLock] = {}
_TLS = threading.local()


def _key(path: str | Path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _rlock(key: str) -> threading.RLock:
    with _GUARD:
        lk = _RLOCKS.get(key)
        if lk is None:
            lk = _RLOCKS[key] = threading.RLock()
        return lk


@contextlib.contextmanager
def held(lock_path: str | Path, *, timeout: float = 60.0, poll: float = 0.002) -> Iterator[None]:
    """Hold the exclusive lock of *lock_path* (created if missing)."""
    key = _key(lock_path)
    depth = getattr(_TLS, "depth", None)
    if depth is None:
        depth = _TLS.depth = {}
    if depth.get(key):
        depth[key] += 1
        try:
            yield
        finally:
            depth[key] -= 1
        return
    rl = _rlock(key)
    deadline = time.monotonic() + timeout
    if not rl.acquire(timeout=max(0.0, timeout)):
        raise LockTimeout(f"{lock_path} stayed locked for {timeout:.0f} s")
    try:
        Path(lock_path).parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o666)
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            while True:
                try:
                    _try(fd)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise LockTimeout(f"{lock_path} stayed locked for {timeout:.0f} s") from None
                    time.sleep(poll)
            depth[key] = 1
            try:
                yield
            finally:
                depth[key] = 0
                try:
                    os.lseek(fd, 0, os.SEEK_SET)
                    _release(fd)
                except OSError:
                    pass
        finally:
            os.close(fd)
    finally:
        rl.release()


def live_lock_path(live_folder: str | Path) -> Path:
    """The machine-wide lock of one live chip folder: every SM process (any
    instance dir) writing that folder takes the same file. It lives in the
    user's temp dir, never in the customer's chip folder."""
    try:
        resolved = str(Path(live_folder).resolve())
    except OSError:
        resolved = os.path.abspath(str(live_folder))
    digest = hashlib.sha1(os.path.normcase(resolved).encode("utf-8")).hexdigest()[:20]
    return Path(tempfile.gettempdir()) / "quam-sm-locks" / f"live-{digest}.lock"
