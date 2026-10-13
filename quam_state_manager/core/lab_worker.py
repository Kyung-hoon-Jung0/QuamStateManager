"""One lab subprocess slot across worker kinds and SM processes."""
from __future__ import annotations

import atexit
import json
import os
import tempfile
import threading
import time
from pathlib import Path

import psutil

# Machine-wide on purpose: every SM process on this PC shares the one slot.
# QSM_LAB_WORKER_DIR moves it (the test suite uses its own, so a test run never
# retires or waits on the worker of an SM the user has open).
_COORD_DIR = (Path(os.environ["QSM_LAB_WORKER_DIR"]) if os.environ.get("QSM_LAB_WORKER_DIR")
              else Path(os.environ.get("PUBLIC", "C:/Users/Public")) / "Documents"
              if os.name == "nt" else Path("/tmp"))


class _MachineLock:
    """Thread lock plus an OS file lock; released automatically on parent exit."""

    def __init__(self):
        self.local = threading.RLock()
        self.depth = threading.local()
        self.path = _COORD_DIR / "qsm_lab_worker.lock"

    def acquire(self, blocking=True):
        if not self.local.acquire(blocking=blocking):
            return False
        depth = getattr(self.depth, "value", 0)
        if depth and not blocking:
            self.local.release()
            return False
        if depth:
            self.depth.value = depth + 1
            return True
        fh = self.path.open("a+b")
        fh.seek(0)
        if not fh.read(1):
            fh.write(b"0")
            fh.flush()
        while True:
            try:
                fh.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.depth.fh = fh
                self.depth.value = 1
                return True
            except OSError:
                if not blocking:
                    fh.close()
                    self.local.release()
                    return False
                time.sleep(.05)

    def release(self):
        self.depth.value -= 1
        if not self.depth.value:
            fh = self.depth.fh
            if os.name == "nt":
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh, fcntl.LOCK_UN)
            fh.close()
        self.local.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *args):
        self.release()


machine_lock = _MachineLock()
_SLOT = None
_RECORD = _COORD_DIR / "qsm_lab_worker.json"


def _kill_tree(proc):
    """Stop only this worker and its descendants, including plotting helpers."""
    try:
        children = proc.children(recursive=True)
    except psutil.Error:
        children = []
    for child in reversed(children):
        try:
            child.kill()
        except psutil.Error:
            pass
    proc.kill()
    psutil.wait_procs(children, timeout=5)


def _spawn(argv, **kwargs):
    import subprocess
    if os.name != "nt":
        return subprocess.Popen(argv, **kwargs)
    from .process_owner import Job
    job = Job()
    proc = None
    # Attach before any plotting code can create helpers.
    kwargs["creationflags"] = kwargs.get("creationflags", 0) | 0x4  # CREATE_SUSPENDED
    try:
        proc = subprocess.Popen(argv, **kwargs)
        job.assign(proc._handle)
        proc._sm_owner = job
        job.resume(proc.pid)
        return proc
    except BaseException:
        job.close()
        if proc is not None:
            proc.kill()
            proc.wait(timeout=5)
        raise


def _close_owner(proc):
    job = getattr(proc, "_sm_owner", None)
    if job is not None:
        job.close()


def run(argv, *, timeout, **kwargs):
    """Cold replay with the same RSS and deadline limits as a warm request."""
    import subprocess
    kwargs.pop("capture_output", None)
    if os.name == "nt":
        kwargs.setdefault("creationflags", subprocess.CREATE_NO_WINDOW)
    with _spawn(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs) as proc:
        observed = psutil.Process(proc.pid)
        deadline = time.monotonic() + min(timeout, 180)
        try:
            while True:
                try:
                    if observed.memory_info().rss > 2 * 1024**3:
                        raise OSError("Lab worker exceeded the 2 GiB memory limit")
                except psutil.NoSuchProcess:
                    pass
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(argv, timeout)
                try:
                    stdout, stderr = proc.communicate(timeout=min(.1, remaining))
                    return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)
                except subprocess.TimeoutExpired:
                    continue
        finally:
            try:
                _kill_tree(observed)
            except psutil.Error:
                pass
            _close_owner(proc)


