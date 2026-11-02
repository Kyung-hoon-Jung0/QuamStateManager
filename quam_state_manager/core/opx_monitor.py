"""OPX monitor: settings, the env worker, the cached answer, history and alarms.

SM never imports ``qm`` (the QM stack runs in a subprocess of a user-chosen
env, ``generator/opx_monitor_worker.py``). This module owns:

* the user's settings under ``<instance>/opx_monitor/settings.json`` and the
  connection headers (secrets) in a separate ``secrets.json`` that is never
  rendered, logged or returned to a page (D-3, D-4);
* one persistent worker per target, polled only while a monitor page asked
  recently or an alarm is armed (D-5);
* the last answer with its age (a stale answer is labelled, an unreachable
  server is a sentence, never a green light -- D-6);
* an append-only jsonl history per target, deduplicated by qm id + job id
  (D-7), and the alarm judgement against thresholds on metrics the server
  really returned (D-9).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_S = 5.0
MIN_INTERVAL_S = 2.0
MAX_INTERVAL_S = 300.0
CALL_TIMEOUT_S = 30.0
START_TIMEOUT_S = 90.0
#: a page view keeps the poller alive for this many intervals
WANT_INTERVALS = 3
#: an answer older than this many intervals is labelled stale
STALE_INTERVALS = 3
#: after a worker could not start or died, wait this long before the next try,
#: doubling per failure up to MAX_INTERVAL_S (a dead server never means a new
#: env process every interval)
RESTART_BACKOFF_S = 10.0
#: a history file larger than this is compacted to its current rows
HISTORY_MAX_BYTES = 2_000_000
HISTORY_KEEP_ACTIONS = 200
#: closed rows kept by a compaction (open rows are always kept)
HISTORY_KEEP_ROWS = 5000
NOT_QM_SERVER = ("This target is not a QM server. Only a QM server (host and port) exposes "
                 "devices, open QMs and jobs; choose one under Monitor settings.")

WORKER_SCRIPT = Path(__file__).resolve().parent.parent / "generator" / "opx_monitor_worker.py"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clamp_interval(v) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL_S
    if f != f:  # NaN
        return DEFAULT_INTERVAL_S
    return max(MIN_INTERVAL_S, min(MAX_INTERVAL_S, f))


# ---------------------------------------------------------------- settings

def _dir(instance: str | Path) -> Path:
    return Path(instance) / "opx_monitor"


def target_key(target: dict) -> Optional[str]:
    if not target:
        return None
    ident = f"qmm|{target.get('host', '')}|{target.get('port') or ''}|{target.get('cluster_name') or ''}"
    return hashlib.sha1(ident.encode("utf-8")).hexdigest()[:12]


def target_label(target: dict) -> str:
    if not target:
        return ""
    s = str(target.get("host", ""))
    if target.get("port"):
        s += f":{target['port']}"
    if target.get("cluster_name"):
        s += f" ({target['cluster_name']})"
    return s


def load_settings(instance) -> dict:
    p = _dir(instance) / "settings.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            d = {}
    except (OSError, ValueError):
        d = {}
    d.setdefault("target", None)
    d.setdefault("python", None)
    d["interval_s"] = clamp_interval(d.get("interval_s", DEFAULT_INTERVAL_S))
    th = d.get("thresholds")
    d["thresholds"] = th if isinstance(th, dict) else {}
    d["alarms_armed"] = bool(d.get("alarms_armed", False))
    return d


def save_settings(instance, settings: dict) -> None:
    d = _dir(instance)
    d.mkdir(parents=True, exist_ok=True)
    clean = {k: settings.get(k) for k in ("target", "python", "interval_s", "thresholds", "alarms_armed")}
    tmp = d / "settings.json.tmp"
    tmp.write_text(json.dumps(clean, indent=1), encoding="utf-8")
    os.replace(tmp, d / "settings.json")


def load_secret_headers(instance, key: Optional[str]) -> dict:
    if not key:
        return {}
    try:
        d = json.loads((_dir(instance) / "secrets.json").read_text(encoding="utf-8"))
        h = d.get(key) if isinstance(d, dict) else None
        return {str(k): str(v) for k, v in h.items()} if isinstance(h, dict) else {}
    except (OSError, ValueError):
        return {}


def save_secret_headers(instance, key: str, headers: Optional[dict]) -> None:
    """Store connection headers for one target. ``None`` keeps the stored ones,
    ``{}`` removes them. The file lives under the instance dir, outside any repo."""
    if headers is None:
        return
    d = _dir(instance)
    d.mkdir(parents=True, exist_ok=True)
    p = d / "secrets.json"
    try:
        cur = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(cur, dict):
            cur = {}
    except (OSError, ValueError):
        cur = {}
    if headers:
        cur[key] = {str(k): str(v) for k, v in headers.items()}
    else:
        cur.pop(key, None)
    tmp = d / "secrets.json.tmp"
    tmp.write_text(json.dumps(cur), encoding="utf-8")
    os.replace(tmp, p)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


# ------------------------------------------------------------------ worker

class WorkerError(Exception):
    """A worker that died, timed out or could not start; ``str`` is the sentence."""


class EnvWorker:
    """One persistent ``opx_monitor_worker.py`` process in the chosen env."""

    def __init__(self, python: str, target: dict, *, start_timeout=START_TIMEOUT_S):
        self.python = python
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        env.pop("PYTHONPATH", None)
        flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
        self.proc = subprocess.Popen(
            [python, "-I", str(WORKER_SCRIPT)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8", env=env, creationflags=flags)
        self._q: "queue.Queue[Optional[str]]" = queue.Queue()
        threading.Thread(target=self._read, name="sm-opxmon-read", daemon=True).start()
        self._lock = threading.Lock()
        self.proc.stdin.write(json.dumps({"target": target}) + "\n")
        self.proc.stdin.flush()
        first = self._next(start_timeout, "starting the monitor worker")
        if not first.get("ready"):
            self.kill()
            raise WorkerError(f"The QM SDK could not connect: {first.get('error') or 'no reason given'}")

    def _read(self):
        try:
            for line in self.proc.stdout:
                line = line.strip()
                if line.startswith("{"):
                    self._q.put(line)
        finally:
            self._q.put(None)

    def _next(self, timeout, what):
        try:
            line = self._q.get(timeout=timeout)
        except queue.Empty:
            self.kill()
            raise WorkerError(f"No answer within {timeout:.0f} s while {what}; the worker was stopped.")
        if line is None:
            raise WorkerError(f"The monitor worker exited while {what} (exit code {self.proc.poll()}).")
        return json.loads(line)

    def alive(self) -> bool:
        return self.proc.poll() is None

    def request(self, op: str, timeout=CALL_TIMEOUT_S, **kw) -> dict:
        with self._lock:
            if not self.alive():
                raise WorkerError(f"The monitor worker is not running (exit code {self.proc.poll()}).")
            try:
                self.proc.stdin.write(json.dumps({"op": op, **kw}) + "\n")
                self.proc.stdin.flush()
            except OSError as exc:
                raise WorkerError(f"The monitor worker is not running ({exc}).")
            return self._next(timeout, f"running '{op}'")

    def kill(self):
        try:
            self.proc.kill()
        except OSError:
            pass


# ----------------------------------------------------------------- history

class History:
    """Append-only jsonl per target. Events: ``qm_seen`` / ``job_seen`` the first
    time a key appears, ``job_status`` when a seen job's status changes,
    ``qm_gone`` / ``job_gone`` when a key leaves the answer (carrying the
    last-seen time), and ``action`` for a clean-up the user ran."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self.rows: dict[str, dict] = {}
        self.actions: list[dict] = []
        self._load()

    @staticmethod
    def _key(qm_id, job_id=None) -> str:
        return f"{qm_id or ''}|{job_id or ''}"

    def _apply(self, ev: dict):
        k = self._key(ev.get("qm_id"), ev.get("job_id"))
        t = ev.get("t")
        kind = ev.get("ev")
        if kind == "action":
            self.actions.append(ev)
            return
        row = self.rows.get(k)
        if kind in ("qm_seen", "job_seen"):
            if row is None:
                row = self.rows[k] = {"qm_id": ev.get("qm_id"), "job_id": ev.get("job_id"),
                                      "first_seen": t, "last_seen": t, "status": ev.get("status"),
                                      "open": True, "user": ev.get("user"),
                                      "description": ev.get("description")}
            else:
                row["open"] = True
                row["last_seen"] = t
        elif row is not None and kind == "job_status":
            row["status"] = ev.get("status")
            row["last_seen"] = t
        elif row is not None and kind in ("qm_gone", "job_gone"):
            row["open"] = False
            row["last_seen"] = ev.get("last_seen") or t

    def _load(self):
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            return
        for line in text.splitlines():
            try:
                ev = json.loads(line)
            except ValueError:
                continue  # a torn last line from a crash is skipped, never fatal
            if isinstance(ev, dict):
                self._apply(ev)

    def _append(self, evs: list[dict]):
        if not evs:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            for ev in evs:
                f.write(json.dumps(ev, default=str) + "\n")
        for ev in evs:
            self._apply(ev)
        try:
            big = self.path.stat().st_size > HISTORY_MAX_BYTES
        except OSError:
            big = False
        if big:
            self._compact()

    def _compact(self):
        """Rewrite the file as the rows it describes (one seen/status/gone line
        per row) plus the newest actions; what the page shows is unchanged."""
        lines = []
        closed = sorted((k for k, r in self.rows.items() if not r.get("open")),
                        key=lambda k: self.rows[k].get("last_seen") or "")
        for k in closed[:max(0, len(closed) - HISTORY_KEEP_ROWS)]:
            del self.rows[k]
        rows = sorted(self.rows.values(), key=lambda r: r.get("first_seen") or "")
        for r in rows:
            base = {"qm_id": r.get("qm_id"), "job_id": r.get("job_id")}
            lines.append({"ev": "job_seen" if r.get("job_id") else "qm_seen", **base,
                          "t": r.get("first_seen"), "status": r.get("status"),
                          "user": r.get("user"), "description": r.get("description")})
            if r.get("job_id"):
                lines.append({"ev": "job_status", **base, "status": r.get("status"),
                              "t": r.get("last_seen")})
            if not r.get("open"):
                lines.append({"ev": "job_gone" if r.get("job_id") else "qm_gone", **base,
                              "t": r.get("last_seen"), "last_seen": r.get("last_seen")})
        self.actions = self.actions[-HISTORY_KEEP_ACTIONS:]
        lines.extend(self.actions)
        tmp = self.path.with_suffix(".jsonl.tmp")
        tmp.write_text("".join(json.dumps(x, default=str) + "\n" for x in lines), encoding="utf-8")
        os.replace(tmp, self.path)

    def observe(self, answer: dict, at: str, last_ok_at: Optional[str] = None) -> int:
        """Record what one successful poll saw; returns the number of events written."""
        if not isinstance(answer, dict) or answer.get("open_qms") is None:
            return 0
        seen: dict[str, dict] = {}
        for q in answer.get("open_qms") or []:
            seen[self._key(q.get("id"))] = {"ev": "qm_seen", "qm_id": q.get("id")}
            for j in q.get("jobs") or []:
                seen[self._key(q.get("id"), j.get("id"))] = {
                    "ev": "job_seen", "qm_id": q.get("id"), "job_id": j.get("id"),
                    "status": j.get("status"), "user": j.get("user"),
                    "description": j.get("description") or None}
        with self._lock:
            evs = []
            for k, base in seen.items():
                row = self.rows.get(k)
                if row is None or not row.get("open"):
                    evs.append({**base, "t": at})
                elif base["ev"] == "job_seen" and base.get("status") != row.get("status"):
                    evs.append({"ev": "job_status", "qm_id": base["qm_id"],
                                "job_id": base["job_id"], "status": base["status"], "t": at})
            for k, row in self.rows.items():
                if row.get("open") and k not in seen:
                    evs.append({"ev": "job_gone" if row.get("job_id") else "qm_gone",
                                "qm_id": row.get("qm_id"), "job_id": row.get("job_id"),
                                "t": at, "last_seen": last_ok_at or row.get("last_seen")})
            self._append(evs)
            # last-seen moves in RAM every poll and reaches the file with the gone event
            for k in seen:
                self.rows[k]["last_seen"] = at
            return len(evs)

    def log_action(self, action: str, detail: dict) -> None:
        with self._lock:
            self._append([{"ev": "action", "t": now_iso(), "action": action, **detail}])

    def table(self, limit: int = 500) -> list[dict]:
        with self._lock:
            rows = sorted(self.rows.values(), key=lambda r: r.get("first_seen") or "", reverse=True)
            return [dict(r) for r in rows[:limit]]

    def recent_actions(self, limit: int = 50) -> list[dict]:
        with self._lock:
            return [dict(a) for a in reversed(self.actions[-limit:])]


