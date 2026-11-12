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
import math
import os
import queue
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from urllib.parse import urlsplit
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


def timestamp(value):
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return dt.timestamp() if dt.tzinfo else None
    except (ValueError, TypeError, OverflowError):
        return None


# Job status strings as the qm SDK reports them (job_api JOB_STATUS_MAPPING):
# "In queue", "Running", "Processing", "Done", "Canceled", "Error". The SDK also
# maps its own UNKNOWN state to "Error", so "Error" alone never proves a job ended.
FINISHED_JOB_STATUSES = ('done', 'completed', 'canceled', 'cancelled', 'failed')
UNPROVEN_JOB_STATUSES = ('error',)


def job_activity(status) -> str:
    """'finished', 'unknown' (Error: finished or unknown to the server) or 'active'.
    Any status string not known to be finished counts as active, never idle."""
    s = str(status or '').strip().lower()
    if s in FINISHED_JOB_STATUSES:
        return 'finished'
    if s in UNPROVEN_JOB_STATUSES:
        return 'unknown'
    return 'active'


# The server's get_jobs() lists at most this many jobs; a list this long is a cap, not a total.
JOB_LIST_CAP = 1000


def _answer_jobs(answer):
    ans = answer or {}
    return list(ans.get('jobs') or []) + [j for q in ans.get('open_qms') or [] for j in q.get('jobs') or []]


def server_times(answer):
    """Every job timestamp in an answer (the OPX's own clock), as POSIX seconds."""
    return [t for j in _answer_jobs(answer)
            for t in (timestamp(j.get('created_at')), timestamp(j.get('updated_at'))) if t is not None]


#: A clock measurement older than this (monotonic seconds) is not used.
SKEW_MAX_AGE_S = 600.0
#: Wall-clock and monotonic ages of one measurement may differ by this much
#: before the PC clock is taken to have stepped (the measurement is dropped).
SKEW_STEP_TOLERANCE_S = 2.0


def measure_skew(t0, t1, date_header):
    """(this PC's clock - the OPX clock) from one HTTP ``Date`` header.

    The server stamped the header at a PC time between ``t0`` (request sent)
    and ``t1`` (answer received), with a whole-second value (RFC 9110 5.6.7,
    IMF-fixdate). Returns ``{'skew_s', 'uncertainty_s'}`` or None when the
    header is absent or unreadable. uncertainty = round trip + 1 s granularity."""
    if not date_header or t0 is None or t1 is None or t1 < t0:
        return None
    try:
        from email.utils import parsedate_to_datetime
        d = parsedate_to_datetime(str(date_header))
    except (TypeError, ValueError, IndexError):
        return None
    if d is None or d.tzinfo is None:
        return None
    server = d.timestamp()
    return {'skew_s': (t0 + t1) / 2.0 - (server + 0.5), 'uncertainty_s': (t1 - t0) + 1.0}


def server_now(answer, now, skew=None):
    """A lower bound of the OPX clock's present time, or None when nothing bounds it.

    ``skew`` is a measured ``{'skew_s', 'uncertainty_s'}`` (or None): the PC
    reading is corrected by it and moved back by its uncertainty, so the bound
    can only be early (an age from it can only be short, never more idle)."""
    bounds = server_times(answer)
    if isinstance(skew, dict) and skew.get('skew_s') is not None:
        bounds.append(now - skew['skew_s'] - abs(skew.get('uncertainty_s') or 0.0))
    return max(bounds) if bounds else None


def idle_state(q, first_seen, now, minutes=120, server_clock_now=None):
    """Conservative activity judgement, including queued jobs and unknown jobs.

    PC-clock observations (SM first saw the QM) are aged against the PC clock;
    server timestamps (job created/updated) only against a lower bound of the
    server's own clock (``server_clock_now``). The two clocks are never mixed."""
    jobs = q.get('jobs')
    if jobs is None or any(job_activity(j.get('status')) != 'finished' for j in jobs):
        statuses = sorted({str(j.get('status') or 'unknown') for j in jobs or []
                           if job_activity(j.get('status')) != 'finished'})
        return {'idle': False, 'basis': 'Job status: ' + ', '.join(statuses or ['unknown']), 'last_activity': None}
    remembered = first_seen if isinstance(first_seen, dict) else {'first_seen': first_seen}
    if any(timestamp(j.get('created_at')) is None or timestamp(j.get('updated_at')) is None for j in jobs):
        return {'idle': False, 'basis': 'last activity unknown', 'last_activity': None}
    srv = [t for t in [timestamp(remembered.get('job_activity'))]
           + [timestamp(j.get(k)) for j in jobs for k in ('created_at', 'updated_at')] if t is not None]
    ages = []
    pc_first = timestamp(remembered.get('first_seen'))
    if pc_first is not None:
        ages.append((max(0, now - pc_first), 'SM first saw it'))
    if srv:
        if server_clock_now is None:
            return {'idle': False, 'basis': 'last activity unknown: the OPX clock offset is unknown',
                    'last_activity': None}
        ages.append((max(0, server_clock_now - max(srv)), 'job created/last status update (OPX clock)'))
    if not ages:
        return {'idle': False, 'basis': 'last activity unknown', 'last_activity': None}
    age, basis = min(ages)
    return {'idle': age > minutes * 60, 'last_activity': now - age,
            'basis': f'no job for {int(age // 60)} min, since {basis}'}


