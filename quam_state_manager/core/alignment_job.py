"""Workspace alignment off the request path (RAM P7, ram_design.md §4 P7:
"alignment on a new root: request <= 150 ms with background progress (from
97 s inline); <= 50 ms after a new run").

``HistoryManager.scan_workspace_alignment`` reads and fingerprints every
workspace run's quam_state the first time it meets it -- 60 s measured on the
4,121-run KH archive, inside the ``/param-history/alignment`` request. Its
result cache is already validated on read (workspace token + the loaded
chip's fingerprint), so what changes here is only WHO pays for a miss:

* ``request(...)`` answers a valid cached result at once (``ready``);
* otherwise it starts -- or joins -- ONE background computation per
  (manager, loaded chip) and waits at most ``wait_s`` for it; a computation
  still running then answers ``running`` with its progress, and the route
  renders a placeholder that fetches itself again. The previous verdict is
  never presented as the current one (design §1.1).

``refresh(...)`` recomputes in the caller's thread; the run-watch worker
calls it after a new run for a chip whose alignment somebody has viewed, so
the next request finds it ready.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class _Job:
    __slots__ = ("done", "total", "thread", "error", "started", "finished")

    def __init__(self) -> None:
        self.done = 0
        self.total = 0
        self.thread: threading.Thread | None = None
        self.error: BaseException | None = None
        self.started = time.monotonic()
        self.finished = threading.Event()


class AlignmentJobs:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[tuple[int, str], _Job] = {}
        self.started = 0
        self.errors = 0

    def _key(self, hm: Any, loaded_path: Path) -> tuple[int, str]:
        return (id(hm), str(Path(loaded_path)))

    def request(self, hm: Any, loaded_path: Path, workspace: Any, *,
                wait_s: float = 0.12) -> dict[str, Any]:
        """``{"state": "ready", "result": ...}`` or
        ``{"state": "running", "done": n, "total": m}``."""
        cached = hm.cached_workspace_alignment(loaded_path, workspace)
        if cached is not None:
            return {"state": "ready", "result": cached}
        job = self._start(hm, loaded_path, workspace)
        job.finished.wait(max(0.0, wait_s))
        if job.finished.is_set():
            if job.error is not None:
                raise job.error
            cached = hm.cached_workspace_alignment(loaded_path, workspace)
            if cached is not None:
                return {"state": "ready", "result": cached}
            # the workspace moved while it ran: a newer computation is needed
            job = self._start(hm, loaded_path, workspace)
        return {"state": "running", "done": job.done, "total": job.total}

    def _start(self, hm: Any, loaded_path: Path, workspace: Any) -> _Job:
        key = self._key(hm, loaded_path)
        with self._lock:
            job = self._jobs.get(key)
            if job is not None and not job.finished.is_set():
                return job                       # single-flight: join it
            job = _Job()
            self._jobs[key] = job
            self.started += 1

        def run() -> None:
            def progress(done: int, total: int) -> None:
                job.done, job.total = done, total
            try:
                hm.scan_workspace_alignment(loaded_path, workspace, progress=progress)
            except BaseException as exc:          # surfaced to the next request
                job.error = exc
                self.errors += 1
                logger.warning("background alignment scan failed", exc_info=True)
            finally:
                job.finished.set()

        t = threading.Thread(target=run, name="ph-alignment", daemon=True)
        job.thread = t
        t.start()
        return job

    def refresh(self, hm: Any, loaded_path: Path, workspace: Any) -> bool:
        """Recompute now, in the caller's thread, when this chip has a cached
        alignment that went stale. Returns whether it computed."""
        if not hm.has_workspace_alignment(loaded_path):
            return False                          # nobody has looked: stay cold
        if hm.cached_workspace_alignment(loaded_path, workspace) is not None:
            return False
        key = self._key(hm, loaded_path)
        with self._lock:
            job = self._jobs.get(key)
        if job is not None and not job.finished.is_set():
            job.finished.wait(60.0)               # one computation at a time
            return False
        hm.scan_workspace_alignment(loaded_path, workspace)
        return True

    def join(self, timeout: float = 30.0) -> None:
        """Tests: wait for every running job."""
        with self._lock:
            jobs = list(self._jobs.values())
        for j in jobs:
            j.finished.wait(timeout)