# ------------------------------------------------------------------ alarms

#: A temperature of exactly 0.0 is "no reading" (an empty FEM slot or an absent
#: power supply), never 0 degC [measured on one OPX1000, QOP 3.7.3: the empty
#: slots and the absent supplies read exactly 0.0 while every fitted unit read
#: 37-69 degC]. Such a value is shown as "no sensor" and never judged by an alarm.
NO_READING = 0.0


def _fem_slot(sensor: str) -> Optional[int]:
    """The FEM slot a ``FEM k`` sensor belongs to. The SDK numbers these sensors
    from 0 while FEM slots are 1..8, so ``FEM k`` is slot ``k + 1`` [measured on
    one OPX1000, QOP 3.7.3: the sensors of the two empty slots were FEM 3 and
    FEM 7, i.e. slots 4 and 8]. Any other sensor name has no slot."""
    parts = str(sensor).split()
    if len(parts) == 2 and parts[0] == "FEM" and parts[1].isdigit():
        return int(parts[1]) + 1
    return None


def temperature_rows(controller: dict) -> list[dict]:
    """One row per sensor: the raw key, a label naming the slot, the reading or
    None when the value means "no reading"."""
    rows = []
    for k, v in (controller.get("temperatures") or {}).items():
        slot = _fem_slot(k)
        rows.append({"key": k, "slot": slot,
                     "label": f"FEM slot {slot}" if slot is not None else str(k),
                     "value": None if v is None or v == NO_READING else v})
    # FEM slots in slot order first, then the other sensors by name
    return sorted(rows, key=lambda r: (r["slot"] is None, r["slot"] or 0, str(r["key"])))