def value_vs_limit(value, limit):
    """Format a reading so that a breach is visible: never '70 (above limit 70)'."""
    for digits in (1, 2, 3, 4, 6):
        v = ('%.*f' % (digits, value)).rstrip('0').rstrip('.')
        if float(v) != float(limit) or float(value) == float(limit):
            return v
    return repr(float(value))


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
            raise ValueError('expected an object')
        if (d.get('target') is not None and not isinstance(d['target'], dict)) or any(
                d.get(k) is not None and not isinstance(d[k], str) for k in ('python', 'admin_url')):
            raise ValueError('invalid settings fields')
    except FileNotFoundError:
        d = {}
    except (OSError, ValueError):
        backup = p.with_suffix('.json.corrupt')
        try:
            if not backup.exists():
                backup.write_bytes(p.read_bytes())
            message = 'Settings file is corrupt or unreadable; a backup is kept as settings.json.corrupt. Save corrected Monitor settings.'
        except OSError:
            message = 'Settings file is corrupt or unreadable; backup could not be written. Check file permissions.'
        d = {'settings_error': message}
    d.setdefault("target", None)
    d.setdefault("python", None)
    d["interval_s"] = clamp_interval(d.get("interval_s", DEFAULT_INTERVAL_S))
    th = d.get("thresholds")
    d["thresholds"] = th if isinstance(th, dict) else {}
    d["alarms_armed"] = bool(d.get("alarms_armed", False))
    try:
        d['idle_minutes'] = max(0.01, min(10080, float(d.get('idle_minutes') or 120)))
        if not math.isfinite(d['idle_minutes']):
            d['idle_minutes'] = 120
    except (ValueError, TypeError):
        d['idle_minutes'] = 120
    return d


def save_settings(instance, settings: dict) -> None:
    d = _dir(instance)
    d.mkdir(parents=True, exist_ok=True)
    clean = {k: settings.get(k) for k in ("target", "python", "interval_s", "thresholds", "alarms_armed", "idle_minutes", "admin_url")}
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
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8", env=env, creationflags=flags,
            start_new_session=os.name != 'nt')
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
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(self.proc.pid), '/T', '/F'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=0x08000000, timeout=10)
            else:
                os.killpg(self.proc.pid, signal.SIGKILL)
        except OSError:
            pass
        finally:
            if self.proc.poll() is None:
                self.proc.kill()
            self.proc.wait(timeout=10)


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
        self.alarm_states = {}
        self._load()

    @staticmethod
    def _key(qm_id, job_id=None) -> str:
        return f"{qm_id or ''}|{job_id or ''}"

    def _apply(self, ev: dict):
        k = self._key(ev.get("qm_id"), ev.get("job_id"))
        t = ev.get("t")
        kind = ev.get("ev")
        if kind in ('alarm_raise', 'alarm_clear'):
            self.alarm_states[(ev.get('controller'), ev.get('key'))] = ev
            self.actions.append(ev)
            return
        if kind == "action":
            self.actions.append(ev)
            return
        row = self.rows.get(k)
        if kind in ("qm_seen", "job_seen"):
            if row is None:
                row = self.rows[k] = {"qm_id": ev.get("qm_id"), "job_id": ev.get("job_id"),
                                      "first_seen": t, "last_seen": t, "status": ev.get("status"),
                                      "open": True, "user": ev.get("user"),
                                      "description": ev.get("description"), 'job_activity': ev.get('job_activity')}
            else:
                row["open"] = True
                row["last_seen"] = t
        elif row is not None and kind == "job_status":
            row["status"] = ev.get("status")
            row["last_seen"] = t
        elif row is not None and kind == 'qm_activity':
            row['job_activity'] = ev['job_activity']
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
                          "user": r.get("user"), "description": r.get("description"), 'job_activity': r.get('job_activity')})
            if r.get("job_id"):
                lines.append({"ev": "job_status", **base, "status": r.get("status"),
                              "t": r.get("last_seen")})
            if not r.get("open"):
                lines.append({"ev": "job_gone" if r.get("job_id") else "qm_gone", **base,
                              "t": r.get("last_seen"), "last_seen": r.get("last_seen")})
        self.actions = self.actions[-HISTORY_KEEP_ACTIONS:]
        for event in self.alarm_states.values():
            if event not in self.actions:
                lines.append(event)
        lines.extend(self.actions)
        tmp = self.path.with_suffix(".jsonl.tmp")
        tmp.write_text("".join(json.dumps(x, default=str) + "\n" for x in lines), encoding="utf-8")
        os.replace(tmp, self.path)

    def observe(self, answer: dict, at: str, last_ok_at: Optional[str] = None) -> int:
        """Record what one successful poll saw; returns the number of events written."""
        if not isinstance(answer, dict) or answer.get("open_qms") is None:
            return 0
        seen: dict[str, dict] = {}
        unknown_jobs = {q['id'] for q in answer.get('open_qms') or [] if q.get('jobs') is None}
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
                if row.get('job_id') and row.get('qm_id') in unknown_jobs:
                    continue  # a refused job list is not evidence that a job disappeared
                if row.get("open") and k not in seen:
                    evs.append({"ev": "job_gone" if row.get("job_id") else "qm_gone",
                                "qm_id": row.get("qm_id"), "job_id": row.get("job_id"),
                                "t": at, "last_seen": last_ok_at or row.get("last_seen")})
            for q in answer.get('open_qms') or []:
                times = [j.get(k) for j in q.get('jobs') or [] for k in ('created_at', 'updated_at')
                         if timestamp(j.get(k)) is not None]
                if times:
                    newest = max(times, key=timestamp)
                    old = self.rows.get(self._key(q['id']), {}).get('job_activity')
                    if old is None or timestamp(newest) > (timestamp(old) or 0):
                        evs.append({'ev': 'qm_activity', 't': at, 'qm_id': q['id'], 'job_activity': newest})
            self._append(evs)
            # last-seen moves in RAM every poll and reaches the file with the gone event
            for k in seen:
                self.rows[k]["last_seen"] = at
            return len(evs)

    def log_action(self, action: str, detail: dict) -> None:
        with self._lock:
            self._append([{"ev": "action", "t": now_iso(), "action": action, **detail}])

    def observe_alarms(self, answer, thresholds, at):
        """Unknown/missing readings never clear an alarm. Rebuild state on restart."""
        fired = {(a['controller'], a['key']): a for a in evaluate_alarms(answer, thresholds)}
        with self._lock:
            events = []
            for c in answer.get('controllers') or []:
                values = {'temp:' + r['key']: r['value'] for r in temperature_rows(c)}
                values.update({'metric:' + k: v for k, v in (c.get('metrics') or {}).items()})
                for key, value in values.items():
                    if value is None or key not in thresholds:
                        continue
                    try:
                        if not math.isfinite(float(thresholds[key])):
                            continue
                    except (TypeError, ValueError):
                        continue
                    ident = (c['name'], key)
                    old = self.alarm_states.get(ident)
                    active = ident in fired
                    if active == bool(old and old['ev'] == 'alarm_raise'):
                        continue
                    events.append({'ev': 'alarm_raise' if active else 'alarm_clear', 't': at,
                                   'controller': c['name'], 'key': key, 'value': value,
                                   'limit': thresholds[key]})
            self._append(events)

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
    verified = temperature_mapping_verified(controller)
    for k, v in (controller.get("temperatures") or {}).items():
        slot = _fem_slot(k)
        rows.append({"key": k, "slot": slot,
                     "label": f"FEM slot {slot}" if slot is not None and verified else str(k),
                     'mapping_verified': verified,
                     'empty': slot is not None and verified and str(slot) not in {str(k) for k in controller.get('fems') or {}} and v == NO_READING,
                     'type': (controller.get('fems') or {}).get(str(slot), (controller.get('fems') or {}).get(slot, '')) if verified else '',
                     'reads_zero': v is not None and v == NO_READING,
                     "value": None if v is None or v == NO_READING else v})
    # FEM slots in slot order first, then the other sensors by name
    return sorted(rows, key=lambda r: (r["slot"] is None, r["slot"] or 0, str(r["key"])))