def command(argv, *, timeout):
    """Outcome adapter for the existing waveform result-file reader."""
    import subprocess
    try:
        result = run(argv, timeout=timeout, text=True, encoding="utf-8",
                     env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "Lab worker timed out"
    except (OSError, UnicodeError) as exc:
        return -1, "", str(exc)


def retire_except(worker=None):
    """Retire the previous slot before any warm or cold lab process starts.

    Call while holding machine_lock. PID creation time prevents PID reuse from
    targeting a different process. The record covers other SM instances too.
    """
    global _SLOT
    if _SLOT is not None and _SLOT is not worker:
        _SLOT.kill()
        _SLOT = None
    try:
        rec = json.loads(_RECORD.read_text(encoding="utf-8"))
        if worker is not None and rec["pid"] == worker.proc.pid:
            return
        proc = psutil.Process(rec["pid"])
        if proc.create_time() == rec["created"]:
            _kill_tree(proc)
            proc.wait(timeout=5)
    except (OSError, ValueError, KeyError, psutil.Error):
        pass
    _RECORD.unlink(missing_ok=True)


def register(worker):
    global _SLOT
    _SLOT = worker
    proc = psutil.Process(worker.proc.pid)
    _RECORD.write_text(json.dumps({"pid": proc.pid, "created": proc.create_time()}), encoding="utf-8")


def shutdown():
    global _SLOT
    if _SLOT is not None:
        _SLOT.kill()
        _SLOT = None


atexit.register(shutdown)


from typing import Any


def _stat(f):
    try:
        st = os.stat(f)
        return st.st_mtime_ns, st.st_size
    except OSError:
        return None


def _env_sig(python_path):
    from quam_state_manager.core.config_generator import _env_signature
    return _env_signature(python_path)


class Worker:
    """``run_pulse_waveform.py --serve`` kept alive between edits.

    Safe to reuse only while it would answer exactly what a cold run answers
    now, so :meth:`fresh` is checked before EVERY request:

    * the process is alive, the env signature it was started under still
      holds (a ``pip install`` moves it) and so does the process environment
      it inherited (``PYTHONPATH`` decides which lab module is imported);
    * every out-of-env file it imported (the lab's editable module) still has
      the stat it had WHEN IMPORTED -- the worker reports first-seen stats, not
      re-stats, so an edit after the import is caught here and the worker is
      killed, never asked.

    Anything unexpected (a timeout, a dead pipe, a garbled line) kills it and
    the caller falls back to one cold run. It is killed after ``WARM_IDLE_S``
    idle, when another env is selected, and at interpreter exit."""

    def __init__(self, python_path: str, script: str, marker: str, *, signature=None, context="", idle_s=3600):
        import queue
        import subprocess
        self.marker = marker
        self.signature = signature or _env_sig
        self.context = context
        self.idle_s = idle_s
        retire_except()
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
        self.python_path = python_path
        self.env_sig = self.signature(python_path)
        # a cold run inherits the environment AT ITS SPAWN (PYTHONPATH decides
        # which lab module is imported): the warm one must still match it
        self.environ = dict(os.environ)
        self.sources: dict[str, Any] = {script: _stat(script)}
        self._idle: threading.Timer | None = None
        self.proc = _spawn(
            [python_path, "-B", script, "--serve"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
            bufsize=1, env=env, cwd=tempfile.gettempdir(), **kwargs)
        try:
            self._process = psutil.Process(self.proc.pid)
            register(self)
        except Exception:
            self.kill()
            raise
        self._q: "queue.Queue[str | None]" = queue.Queue()
        threading.Thread(target=self._pump, daemon=True,
                         name="lab-waveform-warm").start()
        self.requests = 0
        self.timed_out = False
        # w9/labwarm: False until the process has answered once (its imports
        # are done); what the UI's "Preparing your lab code..." asks
        self.ready = False
        self.t_spawn = time.monotonic()
        self.t_ready: float | None = None
        self.prewarmed = False

    def _pump(self) -> None:
        try:
            for line in self.proc.stdout:          # type: ignore[union-attr]
                if line.startswith(self.marker):
                    self._q.put(line[len(self.marker):])
        except Exception:  # noqa: BLE001
            pass
        self._q.put(None)

    def fresh(self) -> bool:
        if self.proc.poll() is not None:
            return False
        if self.signature(self.python_path) != self.env_sig:
            return False
        if dict(os.environ) != self.environ:
            return False
        for f, rec in self.sources.items():
            now = _stat(f)
            if now is None or list(now) != list(rec or []):
                return False
        return True

    def ask(self, spec: dict, timeout: float) -> dict | None:
        import queue
        try:
            self.proc.stdin.write(json.dumps(spec, default=repr) + "\n")  # type: ignore[union-attr]
            self.proc.stdin.flush()                                                   # type: ignore[union-attr]
            deadline = time.monotonic() + min(timeout, 180)
            while True:
                if time.monotonic() >= deadline:
                    raise queue.Empty
                try:
                    if psutil.Process(self.proc.pid).memory_info().rss > 2 * 1024**3:
                        self.kill()
                        return None
                except psutil.Error:
                    return None
                try:
                    line = self._q.get(timeout=min(.1, max(.001, deadline - time.monotonic())))
                    break
                except queue.Empty:
                    continue
        except queue.Empty:
            self.timed_out = True       # the lab's code did not answer
            return None
        except (OSError, ValueError):
            return None
        if line is None:
            return None
        try:
            parsed = json.loads(line)
        except ValueError:
            return None
        if parsed.get("status") != "ok":
            return None
        for f, rec in (parsed.get("sources") or {}).items():
            self.sources.setdefault(f, rec)
        self.requests += 1
        if not self.ready:
            self.ready = True
            self.t_ready = time.monotonic()
        self._arm_idle()
        return parsed

    def _arm_idle(self) -> None:
        self._idle_deadline = time.monotonic() + self.idle_s
        self._schedule_idle(self.idle_s)

    def _schedule_idle(self, delay) -> None:
        if self._idle is not None:
            self._idle.cancel()
        self._idle = threading.Timer(delay, self._expire)
        self._idle.daemon = True
        self._idle.start()

    def _expire(self):
        with machine_lock:
            remaining = self._idle_deadline - time.monotonic()
            if remaining > 0:
                # A timer can wake before a coarse monotonic clock reaches the deadline.
                self._schedule_idle(remaining)
            else:
                self.kill()

    def kill(self) -> None:
        if self._idle is not None:
            self._idle.cancel()
        try:
            _kill_tree(self._process)
        except (AttributeError, psutil.Error):
            self.proc.kill()
        _close_owner(self.proc)
        try:
            self.proc.stdin.close()                 # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass
        try:
            self.proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            pass