def slot_temperatures(controller: dict) -> dict:
    """``{slot (str): reading or None}`` for the FEM chips on the Live tab."""
    return {str(r["slot"]): r for r in temperature_rows(controller) if r["slot"] is not None}


def offered_metrics(answer: Optional[dict]) -> list[dict]:
    """The metrics the server actually returned, as alarm candidates.

    A key is ``temp:<sensor>`` (degrees C, upper limit) or ``metric:<name>``;
    only what an answer carried is offered (D-9)."""
    out: dict[str, dict] = {}
    for c in (answer or {}).get("controllers") or []:
        for r in temperature_rows(c):
            if r["value"] is None:
                continue  # no reading: nothing to set a limit on
            k = f"temp:{r['key']}"
            out.setdefault(k, {"key": k, "label": f"{r['label']} temperature", "unit": "degC",
                               "direction": "above"})
        for m in (c.get("metrics") or {}):
            k = f"metric:{m}"
            low = m.endswith("_available_gb")
            out.setdefault(k, {"key": k, "label": m.replace("_", " "),
                               "unit": "GB" if m.endswith("_gb") else "%",
                               "direction": "below" if low else "above"})
    return sorted(out.values(), key=lambda r: r["key"])


def evaluate_alarms(answer: Optional[dict], thresholds: dict) -> list[dict]:
    fired = []
    meta = {m["key"]: m for m in offered_metrics(answer)}
    for c in (answer or {}).get("controllers") or []:
        values = {f"temp:{r['key']}": r["value"] for r in temperature_rows(c)}
        values.update({f"metric:{k}": v for k, v in (c.get("metrics") or {}).items()})
        for k, v in values.items():
            lim = thresholds.get(k)
            if lim in (None, "") or v is None:
                continue
            try:
                lim = float(lim)
            except (TypeError, ValueError):
                continue
            m = meta.get(k, {"direction": "above", "label": k, "unit": ""})
            bad = v > lim if m["direction"] == "above" else v < lim
            if bad:
                fired.append({"key": k, "controller": c.get("name"), "value": v, "limit": lim,
                              "label": m["label"], "unit": m["unit"], "direction": m["direction"]})
    return fired