def temperature_mapping_verified(controller):
    populated = {str(k) for k in (controller.get('fems') or {})}
    sensors = {str(_fem_slot(k)) for k, v in (controller.get('temperatures') or {}).items()
               if _fem_slot(k) is not None and v not in (None, NO_READING)}
    return bool(populated) and populated == sensors


def unreadable_temperatures(answer: Optional[dict]) -> dict:
    """``{controller: [{key, label, reason}]}`` for sensors whose reading could not
    be read as a number. One bad sensor never hides the readable ones: the worker
    drops only that sensor and records ``<controller>.temperatures.<sensor>``."""
    out: dict = {}
    for name, reason in ((answer or {}).get('refused') or {}).items():
        ctrl, sep, sensor = str(name).partition('.temperatures.')
        if sep and sensor:
            slot = _fem_slot(sensor)
            out.setdefault(ctrl, []).append({'key': sensor, 'slot': slot, 'reason': str(reason),
                                             'label': sensor})
    for rows in out.values():
        rows.sort(key=lambda r: (r['slot'] is None, r['slot'] or 0, r['key']))
    return out


def slot_temperatures(controller: dict) -> dict:
    """``{slot (str): reading or None}`` for the FEM chips on the Live tab."""
    return {str(r["slot"]): r for r in temperature_rows(controller)
            if r["slot"] is not None and r['mapping_verified']}


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
            out.setdefault(k, {"key": k, "label": f"{r['label']} temperature", "unit": "°C",
                               "direction": "above"})
        for m, mv in (c.get("metrics") or {}).items():
            if mv is None:
                continue  # no reading: nothing to set a limit on
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
            lim = temperature_limit(k[5:], thresholds) if k.startswith('temp:') else thresholds.get(k)
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

