"""A pulse's waveform drawn by the LAB's own class code (docs/218 adaptive pulses).

``waveform_synth`` mirrors quam's pulse classes in-process; a class the lab
wrote has no mirror, and SM deliberately does not transcribe one (docs/189 §2:
a copy goes stale the day the lab edits its module). docs/189 answered with
the generated config, which is the lab's own ``generate_config()`` output --
but it is only as fresh as its last run (13 s, whole chip), and it cannot draw
a pulse that does not exist yet.

This module asks the class itself, in the selected env: one subprocess
(``generator/run_pulse_waveform.py``) builds the dataclass from the pulse's
fields and calls quam's ``Pulse.calculate_waveform()`` -- the same method
``_config_add_waveforms`` calls when the config is generated. ~2-6 s, once
per distinct (class, fields); after that the answer comes from RAM.

Staleness (the global RAM risk, ram_design.md): the cache key is the
CONTENT -- env interpreter + class path + the canonical JSON of every field
value -- and the entry's token is the env signature plus the ``[mtime_ns,
size]`` of every out-of-env source file the subprocess imported (the lab's
editable package). The token is re-derived by re-statting those files on
EVERY read, so an edit to the lab's module is a miss, not a stale curve.
Errors the CLASS raised for these fields are answers and are cached the same
way; a failure to RUN (no env, timeout) is never cached.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from quam_state_manager.core.ramcache import Keyed, KeyedMemo

logger = logging.getLogger(__name__)

_MiB = 1024 * 1024
MEMO = KeyedMemo("pulses.lab_waveform", max_bytes=32 * _MiB, max_entries=1024)

# slot -> the source files its stored entry was computed from. Only the file
# NAMES live here; the token compared on read is always re-statted, so this
# side table can never make a stale entry look current.
_SLOT_FILES: dict[Any, tuple[str, ...]] = {}
_SLOT_LOCK = threading.Lock()
# one subprocess at a time per interpreter: concurrent requests for the same
# pulse wait and then find it in RAM instead of spawning a second process
_ENV_LOCKS: dict[str, threading.Lock] = {}

TIMEOUT_S = 90

#: an item whose class starts with this is a GATE check (the lab's own
#: ``macro.apply()`` on a loaded chip), not a drawing -- the same string
#: ``generator/run_pulse_waveform.MACRO_PREFIX`` dispatches on
MACRO_PREFIX = "@macro:"


def _env_lock(python_path: str) -> threading.Lock:
    with _SLOT_LOCK:
        lk = _ENV_LOCKS.get(python_path)
        if lk is None:
            lk = _ENV_LOCKS[python_path] = threading.Lock()
        return lk


def _canon(params: dict) -> str:
    return json.dumps(params or {}, sort_keys=True, separators=(",", ":"),
                      default=repr)


def slot_for(python_path: str, qclass: str, params: dict) -> tuple:
    digest = hashlib.sha256(_canon(params).encode("utf-8")).hexdigest()
    return (os.path.normcase(str(python_path)), str(qclass), digest)


def _stat(f: str):
    try:
        st = os.stat(f)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _env_sig(python_path: str):
    from quam_state_manager.core.config_generator import _env_signature
    return _env_signature(python_path)


def _token_now(python_path: str, slot) -> tuple:
    with _SLOT_LOCK:
        files = _SLOT_FILES.get(slot, ())
    return (_env_sig(python_path), tuple((f, _stat(f)) for f in files))


def _token_from(env_sig, sources: dict) -> tuple:
    return (env_sig, tuple((f, tuple(rec) if isinstance(rec, (list, tuple)) else None)
                           for f, rec in sorted((sources or {}).items())))


class _Miss(Exception):
    pass


def _raise_miss():
    raise _Miss()


def cached(python_path: str, qclass: str, params: dict) -> dict | None:
    """The cached drawing for exactly these fields, or None. Never spawns.
    Served only through ``KeyedMemo.get`` with the token re-derived NOW."""
    if not python_path or not qclass:
        return None
    slot = slot_for(python_path, qclass, params)
    with _SLOT_LOCK:
        known = slot in _SLOT_FILES
    if not known:
        return None
    try:
        return MEMO.get(slot, _token_now(python_path, slot), _raise_miss)
    except _Miss:
        return None


#: the warm worker (2026-09-27): one long-lived subprocess per selected env,
#: reused while it is provably drawing with the code on disk. Off => every miss
#: is a cold subprocess, exactly as before.
WARM = True
#: w9/labwarm: 10 min -> 60 min. A lab session is edit, run a node, look at
#: the result, edit again -- ten minutes of looking cost the next edit a cold
#: start (5-12 s krs5). The other retirements are unchanged: another env
#: selected, a lab file edited (``fresh``), interpreter exit.
WARM_IDLE_S = 3600
_WARM_MARK = "@@SM-LABWF@@"
_WORKERS: dict[str, "_Worker"] = {}
_WORKERS_LOCK = threading.Lock()
#: an item whose class starts with this only IMPORTS it (the pre-warm) -- the
#: same string ``generator/run_pulse_waveform.IMPORT_PREFIX`` dispatches on
IMPORT_PREFIX = "@import:"


class _Worker:
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

    def __init__(self, python_path: str):
        import queue
        import subprocess
        from quam_state_manager.core.config_generator import _script_path
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        self.python_path = python_path
        self.env_sig = _env_sig(python_path)
        # a cold run inherits the environment AT ITS SPAWN (PYTHONPATH decides
        # which lab module is imported): the warm one must still match it
        self.environ = dict(os.environ)
        self.sources: dict[str, Any] = {}
        self.proc = subprocess.Popen(
            [python_path, "-B", str(_script_path("run_pulse_waveform.py")), "--serve"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
            bufsize=1, env=env, cwd=tempfile.gettempdir(), **kwargs)
        self._q: "queue.Queue[str | None]" = queue.Queue()
        threading.Thread(target=self._pump, daemon=True,
                         name="lab-waveform-warm").start()
        self._idle: threading.Timer | None = None
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
                if line.startswith(_WARM_MARK):
                    self._q.put(line[len(_WARM_MARK):])
        except Exception:  # noqa: BLE001
            pass
        self._q.put(None)

    def fresh(self) -> bool:
        if self.proc.poll() is not None:
            return False
        if _env_sig(self.python_path) != self.env_sig:
            return False
        if dict(os.environ) != self.environ:
            return False
        for f, rec in self.sources.items():
            now = _stat(f)
            if now is None or list(now) != list(rec or []):
                return False
        return True

    def ask(self, items: list[dict], timeout: float) -> dict | None:
        import queue
        try:
            self.proc.stdin.write(json.dumps({"items": items}, default=repr) + "\n")  # type: ignore[union-attr]
            self.proc.stdin.flush()                                                   # type: ignore[union-attr]
            line = self._q.get(timeout=timeout)
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
        if self._idle is not None:
            self._idle.cancel()
        self._idle = threading.Timer(WARM_IDLE_S, _retire, args=(self.python_path, self))
        self._idle.daemon = True
        self._idle.start()

    def kill(self) -> None:
        if self._idle is not None:
            self._idle.cancel()
        try:
            self.proc.stdin.close()                 # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass
        try:
            self.proc.kill()
            self.proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            pass


def _retire(python_path: str, worker: "_Worker | None" = None) -> None:
    with _WORKERS_LOCK:
        w = _WORKERS.get(python_path)
        if w is None or (worker is not None and w is not worker):
            return
        del _WORKERS[python_path]
    w.kill()


def shutdown_workers() -> None:
    """Kill every warm worker (interpreter exit, tests)."""
    with _WORKERS_LOCK:
        ws = list(_WORKERS.values())
        _WORKERS.clear()
    for w in ws:
        w.kill()


import atexit  # noqa: E402

atexit.register(shutdown_workers)


def retire_except(python_path: str | None) -> int:
    """Kill every warm worker that is not *python_path*'s (another env was
    selected), and stand down any pre-warm still waiting for another env.
    Returns how many workers were killed."""
    with _PREWARM_LOCK:
        for key in list(_PREWARM):
            if key != python_path:
                _PREWARM.pop(key, None)       # its thread sees it and stops
        for key in list(_PREWARM_MORE):
            if key != python_path:
                _PREWARM_MORE.pop(key, None)
    stale = []
    with _WORKERS_LOCK:
        for key in list(_WORKERS):
            if key != python_path:
                stale.append(_WORKERS.pop(key))
    for s in stale:
        s.kill()
    return len(stale)


# ---------------------------------------------------------------- pre-warm
# w9/labwarm. The first lab check after a server start paid the worker's
# spawn + imports (5-12 s krs5, ~12 s big30x) while the user watched a cell.
# A chip that carries a lab class the check would ask, opened with an env
# already selected, now starts that worker in the background: it IMPORTS the
# chip's lab classes (the modules the chip-open class probe imports already)
# and stops there -- nothing is built, drawn or loaded until a real check
# asks. A pre-warm never runs while a foreground request is in flight (the
# caller's *wait*), never takes the env lock from a check that holds it (a
# check already on its way starts the worker itself), and a check that
# arrives while the imports run waits for THAT worker instead of spawning a
# second, cold one.

_PREWARM: dict[str, object] = {}        # python_path -> the pending pre-warm's token
_PREWARM_LOCK = threading.Lock()
# python_path -> classes asked for while that env's pre-warm was in flight (a
# chip switched to while the first chip's warm-up ran): imported after it
_PREWARM_MORE: dict[str, set] = {}
#: what the last pre-warm per env did -- for the UI's status and measurement
PREWARM_LOG: dict[str, dict] = {}


def prewarm(python_path: str | None, classes, *, wait=None,
            start_thread: bool = True) -> bool:
    """Start *python_path*'s warm worker in the background, importing
    *classes*. True when a pre-warm was started (False: no env, WARM off,
    no class, one already in flight for this env -- its classes are then
    imported by that one, right after its own). A worker already running
    for the env is asked to import *classes* too (another chip's classes;
    an import already done answers in milliseconds). *wait(stop)* blocks
    until the server is quiet (``stop()`` true = give up); None = no wait."""
    classes = sorted({c for c in (classes or ()) if isinstance(c, str) and c})
    if not WARM or not python_path or not classes:
        return False
    token = object()
    with _PREWARM_LOCK:
        if python_path in _PREWARM:
            _PREWARM_MORE.setdefault(python_path, set()).update(classes)
            return False
        _PREWARM[python_path] = token
    PREWARM_LOG[python_path] = {"state": "pending", "t_request": time.monotonic(),
                                "classes": classes}
    args = (python_path, classes, token, wait)
    if start_thread:
        threading.Thread(target=_prewarm_run, args=args, daemon=True,
                         name="lab-waveform-prewarm").start()
    else:
        _prewarm_run(*args)
    return True


def _import_items(classes) -> list[dict]:
    return [{"qclass": IMPORT_PREFIX + c, "params": {}} for c in classes]


def _prewarm_more(python_path: str, w, done, log: dict) -> None:
    """Import what another pre-warm asked for while this one ran (under the
    env lock, on the same worker)."""
    with _PREWARM_LOCK:
        more = _PREWARM_MORE.pop(python_path, set())
    more = sorted(set(more) - set(done))
    if more and w.ask(_import_items(more), TIMEOUT_S) is not None:
        log["more"] = more


def _prewarm_current(python_path: str, token) -> bool:
    with _PREWARM_LOCK:
        return _PREWARM.get(python_path) is token


def _prewarm_run(python_path: str, classes: list[str], token, wait) -> None:
    log = PREWARM_LOG.setdefault(python_path, {})
    try:
        if wait is not None:
            wait(lambda: not _prewarm_current(python_path, token))
        if not _prewarm_current(python_path, token):
            log["state"] = "superseded"
            return
        if not Path(python_path).is_file():
            log["state"] = "no-env"
            return
        lk = _env_lock(python_path)
        if not lk.acquire(blocking=False):
            # a check holds the env: it starts (or already has) the worker
            log["state"] = "check-running"
            return
        try:
            if not _prewarm_current(python_path, token):
                log["state"] = "superseded"
                return
            retire_except(python_path)
            with _WORKERS_LOCK:
                w = _WORKERS.get(python_path)
            if w is not None and w.ready and w.fresh():
                # another chip's classes may be new to it: import them now
                # (an import already done answers in milliseconds)
                parsed = w.ask(_import_items(classes), TIMEOUT_S)
                if parsed is None:
                    _retire(python_path, w)
                    log["state"] = "failed"
                    return
                log["state"] = "already-ready"
                _prewarm_more(python_path, w, classes, log)
                return
            if w is not None:
                _retire(python_path, w)
            try:
                w = _Worker(python_path)
            except Exception:  # noqa: BLE001 -- a pre-warm that cannot start is no loss
                logger.debug("lab worker pre-warm failed to start", exc_info=True)
                log["state"] = "spawn-failed"
                return
            w.prewarmed = True
            with _WORKERS_LOCK:
                _WORKERS[python_path] = w
            log.update(state="importing", t_spawn=w.t_spawn)
            parsed = w.ask(_import_items(classes), TIMEOUT_S)
            if parsed is None:
                _retire(python_path, w)
                log["state"] = "failed"
                if w.timed_out:
                    # a check that waited behind these imports is told now,
                    # not made to wait TIMEOUT_S a second time (_run_warm)
                    log["timed_out_at"] = time.monotonic()
                return
            log.update(state="ready", t_ready=w.t_ready,
                       imported=[bool(it.get("ok")) for it in parsed.get("items") or []])
            _prewarm_more(python_path, w, classes, log)
        finally:
            lk.release()
    except Exception:  # noqa: BLE001 -- background work never raises
        logger.debug("lab worker pre-warm failed", exc_info=True)
        log["state"] = "error"
    finally:
        left = None
        with _PREWARM_LOCK:
            if _PREWARM.get(python_path) is token:
                del _PREWARM[python_path]
                # asked for after _prewarm_more had looked: one more round
                # (a worker that failed takes its extras with it)
                left = _PREWARM_MORE.pop(python_path, None)
        if left and log.get("state") in ("ready", "already-ready"):
            prewarm(python_path, left, start_thread=False)


def worker_state(python_path: str | None) -> str:
    """``"ready"`` (a check is answered by a warm worker now), ``"starting"``
    (the worker is importing -- a check waits for it), ``"cold"`` (the next
    check starts one), ``"no-env"``. Cheap: a dict read and, for a live
    worker, the stat-only ``fresh`` test."""
    if not python_path:
        return "no-env"
    with _WORKERS_LOCK:
        w = _WORKERS.get(python_path)
    if w is None:
        return "cold"
    if not w.fresh():
        return "cold"                 # the next check kills it and starts anew
    return "ready" if w.ready else "starting"


#: per thread: when the running ``draw`` began waiting for the env lock
_TL = threading.local()


def _run_warm(python_path: str, items: list[dict]) -> dict | None:
    """``_run``'s answer from the warm worker, or None (caller runs cold).
    Called under ``_env_lock(python_path)``: one request at a time per env.
    A pre-warm whose imports timed out while this caller waited for that
    lock answers it with the timeout (``_TL.waited_since``), instead of a
    second full ``TIMEOUT_S`` on a fresh worker."""
    waited_since = getattr(_TL, "waited_since", None)
    t_out = (PREWARM_LOG.get(python_path) or {}).get("timed_out_at")
    if waited_since is not None and t_out is not None and t_out >= waited_since:
        return {"ok": False, "error": _timeout_text(), "items": [], "sources": {}}
    retire_except(python_path)              # another env selected: retire it
    stale = []
    with _WORKERS_LOCK:
        w = _WORKERS.get(python_path)
        if w is not None and not w.fresh():
            stale.append(_WORKERS.pop(python_path))
            w = None
    for s in stale:
        s.kill()
    if w is None:
        try:
            w = _Worker(python_path)
        except Exception:  # noqa: BLE001 -- cannot start: run cold
            logger.debug("warm lab-waveform worker failed to start", exc_info=True)
            return None
        with _WORKERS_LOCK:
            _WORKERS[python_path] = w
    parsed = w.ask(items, TIMEOUT_S)
    if parsed is None:
        _retire(python_path, w)
        if w.timed_out:
            # docs/218: the same request run cold would hang as long again --
            # say so now instead of making the user wait twice
            return {"ok": False, "error": _timeout_text(), "items": [],
                    "sources": {}}
        return None
    return {"ok": True, "error": None, "items": parsed.get("items") or [],
            "sources": parsed.get("sources") or {}}


def _timeout_text() -> str:
    return (f"your lab's code did not answer within {TIMEOUT_S} s and was "
            "stopped")


def _run(python_path: str, items: list[dict]) -> dict:
    """One subprocess for *items*; ``{"ok", "error", "items", "sources"}``.
    The warm worker answers when it can; otherwise one cold run."""
    if WARM and python_path and Path(python_path).is_file():
        warm = _run_warm(python_path, items)
        if warm is not None:
            return warm
    return _run_cold(python_path, items)


def _run_cold(python_path: str, items: list[dict]) -> dict:
    """One cold subprocess for *items*."""
    from quam_state_manager.core.config_generator import (
        _blank_outcome, _cleanup_work_dir, _run_script_outcome, _script_path)
    script = _script_path("run_pulse_waveform.py")
    out = {"ok": False, "error": None, "items": [], "sources": {}}
    if not python_path or not Path(python_path).is_file():
        out["error"] = ("the selected Python environment no longer exists — "
                        "reselect it in Generate Config")
        return out
    outcome = _blank_outcome()
    work_dir = Path(tempfile.mkdtemp(prefix="quamlabwf_"))
    try:
        (work_dir / "_in.json").write_text(json.dumps({"items": items}, default=repr),
                                           encoding="utf-8")
        # -B: never leave __pycache__ behind in the lab's own package
        _run_script_outcome(
            [python_path, "-B", str(script), "--in", str(work_dir / "_in.json"),
             "--out", str(work_dir / "_result.json")],
            work_dir, TIMEOUT_S, outcome,
            no_result_label="lab waveform",
            error_fallback="drawing with the lab's class failed")
    finally:
        _cleanup_work_dir(work_dir)
    parsed = outcome.get("result") or {}
    if not outcome.get("ok"):
        out["error"] = (_timeout_text()
                        if "timed out" in str(outcome.get("stderr") or "")
                        and not parsed else
                        outcome.get("error") or "drawing with the lab's class failed")
        return out
    out.update(ok=True, items=parsed.get("items") or [],
               sources=parsed.get("sources") or {})
    return out


def draw(python_path: str | None, items: list[tuple[str, dict]], *,
         spawn: bool = True) -> list[dict]:
    """Drawings for ``[(qclass, params), ...]`` in the same order.

    Every entry is ``{"ok", "error", "i", "q", "iq", "kind", "length",
    "canonical", "dropped", "warnings", "cached"}``. Hits are served from RAM;
    the misses of one call share ONE subprocess. With ``spawn=False`` a miss
    answers ``{"ok": False, "reason": "not-drawn"}`` (a list render may never
    wait on a subprocess).
    """
    if not python_path:
        return [{"ok": False, "reason": "no-env",
                 "error": "no Python environment is selected — pick one in "
                          "Generate Config so SM can run your class's code"}
                for _ in items]
    results: list[dict | None] = [None] * len(items)
    for n, (qclass, params) in enumerate(items):
        hit = cached(python_path, qclass, params)
        if hit is not None:
            results[n] = {**hit, "cached": True}
    todo = [n for n, r in enumerate(results) if r is None]
    if not todo:
        return results  # type: ignore[return-value]
    if not spawn:
        for n in todo:
            results[n] = {"ok": False, "reason": "not-drawn",
                          "error": "not drawn yet"}
        return results  # type: ignore[return-value]

    _TL.waited_since = time.monotonic()
    with _env_lock(python_path):
        # a concurrent request may have drawn these while we waited
        for n in todo:
            hit = cached(python_path, *items[n])
            if hit is not None:
                results[n] = {**hit, "cached": True}
        todo = [n for n, r in enumerate(results) if r is None]
        if todo:
            env_sig = _env_sig(python_path)
            ran = _run(python_path, [{"qclass": items[n][0], "params": items[n][1]}
                                     for n in todo])
            if not ran["ok"]:
                for n in todo:
                    results[n] = {"ok": False, "reason": "run-failed",
                                  "error": ran["error"]}
            else:
                token = _token_from(env_sig, ran["sources"])
                files = tuple(f for f, _ in token[1])
                for k, n in enumerate(todo):
                    rec = ran["items"][k] if k < len(ran["items"]) else {
                        "ok": False, "error": "no answer for this pulse"}
                    rec = {key: rec.get(key) for key in (
                        "ok", "error", "i", "q", "iq", "kind", "length",
                        "canonical", "dropped", "warnings", "macros",
                        "load_failed", "reason", "config_ran",
                        "config_error")}
                    if rec.get("reason"):
                        # "this env cannot run the class" is not an answer
                        # about these fields: never cached, asked again
                        results[n] = {**rec, "cached": False}
                        continue
                    rec.pop("reason", None)
                    slot = slot_for(python_path, *items[n])
                    with _SLOT_LOCK:
                        _SLOT_FILES[slot] = files
                    value = MEMO.get(slot, ("fresh", object()),
                                     lambda r=rec: Keyed(r, token),
                                     sizeof=_sizeof)
                    results[n] = {**value, "cached": False}
    return results  # type: ignore[return-value]


def _sizeof(rec: dict) -> int:
    n = len(rec.get("i") or []) + len(rec.get("q") or [])
    return 256 + 24 * n


def payload_for_plot(rec: dict) -> dict:
    """A ``waveform_synth``-shaped payload, so ``_pulse_plot_traces`` and
    ``sparkline_svg`` draw it exactly like an in-process synthesis."""
    if not rec or not rec.get("ok"):
        return {"ok": False, "error": (rec or {}).get("error") or "not drawn"}
    i = rec.get("i") or []
    return {"ok": True, "i": i, "q": rec.get("q"), "iq": bool(rec.get("iq")),
            "kind": rec.get("kind"), "length": rec.get("length") or len(i),
            "x_ns": list(range(len(i))), "warnings": list(rec.get("warnings") or []),
            "error": None, "param_errors": {}}