# ----------------------------------------------------------------- monitor

class Monitor:
    """One poller per SM process. ``worker_factory(python, target)`` is
    injectable so tests drive it with fake answers."""

    def __init__(self, instance, worker_factory: Optional[Callable] = None,
                 clock: Callable[[], float] = time.time):
        self.instance = Path(instance)
        self.worker_factory = worker_factory or (lambda py, t: EnvWorker(py, t))
        self.clock = clock
        self._lock = threading.Lock()
        self._worker = None
        self._worker_sig = None
        self.last_answer: Optional[dict] = None
        self.last_ok_at: Optional[float] = None
        self.last_error: Optional[str] = None
        self.last_try_at: Optional[float] = None
        self.wanted_until = 0.0
        self._thread = None
        self._stop = threading.Event()
        self._histories: dict[str, History] = {}
        self.alarms: list[dict] = []
        self._toasted: set = set()
        self._fails = 0
        self._retry_at = 0.0

    # -- settings shortcuts
    def settings(self) -> dict:
        return load_settings(self.instance)

    def history(self, key: Optional[str] = None) -> Optional[History]:
        key = key or target_key(self.settings().get("target"))
        if not key:
            return None
        h = self._histories.get(key)
        if h is None:
            h = self._histories[key] = History(_dir(self.instance) / "history" / f"{key}.jsonl")
        return h

    def reset(self):
        """Settings changed: drop the worker and the cached answer."""
        with self._lock:
            if self._worker is not None:
                try:
                    self._worker.kill()
                except Exception:  # noqa: BLE001
                    pass
            self._worker = None
            self._worker_sig = None
            self.last_answer = None
            self.last_ok_at = None
            self.last_error = None
            self.last_try_at = None
            self.alarms = []
            self._fails = 0
            self._retry_at = 0.0

    # -- polling
    def _ensure_worker(self, s: dict):
        t = dict(s["target"])
        t["headers"] = load_secret_headers(self.instance, target_key(t))
        sig = (s["python"], json.dumps(t, sort_keys=True))
        if self._worker is not None and self._worker_sig == sig and \
                getattr(self._worker, "alive", lambda: True)():
            return self._worker
        if self._worker is not None:
            try:
                self._worker.kill()
            except Exception:  # noqa: BLE001
                pass
        self._worker = None
        self._worker = self.worker_factory(s["python"], t)
        self._worker_sig = sig
        return self._worker

    def _drop_worker(self, err: str):
        """The worker failed: stop its process (never leave it running) and
        back off before the next start. Caller holds ``_lock``."""
        if self._worker is not None:
            try:
                self._worker.kill()
            except Exception:  # noqa: BLE001
                pass
        self._worker = None
        self.last_error = err
        self._fails += 1
        self._retry_at = self.clock() + min(MAX_INTERVAL_S,
                                            RESTART_BACKOFF_S * 2 ** (self._fails - 1))

    def poll_once(self) -> None:
        s = self.settings()
        if not s.get("target") or not s.get("python"):
            return
        if (s["target"] or {}).get("kind", "qmm") != "qmm":
            self.last_error = NOT_QM_SERVER
            return
        with self._lock:
            if self._worker is None and self.clock() < self._retry_at:
                return  # backing off after a failure
            self.last_try_at = self.clock()
            try:
                w = self._ensure_worker(s)
                r = w.request("poll")
            except WorkerError as exc:
                self._drop_worker(str(exc))
                return
            except Exception as exc:  # noqa: BLE001
                self._drop_worker(f"The monitor worker failed: {type(exc).__name__}: {exc}")
                return
            self._fails = 0
            if not r.get("ok"):
                self.last_error = f"The server refused the poll: {r.get('error')}"
                return
            ans = r.get("result") or {}
            prev_ok = self.last_ok_at
            self.last_answer = ans
            self.last_ok_at = self.clock()
            self.last_error = None
        h = self.history(target_key(s["target"]))
        at = datetime.fromtimestamp(self.last_ok_at, timezone.utc).isoformat(timespec="seconds")
        prev_iso = (datetime.fromtimestamp(prev_ok, timezone.utc).isoformat(timespec="seconds")
                    if prev_ok else None)
        if h is not None:
            try:
                h.observe(ans, at, prev_iso)
            except OSError as exc:
                logger.warning("opx monitor: history write failed: %s", exc)
        self.alarms = evaluate_alarms(ans, s.get("thresholds") or {}) if s.get("alarms_armed") else []

    def want(self):
        """A monitor page asked: keep polling for a few intervals."""
        s = self.settings()
        self.wanted_until = self.clock() + WANT_INTERVALS * s["interval_s"]
        self._start_thread()

    def should_poll(self) -> bool:
        s = self.settings()
        return bool(s.get("target") and s.get("python")) and (
            self.clock() < self.wanted_until or s.get("alarms_armed"))

    def _start_thread(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="sm-opxmon", daemon=True)
        self._thread.start()

    def ensure_running_if_armed(self):
        if self.settings().get("alarms_armed"):
            self._start_thread()

    def _loop(self):
        while not self._stop.is_set():
            if not self.should_poll():
                with self._lock:
                    if self._worker is not None:
                        try:
                            self._worker.kill()
                        except Exception:  # noqa: BLE001
                            pass
                        self._worker = None
                return
            t0 = self.clock()
            self.poll_once()
            wait = max(0.2, self.settings()["interval_s"] - (self.clock() - t0))
            self._stop.wait(wait)

    def stop(self):
        self._stop.set()

    # -- actions
    def close(self, qm_id: Optional[str], who: str = "", qm_ids: Optional[list] = None) -> dict:
        """Close one QM (``qm_id``) or the QMs the user confirmed (``qm_ids``,
        "close all" as listed on the page). Only a QM that is open on the server
        at that moment AND was named is closed -- a QM opened after the page
        rendered is never touched. Logged in History."""
        s = self.settings()
        cap = self.capabilities()
        if not cap["close"]:
            return {"ok": False, "error": cap["close_reason"]}
        every = qm_id is None
        names = [qm_id] if not every else [str(q).strip() for q in (qm_ids or []) if str(q).strip()]
        if not names:
            return {"ok": False, "error": "Nothing named to close."}
        if not self._lock.acquire(timeout=CALL_TIMEOUT_S):
            return {"ok": False, "error": "The monitor is busy talking to the server; try again."}
        try:
            try:
                w = self._ensure_worker(s)
                r = (w.request("close_qm", qm_id=qm_id) if not every
                     else w.request("close_qms", qm_ids=names))
            except WorkerError as exc:
                r = {"ok": False, "error": str(exc)}
                self._drop_worker(str(exc))
            except Exception as exc:  # noqa: BLE001
                r = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                self._drop_worker(r["error"])
        finally:
            self._lock.release()
        closed, skipped, failed = (names if r.get("ok") else []), [], {}
        if r.get("ok") and every and isinstance(r.get("result"), dict):
            closed = list(r["result"].get("closed") or [])
            skipped = list(r["result"].get("skipped") or [])
            failed = dict(r["result"].get("failed") or {})
        ok = bool(r.get("ok")) and not failed
        err = r.get("error") or ("; ".join(f"{k}: {v}" for k, v in failed.items()) or None)
        h = self.history()
        if h is not None:
            h.log_action("close_qm" if not every else "close_all",
                         {"qm_ids": closed or names, "ok": ok, "error": err,
                          "skipped": skipped or None, "by": who or None})
        if r.get("ok"):
            self.poll_once()
        return {"ok": ok, "error": err, "closed": closed, "skipped": skipped}

    # -- what the page shows
    def capabilities(self) -> dict:
        s = self.settings()
        t = s.get("target") or {}
        ans = self.last_answer or {}
        refused = ans.get("refused") or {}
        out = {"close": False, "close_reason": "", "jobs": True, "jobs_reason": "",
               "live": True, "live_reason": ""}
        if not t:
            out.update(close_reason="No connection is set up yet (Monitor settings).")
            return out
        if t.get("kind", "qmm") != "qmm":
            out.update(close=False, close_reason=NOT_QM_SERVER, jobs=False,
                       jobs_reason=NOT_QM_SERVER, live=False, live_reason=NOT_QM_SERVER)
            return out
        if "list_open_qms" in refused:
            out.update(close_reason=f"The server refused listing QMs: {refused['list_open_qms']}")
        elif self.last_answer is None:
            out.update(close_reason="No answer from the server yet.")
        else:
            out["close"] = True
        return out

    def status(self) -> dict:
        s = self.settings()
        now = self.clock()
        age = (now - self.last_ok_at) if self.last_ok_at else None
        if not s.get("target"):
            state = "unconfigured"
            sentence = "No OPX connection is set up. Choose one under Monitor settings."
        elif not s.get("python"):
            state = "unconfigured"
            sentence = "No Python environment with the QM SDK is chosen. Choose one under Monitor settings."
        elif self.last_error and (age is None or self.last_try_at and self.last_try_at > (self.last_ok_at or 0)):
            state = "error"
            sentence = self.last_error
            if age is not None:
                sentence += f" The values below are from {age:.0f} s ago."
        elif age is None:
            state = "starting"
            sentence = "Connecting to the server..."
        elif age > STALE_INTERVALS * s["interval_s"]:
            state = "stale"
            sentence = f"No fresh answer for {age:.0f} s; the values below are from then."
        else:
            state = "ok"
            sentence = f"Updated {age:.0f} s ago."
        # an alarm is judged only on a fresh answer: stale or failed data never alarms
        alarms = list(self.alarms) if (s.get("alarms_armed") and state == "ok") else []
        return {"state": state, "sentence": sentence, "age_s": age,
                "answer": self.last_answer, "target_label": target_label(s.get("target") or {}),
                "target_kind": (s.get("target") or {}).get("kind"),
                "interval_s": s["interval_s"], "alarms": alarms,
                "alarms_armed": s.get("alarms_armed"), "capabilities": self.capabilities()}

    def new_alarm_toasts(self) -> list[dict]:
        """Alarms not yet announced in this process (a toast fires once per alarm)."""
        current = self.status()["alarms"]
        keys = {(a["controller"], a["key"]) for a in current}
        fresh = [a for a in current if (a["controller"], a["key"]) not in self._toasted]
        self._toasted = keys
        return fresh


def default_python() -> str:
    return sys.executable