class TemperatureTrends:
    """24 hours of poll samples; display keeps extrema and explicit gaps."""
    MAX_POLLS = 43201  # 24 h at the minimum supported two-second interval
    MAX_SENSORS = 256

    def __init__(self, path):
        self.path = Path(path)
        self.rows = []
        self.lock = threading.Lock()
        try:
            for line in self.path.read_text(encoding='utf-8').splitlines()[-self.MAX_POLLS:]:
                try:
                    row = json.loads(line)
                    if (isinstance(row, dict) and isinstance(row.get('t'), (float, int))
                            and math.isfinite(row['t']) and isinstance(row.get('v'), dict)
                            and isinstance(row.get('interval'), (float, int)) and row['interval'] > 0
                            and len(row['v']) <= self.MAX_SENSORS
                            and all(isinstance(v, (int, float)) and math.isfinite(v) and v != 0 for v in row['v'].values())):
                        self.rows.append(row)
                except ValueError:
                    continue
        except OSError:
            pass
        self.writes = 0

    def append(self, answer, at, interval):
        values = {c['name'] + '/' + r['key']: r['value'] for c in answer.get('controllers') or []
                  for r in temperature_rows(c) if r['value'] is not None}
        row = {'t': at, 'interval': interval, 'v': dict(list(values.items())[:self.MAX_SENSORS])}
        with self.lock:
            self.rows = [r for r in self.rows if at - 86400 <= r['t'] <= at][-self.MAX_POLLS + 1:] + [row]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.writes += 1
            if self.writes == 1 or self.writes % 500 == 0:
                tmp = self.path.with_suffix('.tmp')
                tmp.write_text(''.join(json.dumps(r) + '\n' for r in self.rows), encoding='utf-8')
                os.replace(tmp, self.path)
            else:
                with self.path.open('a', encoding='utf-8') as f:
                    f.write(json.dumps(row) + '\n')

    def series(self, now):
        with self.lock:
            rows = [r for r in self.rows if now - 86400 <= r['t'] <= now]
        keys = sorted({k for r in rows for k in r['v']})[:self.MAX_SENSORS]
        readings = [v for row in rows for v in row['v'].values()]
        y_min = min(0, math.floor(min(readings, default=0) / 20) * 20)
        y_max = max(120, math.ceil(max(readings, default=120) / 20) * 20)
        out = {}
        for key in keys:
            segments, segment, prev = [], [], None
            for row in rows:
                value = row['v'].get(key)
                gap = prev is not None and row['t'] - prev['t'] > 3 * min(row['interval'], prev['interval'])
                if value is None or gap:
                    if segment:
                        segments.append(segment)
                    segment = []
                if value is not None:
                    segment.append((row['t'], value))
                prev = row
            if segment:
                segments.append(segment)
            points = [p for seg in segments for p in seg]
            if not points:
                continue
            start, end = points[0][0], points[-1][0]
            lo, hi = min(p[1] for p in points), max(p[1] for p in points)
            paths = []
            # Split first, then downsample each segment into time buckets. Never join gaps.
            for seg in segments:
                buckets = {}
                for t, v in seg:
                    buckets.setdefault(int((t - start) / max(1, end - start) * 120), []).append((t, v))
                reduced = []
                for bucket in buckets.values():
                    reduced.extend(sorted(set((bucket[0], min(bucket, key=lambda p: p[1]),
                                               max(bucket, key=lambda p: p[1]), bucket[-1]))))
                paths.append(' '.join(f'{(t-start)/max(1,end-start)*240:.2f},{36-(v-y_min)/(y_max-y_min)*32:.2f}' for t, v in reduced))
            unchanged_start = segments[-1][-1][0]
            for t, v in reversed(segments[-1]):
                if v != segments[-1][-1][1]:
                    break
                unchanged_start = t
            out[key] = {'y_min': y_min, 'y_max': y_max, 'unchanged_min': int((end - unchanged_start) // 60),
                        'gap_min': int(max([now - end] + [b[0][0] - a[-1][0] for a, b in zip(segments, segments[1:])]) // 60), 'min': lo, 'max': hi, 'start': datetime.fromtimestamp(start, timezone.utc).isoformat(),
                        'end': datetime.fromtimestamp(end, timezone.utc).isoformat(), 'span_s': end-start,
                        'paths': paths, 'samples': len(points)}
        return out

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def connection_hint(host):
    """Connection advice without assuming how the server is reached."""
    return 'check the server connection, port or power.'


def call_failure(name, error):
    if any(word in str(error).lower() for word in ('connectionerror', 'connectionrefused', 'connection refused', 'unreachable', 'failed to connect', 'timeout', 'statuscode.unavailable')):
        return f'Server unreachable while reading {name}: {error}'
    if call_unreadable(error):
        return f'Unreadable reply for {name}: {error}'
    return f'The server refused {name}: {error}'


def call_timed_out(error):
    """No reply arrived within the time limit (not a refusal)."""
    text = str(error).lower()
    return any(word in text for word in ('no answer within', 'timed out', 'timeout'))


def call_unreadable(error):
    """The server answered, but the reply could not be read as data."""
    text = str(error).lower()
    return any(word in text for word in ('not a number', 'valueerror', 'typeerror', 'keyerror',
                                        'attributeerror', 'dictionary update'))


def failure_advice(errors, host):
    """Connection advice only when a failure looks like a connection failure."""
    texts = [str(e).lower() for e in (errors or {}).values()]
    if texts and all(call_timed_out(e) for e in texts):
        return ('The server did not answer in time; it may be slow or overloaded, '
                'or the network may be dropping replies. Check the network and try again.')
    if any(call_failure('x', e).startswith('Server unreachable') for e in texts):
        return connection_hint(host)
    if texts and all(call_unreadable(e) for e in texts):
        return 'The server answered, but some values could not be read; the affected sections are marked below.'
    if texts:
        return 'The server answered but declined some requests; check the account permissions or the QOP version.'
    return connection_hint(host)


def failure_headline(text):
    """The short cause for the headline: the text before its first ``: ``.
    The full text stays in the status line, so it is shown once in each length."""
    head = str(text or '').split(': ', 1)[0].strip().rstrip('.')
    if len(head) > 90:
        head = head[:87].rstrip() + '...'
    return head or 'No data call answered'


def failure_summary(errors):
    """One actionable cause; section tooltips retain each individual failure."""
    return next((call_failure(name, error) for name, error in errors.items()),
                'No data call answered.')


def read_admin(url, cluster_name=None, clock=time.time):
    """Only the two documented read routes, no redirects or connection headers.

    Each answer's HTTP ``Date`` header measures the OPX clock against this PC
    (``measure_skew``); the tightest measurement is returned as ``clock``."""
    if not url:
        return {'known': False, 'not_applicable': True, 'reason': 'Admin n/a; no Admin URL configured.'}
    admin_host = None
    measured = []
    try:
        parsed = urlsplit(url)
        admin_host = parsed.hostname
        if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.path not in ('', '/') or parsed.query or parsed.fragment or parsed.username:
            raise ValueError('Admin URL must be an HTTP(S) origin')
        opener = urllib.request.build_opener(_NoRedirect)
        answers = []
        for path in ('/api/v2/clusters', '/api/v2/devices/detailed'):
            t0 = clock()
            with opener.open(urllib.request.Request(url.rstrip('/') + path, method='GET'), timeout=2) as response:
                t1 = clock()
                headers = getattr(response, 'headers', None)
                m = measure_skew(t0, t1, headers.get('Date') if headers is not None else None)
                if m is not None:
                    measured.append(m)
                raw = response.read(1_000_001)
                if len(raw) > 1_000_000:
                    raise ValueError('admin response too large')
                answer = json.loads(raw)
                if not isinstance(answer, list):
                    raise ValueError('unexpected admin response shape')
                answers.append(answer)
        clusters, devices = answers
        clock_out = {'clock': min(measured, key=lambda m: m['uncertainty_s'])} if measured else {}
        selected = [c for c in clusters if c.get('name') == cluster_name] if cluster_name and cluster_name != 'any' else []
        automatic = not selected
        if not selected:
            selected = [c for c in clusters if c.get('isCurrentCluster') is True]
        if len(selected) != 1 and len(clusters) == 1:
            selected = clusters
        if len(selected) != 1:
            return {'known': False, 'reason': 'Choose an admin cluster: ' + ', '.join(str(c.get('name', 'unnamed')) for c in clusters), **clock_out}
        c = selected[0]
        ds = [d for d in devices if d.get('clusterName') == c.get('name')]
        members = c.get('devices')
        complete = (isinstance(c.get('health'), dict) and c['health'].get('status') is not None
                    and isinstance(c.get('hasUnreachableDevices'), bool)
                    and bool(members) and bool(ds)
                    and all(isinstance(d.get('unreachable'), bool) for d in members)
                    and all(d.get('status') for d in ds)
                    and {d.get('hostname') for d in members} == {d.get('hostname') for d in ds})
        return {'known': bool(complete), 'reason': 'Admin fields incomplete' if not complete else '',
                'cluster': c, 'devices': ds, 'selection': 'auto-selected from admin' if automatic else 'configured cluster', **clock_out}
    except Exception as exc:
        out = {'known': False, 'problem': True, 'reason': 'Admin unavailable; ' + connection_hint(admin_host), 'details': str(exc)[:400]}
        if measured:
            out['clock'] = min(measured, key=lambda m: m['uncertainty_s'])
        return out


def local_time(value):
    t = timestamp(value)
    if t is None:
        return 'not reported'
    local = datetime.fromtimestamp(t).astimezone()
    offset = int(local.utcoffset().total_seconds() // 60)
    hours, minutes = divmod(abs(offset), 60)
    zone = f"UTC{'+' if offset >= 0 else '-'}{hours}" + (f':{minutes:02d}' if minutes else '')
    return local.strftime('%Y-%m-%d %H:%M:%S') + f' ({zone})'


def opx_time(value):
    """An OPX-clock timestamp for people: digits only, no PC zone label (the
    caller says "OPX clock"); the instant is shown in this PC's zone."""
    t = timestamp(value)
    if t is None:
        return 'not reported'
    return datetime.fromtimestamp(t).astimezone().strftime('%Y-%m-%d %H:%M:%S')


def words(value):
    if value is None:
        return 'not reported'
    if isinstance(value, bool):
        return 'yes' if value else 'no'
    if isinstance(value, dict):
        return '; '.join(str(k) + ': ' + words(v) for k, v in value.items()) or 'none reported'
    if isinstance(value, list):
        return '; '.join(words(v) for v in value) or 'none reported'
    return str(value).replace('OPERATIONAL_CLUSTER', 'Operational').replace('OPERATIONAL_DEVICE', 'Operational').replace('_', ' ').lower()


def firmware_summary(admin):
    cluster = admin.get('cluster') or {}
    expected = {p['type']: p['version'] for p in cluster.get('packages') or []}
    devices = admin.get('devices') or []
    if not expected or not devices:
        return 'Firmware consistency: not reported'
    for d in devices:
        actual = {p['type']: p['version'] for p in d.get('packages') or []}
        if any(k not in actual for k in expected):
            return 'Firmware consistency: not reported for all packages'
        if any(actual[k] != v for k, v in expected.items()):
            return 'Firmware consistency: package versions differ'
    return 'Firmware consistency: reported packages agree'


def temperature_limit(key, thresholds):
    return thresholds.get('temp:' + key, thresholds.get('temp:all_fems') if key.startswith('FEM ') else None)


def health(answer, admin, thresholds, state):
    if state == 'unconfigured':
        return {'color': 'grey', 'reason': 'health not assessed', 'details': ''}
    ans = answer or {}
    bad, unknown = [], []
    known = bool(admin.get('known'))
    if state != 'ok':
        unknown.append('data not fresh')
    if not admin.get('known') and not admin.get('not_applicable'):
        unknown.append(admin.get('reason') or 'admin unknown')
    if admin.get('known'):
        c = admin['cluster']
        statuses = [str(c['health']['status'])] + [str(d['status']) for d in admin['devices']]
        if any('UNKNOWN' in v.upper() for v in statuses):
            unknown.append('admin status unknown')
        elif any(not v.startswith('OPERATIONAL_') for v in statuses) or c['hasUnreachableDevices'] or any(d['unreachable'] for d in c['devices']):
            bad.append('admin reports unhealthy or unreachable devices')
    values = {r['key']: r['value'] for c in ans.get('controllers') or [] for r in temperature_rows(c) if r['value'] is not None}
    for key in thresholds:
        if key.startswith('temp:') and key != 'temp:all_fems' and key[5:] not in values:
            unknown.append('a configured temperature input is unavailable')
    metric_values = {k: v for c in ans.get('controllers') or [] for k, v in (c.get('metrics') or {}).items()
                     if v is not None}
    for key, lim in thresholds.items():
        if key.startswith('metric:') and lim not in (None, '') and key[7:] not in metric_values:
            unknown.append('a configured metric input is unavailable')
    if thresholds.get('temp:all_fems') is not None:
        if not any(k.startswith('FEM ') for k in values):
            unknown.append('configured FEM readings unavailable')
        # Every populated slot must have been read; one silent slot is unwatched.
        for c in ans.get('controllers') or []:
            if (c.get('fems') or any(_fem_slot(k) is not None for k in (c.get('temperatures') or {})))                     and not temperature_mapping_verified(c):
                unknown.append(f"{c.get('name') or 'a controller'}: a populated FEM slot has no temperature reading")
    known = known or any(temperature_limit(k, thresholds) is not None for k in values)
    if evaluate_alarms(ans, thresholds):
        bad.append('temperature above limit' if any(a['key'].startswith('temp:') for a in evaluate_alarms(ans, thresholds)) else 'metric outside limit')
    if bad:
        return {'color': 'red', 'reason': bad[0], 'details': '; '.join(bad + unknown)}
    if admin.get('problem'):
        return {'color': 'grey', 'problem': True, 'reason': admin['reason'],
                'details': 'The SDK itself answers.' if state == 'ok' else ''}
    if unknown or not known:
        return {'color': 'grey', 'reason': unknown[0] if unknown else 'health not assessed: no admin URL / no limits set', 'details': '; '.join(unknown)}
    count = len(ans.get('open_qms') or [])
    return {'color': 'amber' if count else 'green', 'reason': f"{count} open QM{'' if count == 1 else 's'} claim{'s' if count == 1 else ''} resources" if count else 'admin operational' if admin.get('known') else 'reported temperatures within configured limits'}

def verdict(answer, health_state, idle_states, state=None, age_s=None):
    if state == 'unconfigured':
        return {'label': 'Health not assessed', 'reason': 'Set up a connection in Monitor settings'}
    if state == 'starting':
        return {'label': 'Checking...', 'reason': 'Waiting for the first server answer'}
    result = _occupancy_verdict(answer, health_state, idle_states)
    if state == 'stale' and age_s is not None:
        result['label'] += f' — as of {age_s:.0f} s ago'
    return result


def _occupancy_verdict(answer, health_state, idle_states):
    ans = answer or {}
    if health_state['color'] == 'red' or health_state.get('problem'):
        return {'label': 'PROBLEM', 'reason': health_state['reason']}
    qms = ans.get('open_qms')
    if qms is None:
        return {'label': 'PROBLEM', 'reason': 'Open QM list unknown'}
    active = [(q, j) for q in qms for j in q.get('jobs') or []
              if job_activity(j.get('status')) == 'active']
    if active:
        q, j = active[0]
        active_qms = {q['id'] for q, _ in active}
        return {'label': 'BUSY', 'reason': f'{len(active_qms)} QMs with active jobs' if len(active_qms) > 1 else 'FEMs ' + ', '.join(q.get('ports') or ['unknown']),
                'since': j.get('started_at') or j.get('created_at')}
    if not qms:
        return {'label': 'FREE', 'reason': 'No open QM'}
    idle = sum(bool(v['idle']) for v in idle_states.values())
    if idle == len(qms):
        return {'label': 'OPEN-IDLE ' + str(idle), 'reason': 'Close eligible QMs under Clean up'}
    if idle:
        return {'label': f'OPEN {len(qms)} · {idle} idle', 'reason': 'Close idle QMs under Clean up'}
    if all(q.get('jobs') == [] for q in qms):
        reason = 'open, no job seen yet; not idle yet'
    elif any(q.get('jobs') is None for q in qms):
        reason = 'open; job activity unavailable; idle not established'
    else:
        reason = 'open; no running job reported; idle not established'
    return {'label': 'OPEN ' + str(len(qms)), 'reason': reason}


class Monitor:
    """One poller per SM process. ``worker_factory(python, target)`` is
    injectable so tests drive it with fake answers."""

    def __init__(self, instance, worker_factory: Optional[Callable] = None,
                 clock: Callable[[], float] = time.time, mono: Optional[Callable[[], float]] = None):
        self.instance = Path(instance)
        # an injected wall clock (tests) also drives the monotonic one
        self.mono = mono or (time.monotonic if clock is time.time else clock)
        self.clock_measure: Optional[dict] = None
        self.worker_factory = worker_factory or (lambda py, t: EnvWorker(py, t))
        self.clock = clock
        self._lock = threading.Lock()
        self._worker = None
        self._worker_sig = None
        self.section_times = {}
        self.section_errors = {}
        self.last_answer: Optional[dict] = None
        self.last_ok_at: Optional[float] = None
        self.last_error: Optional[str] = None
        self.last_try_at: Optional[float] = None
        self.wanted_until = 0.0
        self.last_poll_interval = DEFAULT_INTERVAL_S
        self.jobs_until = 0.0
        self.job_limit = 100
        self._thread = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self.viewer_check_pending = False
        self._histories: dict[str, History] = {}
        self.alarms: list[dict] = []
        self._toasted: set = set()
        self._fails = 0
        self._retry_at = 0.0
        self.admin = {'known': False, 'reason': 'Admin input unknown; no fresh admin poll.'}
        self._trends = {}

    def trends(self):
        key = target_key(self.settings().get('target'))
        if not key:
            return None
        if key not in self._trends:
            self._trends[key] = TemperatureTrends(_dir(self.instance) / 'trends' / (key + '.jsonl'))
        return self._trends[key]

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
            self.section_times.clear()
            self.section_errors.clear()
            self.admin = {'known': False, 'reason': 'Admin input unknown; no fresh admin poll.'}
            self.clock_measure = None
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
        self._sections_failed(err)
        self._fails += 1
        self._retry_at = self.clock() + min(MAX_INTERVAL_S,
                                            RESTART_BACKOFF_S * 2 ** (self._fails - 1))

    def _sections_failed(self, error):
        self.section_errors = {name: error for name in ('controllers', 'open_qms', 'jobs')
                               if name != 'jobs' or name in self.section_times or self.clock() < self.jobs_until}

    def poll_once(self) -> None:
        try:
            self._poll_once()
        finally:
            self.viewer_check_pending = False

    def _poll_once(self) -> None:
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
            self.last_poll_interval = self.poll_interval()
            try:
                w = self._ensure_worker(s)
                r = w.request("poll", include_jobs=self.clock() < self.jobs_until, job_limit=self.job_limit)
            except WorkerError as exc:
                self._drop_worker(str(exc))
                return
            except Exception as exc:  # noqa: BLE001
                self._drop_worker(f"The monitor worker failed: {type(exc).__name__}: {exc}")
                return
            self._fails = 0
            if not r.get("ok"):
                self.last_error = f"The server refused the poll: {r.get('error')}"
                self._sections_failed(self.last_error)
                return
            ans = r.get("result") or {}
            selected_cluster = (s.get('target') or {}).get('cluster_name') or ans.get('cluster_name')
            self.admin = read_admin(s.get('admin_url'), selected_cluster, clock=self.clock)
            if isinstance(self.admin.get('clock'), dict):
                self.clock_measure = {**self.admin['clock'], 'at': self.clock(), 'at_mono': self.mono()}
            fields = {'controllers': 'get_devices', 'open_qms': 'list_open_qms', 'jobs': 'get_jobs'}
            self.section_errors = {field: ans.get('refused', {}).get(call, 'No answer')
                                   for field, call in fields.items() if ans.get(field) is None and (field != 'jobs' or ans.get('jobs_requested', True))}
            for field in fields:
                if ans.get(field) is not None:
                    self.section_times[field] = now_iso()
            if all(ans.get(field) is None for field in fields):
                self.last_error = failure_summary(ans.get('refused', {}))
                self.last_answer = ans
                return
            prev_ok = self.last_ok_at
            self.last_answer = ans
            self.last_ok_at = self.clock()
            self.last_error = None
            try:
                self.trends().append(ans, self.last_ok_at, s['interval_s'])
            except OSError:
                self.last_error = 'Temperature trend could not be saved.'
        h = self.history(target_key(s["target"]))
        at = datetime.fromtimestamp(self.last_ok_at, timezone.utc).isoformat(timespec="seconds")
        prev_iso = (datetime.fromtimestamp(prev_ok, timezone.utc).isoformat(timespec="seconds")
                    if prev_ok else None)
        if h is not None:
            try:
                h.observe(ans, at, prev_iso)
                if s.get('alarms_armed'):
                    h.observe_alarms(ans, s['thresholds'], at)
            except OSError as exc:
                logger.warning("opx monitor: history write failed: %s", exc)
        self.alarms = evaluate_alarms(ans, s.get("thresholds") or {}) if s.get("alarms_armed") else []

    def want(self, tab=None, job_limit=100):
        """A monitor page asked: keep polling for a few intervals."""
        s = self.settings()
        arriving = self.clock() >= self.wanted_until
        if arriving and (self.last_try_at is None or self.clock() - self.last_try_at > s['interval_s']):
            self.viewer_check_pending = True
        self.wanted_until = self.clock() + WANT_INTERVALS * s["interval_s"]
        if tab == 'jobs':
            self.jobs_until = self.wanted_until
            self.job_limit = max(100, min(1000, job_limit))
        if arriving:
            self._wake.set()
        self._start_thread()

    def poll_interval(self):
        s = self.settings()
        return max(60, s['interval_s']) if s.get('alarms_armed') and self.clock() >= self.wanted_until else s['interval_s']

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
            self._wake.clear()
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
            wait = max(0.2, self.poll_interval() - (self.clock() - t0))
            self._wake.wait(wait)

    def stop(self):
        self._stop.set()
        self._wake.set()
        with self._lock:
            if self._worker is not None:
                self._worker.kill()
                self._worker = None

    # -- actions
    def close(self, qm_id: Optional[str], who: str = "", qm_ids: Optional[list] = None, idle=False) -> dict:
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
                     else w.request("close_idle" if idle else "close_qms", qm_ids=names,
                                    **({'first_seen': {r['qm_id']: r for r in self.history().table()
                                                      if not r.get('job_id')},
                                        'idle_minutes': s['idle_minutes'],
                                        'server_now': self._server_now()} if idle else {})))
            except WorkerError as exc:
                r = {"ok": False, "error": str(exc)}
                self._drop_worker(str(exc))
            except Exception as exc:  # noqa: BLE001
                r = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                self._drop_worker(r["error"])
        finally:
            self._lock.release()
        closed, skipped, failed = [], [], {}
        if r.get("ok") and isinstance(r.get("result"), dict):
            closed = list(r["result"].get("closed") or [])
            skipped = list(r["result"].get("skipped") or [])
            failed = dict(r["result"].get("failed") or {})
        if r.get('ok') and not isinstance(r.get('result'), dict):
            failed = {qid: 'Close outcome was not verified' for qid in names}
        ok = bool(r.get("ok")) and not failed
        err = r.get("error") or ("; ".join(f"{k}: {v}" for k, v in failed.items()) or None)
        h = self.history()
        if h is not None:
            h.log_action("close_idle" if idle else ("close_qm" if not every else "close_all"),
                         {"qm_ids": closed, "ok": ok, "error": err,
                          'requested_ids': names, 'skip_reasons': (r.get('result') or {}).get('skip_reasons', {}) if every else {},
                          "skipped": skipped or None, "failed": failed, "by": who or None})
        if r.get("ok"):
            self.poll_once()
        return {"ok": ok, "error": err, "closed": closed, "skipped": skipped, "failed": failed,
                "skip_reasons": (r.get('result') or {}).get('skip_reasons', {}) if every else {}}

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
            out.update(close_reason=call_failure('list_open_qms', refused['list_open_qms']))
        elif self.last_error:
            out.update(close_reason=f"Closing is disabled until a fresh answer ({failure_headline(self.last_error)}).")
        elif ans.get("open_qms") is None or self.status_state_unfresh():
            out.update(close_reason="No answer from the server yet.")
        else:
            out["close"] = True
        out['live'] = ans.get('controllers') is not None
        out['jobs'] = ans.get('open_qms') is not None or ans.get('jobs') is not None
        # a reason only for what is NOT available; an answered section has none
        out['live_reason'] = '' if out['live'] else (
            call_failure('get_devices', refused['get_devices']) if 'get_devices' in refused
            else 'No device data has answered yet.')
        out['jobs_reason'] = '' if out['jobs'] else (
            call_failure('list_open_qms', refused['list_open_qms']) if 'list_open_qms' in refused
            else 'No job data has answered yet.')
        return out

    def clock_skew(self) -> Optional[dict]:
        """The measured (PC - OPX) clock offset, or None when unknown.

        Only a measurement from the admin server's ``Date`` header counts; it
        is dropped once older than ``SKEW_MAX_AGE_S``, when no admin URL is
        set, or when the PC clock stepped since (wall age != monotonic age)."""
        m = self.clock_measure
        if not m or not self.settings().get('admin_url'):
            return None
        mono_age = self.mono() - m['at_mono']
        wall_age = self.clock() - m['at']
        if mono_age < 0 or mono_age > SKEW_MAX_AGE_S or abs(wall_age - mono_age) > SKEW_STEP_TOLERANCE_S:
            return None
        return {'skew_s': m['skew_s'], 'uncertainty_s': m['uncertainty_s'], 'at': m['at']}

    def _server_now(self):
        return server_now(self.last_answer, self.clock(), self.clock_skew())

    def status_state_unfresh(self):
        return self.viewer_check_pending or self.last_ok_at is None or bool(self.last_error) or self.clock() - self.last_ok_at > STALE_INTERVALS * self.poll_interval()

    def status(self) -> dict:
        s = self.settings()
        now = self.clock()
        age = (now - self.last_ok_at) if self.last_ok_at else None
        if s.get('settings_error'):
            state, sentence = 'error', s['settings_error']
        elif not s.get("target"):
            state = "unconfigured"
            sentence = "No OPX connection is set up. Choose one under Monitor settings."
        elif not s.get("python"):
            state = "unconfigured"
            sentence = "No Python environment with the QM SDK is chosen. Choose one under Monitor settings."
        elif self.last_error:
            state = "error"
            sentence = self.last_error.rstrip('.') + '.'
            if age is not None:
                sentence += f" The last good read was {age:.0f} s ago."
        elif age is None or self.viewer_check_pending:
            state = "starting"
            sentence = "Checking the server..."
        elif age > STALE_INTERVALS * self.poll_interval():
            state = "stale"
            sentence = f"No fresh answer for {age:.0f} s; the values below are from then."
        else:
            state = "ok"
            sentence = f"Updated {age:.0f} s ago."
        # an alarm is judged only on a fresh answer: stale or failed data never alarms
        alarms = list(self.alarms) if (s.get("alarms_armed") and state == "ok") else []
        health_state = health(self.last_answer, self.admin, s['thresholds'], state)
        if self.last_error or self.section_errors or (self.last_answer or {}).get('refused'):
            errors = (self.last_answer or {}).get('refused') or self.section_errors
            cause = self.last_error or failure_summary(errors)
            health_state = {**health_state, 'color': 'red', 'problem': True, 'reason': failure_headline(cause), 'details': failure_advice(errors if not self.last_error or errors else {'call': self.last_error}, (s.get('target') or {}).get('host'))}
            if state == 'ok':
                sentence = f'Last reply {age:.0f} s ago; some data unavailable. {cause}'
        return {"section_times": self.section_times, "section_errors": self.section_errors, "state": state, "sentence": sentence, "age_s": age,
                'health': health_state, 'admin': self.admin,
                "answer": self.last_answer, "target_label": target_label(s.get("target") or {}),
                "target_kind": (s.get("target") or {}).get("kind"),
                "valid_for_ms": max(0, int((STALE_INTERVALS * self.poll_interval() - (age or 0)) * 1000)) if state == 'ok' else 0,
                "interval_s": self.poll_interval(), "alarms": alarms,
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
