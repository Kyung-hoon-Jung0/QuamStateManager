"""Simulate a calibration node from SM: the server side of one click (no QM import).

The lab side is ``generator/nodesim_click.py``: it builds the node's program on a
sanitized chip copy under the safety guard, then runs it on the QM-SaaS cloud
simulator only and writes ``_status.json`` per phase and one ``_result.json``.

This module owns the request checks, one run per window on the port test's
:class:`core.port_test_runs.Runs` lifecycle (heartbeat, stop file, stop line on
stdin), the live-file digest before and after the click (D-9), the honesty
labels (D-26), the result cache (D-22) and the mapping of the child's answer
to the one line a page shows.

Connection: only the cloud simulator. The connection sent to the child is
``{"saas": credentials}`` and nothing else (D-2); no local host is read from
the settings or from the chip's wiring.

Every answer is ``{"state", "line", ...}`` with ``state`` one of :data:`STATES`
(see :func:`answer`); ``line`` is the only human text and never carries the
password (D-11).
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable, Optional

from quam_state_manager.core import nodesim_spawn
from quam_state_manager.core import port_test_runs as ptr
from quam_state_manager.core.cz_gate_simulate import window_key

CHILD = Path(__file__).resolve().parent.parent / "generator" / "nodesim_click.py"

#: the window choices of the page; nothing above MAX_WINDOW_US is sent (D-41)
WINDOWS_US = (10, 20, 50, 100)
DEFAULT_WINDOW_US = 20
MAX_WINDOW_US = 100
#: the result cache holds this many entries (D-22)
CACHE_SIZE = 20
KEEP_RUNS = ptr.KEEP_RUNS
#: SM asks a click to stop after this long (estimate: a cold Rabi click at 20 us
#: adds up to about 81 s from the measured parts below)
LIMIT_S = 300.0
MIN_QM_SAAS = (1, 2, 0)
PROBE_TIMEOUT_S = 30.0

#: measured phase times in seconds (node-simulate study: W2c analyze tables for
#: copy and build, the upper end of each range; P0b report for the cloud parts)
COLD_S = {"copy": 0.041, "build": 7.99, "login": 1.0, "spawn": 62.5, "connect": 1.1, "close": 1.15}
SPAWN_S = COLD_S["spawn"]
#: wall time of the simulate step, a + b * window_us, per node family (P0b fit);
#: the family is taken from the node file's name prefix (Q-6)
FAMILY_FIT = (("06", "flux", 2.67, 0.136), ("11", "rabi", 2.75, 0.215),
              ("12", "ramsey", 4.11, 0.230), ("32a", "cz", 6.36, 0.200),
              ("36b", "cz", 6.36, 0.200))

#: D-1 refuse list: nodes with nothing to simulate here, by the name prefix
NO_SIM_PATH = "it has no simulation path."
REFUSE = {"15p": "it executes its program outside the simulator.",
          "18b": "it opens a QM on an instrument.",
          "15m": NO_SIM_PATH, "30f": NO_SIM_PATH, "17d": NO_SIM_PATH, "38": NO_SIM_PATH}
#: D-1 helper-execute set: in scope only after a static check shows no execute
HELPER_EXECUTE = ("04d", "04e", "14b", "15e", "15f", "15g", "17c", "32g", "32h", "38e")
HELPER_REASON = "it calls a helper that may execute a program; that is not checked yet."
REFUSED_LIST_LINE = "This node is not simulated here (it writes or sweeps outside one program)."

#: amplitude notes per QOP version on an MW-FEM chip, measured rows only (D-25)
AMP_NOTES = {"3_7_3": "MW-FEM amplitude 0.50x on this QOP (measured)"}
AMP_UNVERIFIED = "amplitude scale not verified for this QOP"

STATES = ("none", "blocked", "no_env", "no_library", "not_configured", "needs_targets", "refused",
          "busy", "starting", "running", "done", "node_error", "serializer", "guard",
          "chip_changed", "too_long", "timeout", "stopped", "failed")
STATES += tuple("warming ready instance no_slot spawn_failed needs_instance renew_offer renewing expired_retry "
                "lab_busy build_watchdog worker_crashed worker_restart busy_chip stopped_spawning stopped_kept "
                "idle_closed broker_failed cold_fallback lease_failed stopped_none".split())

#: the child's result kinds (child protocol, spec section 4)
KINDS = ("ok", "node_error", "serializer", "guard", "refused", "too_long", "timeout", "failed")
RESULT_KEYS = ("ok", "kind", "message", "report", "script", "script_ok", "program_chars",
               "qop_version", "window_ns", "last_adc_end_ns", "guard_lines")

LINES = {
    "none": "Not simulated yet.",
    "no_env": "Pick a Python env for this chip first; the node is built in that env.",
    "no_library": "No calibration library found for this chip. Set the calibrations folder in the project first.",
    "not_configured": "No cloud simulator login saved. Add it under Settings, Simulator login.",
    "needs_targets": "Pick at least one qubit or pair to simulate; this node has none selected.",
    "busy": "A simulation is already running in this window.",
    "chip_changed": "The live chip files changed while the node was built; the result was dropped.",
    "stopped": "Stopped. The cloud instance was closed.",
    "ready": "Node simulator ready.",
    "needs_instance": ("This chip's FEM layout differs from the open instance; it needs a new instance "
                       "(about 62 s)."),
    "renewing": "Starting a replacement cloud instance; clicks keep using the current one until it is ready.",
    "expired_retry": ("The cloud instance expired during this click; simulating again on a fresh instance "
                      "(about 62 s)."),
    "lab_busy": "The lab environment is busy with another check; try again.",
    "build_watchdog": ("The node's build took over 30 s; the simulator process was stopped and the lab lock "
                       "released."),
    "stopped_spawning": "Stopped. The cloud instance is still starting and is closed as soon as it is up.",
    "stopped_kept": "Stopped. The cloud instance stays open for the next click.",
    "stopped_none": "Stopped. No cloud instance was open.",
    "idle_closed": "The cloud instance was closed after 10 min without a click.",
    "cold_fallback": "Using a one-off simulator process this time.",
}
EMPTY_TARGETS_LINE = "This usually means no qubit or pair was selected; pick targets and simulate again."
TOO_LONG_LINE = "The cloud simulator refused a window this long."
NO_READOUT_NOTE = "No readout inside this window; try a longer window."
NUM_SHOTS_NOTE = "num_shots 1 for simulation"
STALE_LINE = "chip changed since - re-simulate"
UNREADABLE = "The simulation process gave an answer SM could not read; nothing is shown."
DISAGREE = ("The simulation process gave two answers, or a guard line that does not match "
            "its answer; nothing is shown.")

PHASE_LABELS = {
    "copy": "Copying the chip.",
    "build": "Building the node's program on the chip copy.",
    "login": "Logging in to the cloud simulator.",
    "connect": "Connecting to the cloud instance.",
    "submit": "Sending the program to the cloud simulator.",
    "wait": "Simulating on the cloud instance.",
    "report": "Reading the simulator report.",
    "close": "Closing the cloud instance.",
    "stopping": "Stopping; the cloud instance is being closed.",
}

DEFAULTS = {"id": None, "elapsed_s": None, "limit_s": None, "phase": None, "phases": [],
            "estimate_s": None, "banners": [], "result": None, "cached": False, "warm": False}


def _g(x: Any) -> str:
    try:
        return f"{float(x):g}"
    except (TypeError, ValueError):
        return str(x)


def first_line(text: Any) -> str:
    for part in str(text or "").splitlines():
        if part.strip():
            return part.strip()
    return "The simulation failed without a message."


def line(state: str, **kw) -> str:
    """The one user-facing text of a state (P1 spec 1.2, P2 spec 1)."""
    if state == "warming":
        return f"Preparing the node simulator in env {kw.get('env') or '?'} (about 5 s)."
    if state == "starting" and "in_use_here" in kw:
        return ("Starting a cloud simulator instance (about 62 s); "
                f"{kw['in_use_here']} of 3 instances of this account in use on this PC.")
    if state == "instance":
        return (f"Cloud instance open until {_hhmm(kw.get('expires_at'))}; it closes after 10 min without "
                f"a click. {kw.get('in_use_here', 0)} of 3 in use on this PC.")
    if state == "no_slot":
        return (f"All 3 cloud simulator instances of this account are in use ({kw.get('in_use_here', 3)} "
                f"by this PC). The earliest one here closes at {_hhmm(kw.get('earliest_close'))}.")
    if state == "spawn_failed":
        return "The cloud simulator instance could not start: " + first_line(kw.get("message"))
    if state == "broker_failed":
        return "The cloud instance manager could not start: " + first_line(kw.get("message"))
    if state == "lease_failed":
        return first_line(kw.get("message"))
    if state == "renew_offer":
        return f"The cloud instance closes at {_hhmm(kw.get('expires_at'))}."
    if state == "worker_crashed":
        return (f"The simulator process stopped unexpectedly (exit code {kw.get('code')}); "
                "the next click starts a fresh one.")
    if state == "worker_restart":
        return (f"A lab file changed ({kw.get('file')}); the simulator process restarts (about 5 s). "
                "The cloud instance is kept.")
    if state == "busy_chip":
        return f"The simulator is busy with chip {kw.get('name')}; this click waits."
    if state == "blocked":
        env, python, found = kw.get("env") or "?", kw.get("python") or "python", kw.get("found")
        head = (f"The simulator library in env {env} is qm-saas {found}" if found
                else f"The simulator library qm-saas is not installed in env {env}")
        return (f"{head}; this needs 1.2.0 or newer. Upgrade it in that env with: "
                f'{python} -m pip install "qm-saas>=1.2.0". SM does not install it.')
    if state == "refused":
        return f"This node is not simulated here: {kw.get('reason') or NO_SIM_PATH}"
    if state == "starting":
        return f"Starting a cloud simulator instance (about {_g(SPAWN_S)} s)."
    if state == "running":
        return PHASE_LABELS.get(kw.get("phase") or "build", "Simulating the node.")
    if state == "done":
        targets = ", ".join(kw.get("targets") or [])
        return (f"Simulated {_g(kw.get('window_us'))} us of {kw.get('node')} on {targets}. "
                "Report only, no samples.")
    if state == "node_error":
        return f"The node stopped while building its program: {kw.get('message') or ''}".rstrip()
    if state == "serializer":
        out = f"qm could not turn this program into a script: {kw.get('message') or ''}".rstrip()
        return out + ("\n" + EMPTY_TARGETS_LINE if kw.get("targets_empty") else "")
    if state == "guard":
        return f"Stopped by the safety guard: {kw.get('guard_line') or ''}".rstrip()
    if state == "too_long":
        return f"{kw.get('message') or ''}\n{TOO_LONG_LINE}".lstrip()
    if state == "timeout":
        return (f"No answer from the cloud simulator after {_g(kw.get('limit_s'))} s; "
                "the instance was closed.")
    if state == "failed":
        return first_line(kw.get("message"))
    return LINES.get(state, LINES["none"])


def _hhmm(value):
    """Format the server's expiry in local time (D-17, P2 spec 1)."""
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone().strftime("%H:%M")
    except ValueError:
        return "?"


def answer(state: str, **kw) -> dict:
    """``{"state", "line"}`` plus every contract key of spec section 2.2 (defaults
    when not given). The password, when given as ``secret``, is scrubbed."""
    if state not in STATES:
        raise ValueError(f"unknown state {state!r}")
    out = {"state": state, "line": line(state, **kw)}
    for key, default in DEFAULTS.items():
        out[key] = copy.deepcopy(kw.get(key, default))
    return scrub(out, kw.get("secret"))


def scrub(value: Any, secret: Optional[str]) -> Any:
    """*value* with every occurrence of *secret* replaced (strings in dicts/lists too)."""
    if not secret:
        return value
    if isinstance(value, str):
        return value.replace(secret, "***")
    if isinstance(value, dict):
        return {scrub(k, secret): scrub(v, secret) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v, secret) for v in value]
    return value


# --- node picker facts (no node module is imported) ----------------------------

def _token(node_file: Any) -> str:
    return Path(str(node_file)).stem.lower().split("_", 1)[0]


def refusal(info: Any) -> Optional[str]:
    """Why a scanned node (``core.node_scan.NodeInfo``) is not simulated here, or None."""
    if info is None:
        return "it is not in the calibration library."
    if getattr(info, "error", None):
        return f"it could not be read ({info.error})."
    if getattr(info, "kind", "") == "graph":
        return "it is a graph of several nodes, not one program."
    if getattr(info, "kind", "") != "node":
        return "it is not a calibration node."
    token = _token(info.file)
    if token in REFUSE:
        return REFUSE[token]
    if token in HELPER_EXECUTE:
        return HELPER_REASON
    return None


def family(node_file: Any) -> Optional[tuple]:
    """``(name, a, b)`` of the node's time-fit family, or None (Q-6)."""
    stem = Path(str(node_file)).stem.lower()
    for prefix, name, a, b in FAMILY_FIT:
        if stem.startswith(prefix):
            return name, a, b
    return None


def estimate(node_file: Any, window_us: float) -> dict:
    """The time note shown before the click (spec section 2.4, D-40)."""
    cold = sum(COLD_S.values())
    fam = family(node_file)
    if fam is None:
        return {"estimate_s": None, "family": None,
                "line": (f"No measured time for this kind of node yet. The fresh cloud instance "
                         f"part alone is about {cold:.0f} s.")}
    name, a, b = fam
    total = cold + a + b * float(window_us)
    return {"estimate_s": round(total, 1), "family": name,
            "line": (f"About {total:.0f} s with a fresh cloud instance, the sum of measured parts "
                     "(not measured end to end).")}


def warm_estimate(node_file, window_us):
    """Use the upper build range and P0b fit, labelled as an estimate (D-31, P2 spec 1)."""
    fit = family(node_file)
    if fit is None:
        return dict(estimate_s=None, line="No measured time for this kind of node yet.")
    total = .041 + .24 + fit[1] + fit[2] * float(window_us)
    return dict(estimate_s=round(total, 1),
                line=f"About {total:.1f} s with an open cloud instance (estimate).")


def library_root(calibrations_folder: Any) -> Optional[Path]:
    """Q-1: the nearest folder at or above the calibrations folder that holds ``quam_config/``."""
    if not calibrations_folder:
        return None
    try:
        start = Path(calibrations_folder).resolve()
    except OSError:
        return None
    for cand in [start, *start.parents]:
        if (cand / "quam_config").is_dir():
            return cand
    return None


# --- the qm-saas capability probe (D-16) ---------------------------------------

PROBE = ("import importlib.metadata as m\n"
         "try:\n    print(m.version('qm-saas'))\n"
         "except m.PackageNotFoundError:\n    print('')\n")


def probe_qm_saas(python: str, run: Callable = subprocess.run) -> Optional[str]:
    """The qm-saas version installed in the env (``None`` when absent). Reads
    package metadata only; nothing is imported from the package."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    done = run([python, "-I", "-c", PROBE], capture_output=True, text=True,
               timeout=PROBE_TIMEOUT_S, creationflags=flags)
    if done.returncode != 0:
        raise RuntimeError(f"the version check in that env failed (exit code {done.returncode})")
    text = (done.stdout or "").strip().splitlines()
    return text[-1].strip() if text and text[-1].strip() else None


def version_ok(found: Optional[str]) -> bool:
    m = re.match(r"\s*(\d+)\.(\d+)(?:\.(\d+))?", str(found or ""))
    if not m:
        return False
    return tuple(int(x or 0) for x in m.groups()) >= MIN_QM_SAAS


def _qop_key(version: Any) -> str:
    return "_".join(re.findall(r"\d+", str(version or "")))


# --- the child's answer --------------------------------------------------------

def check_result(result: Any, stdout: str) -> Optional[str]:
    """Why the child's ``_result.json`` cannot be trusted, or None (the
    ``parse_output`` rule of the build child, on the click child's files)."""
    if not isinstance(result, dict) or any(k not in result for k in RESULT_KEYS):
        return UNREADABLE
    if result["kind"] not in KINDS or (result["kind"] == "ok") != (result["ok"] is True):
        return UNREADABLE
    lines = result["guard_lines"]
    if not isinstance(lines, list) or not all(isinstance(x, str) and x.startswith("GUARD FAIL ")
                                              for x in lines):
        return UNREADABLE
    printed = [x for x in (stdout or "").splitlines() if x.startswith("GUARD FAIL ")]
    answers = 0
    for raw in (stdout or "").splitlines():
        try:
            value = json.loads(raw)
        except ValueError:
            continue
        if isinstance(value, dict) and {"ok", "kind"} <= value.keys():
            answers += 1
    if answers > 1 or printed != lines:
        return DISAGREE
    if (result["kind"] == "guard") != bool(lines):
        return DISAGREE
    return None


def _clean_phases(raw: Any) -> list:
    out = []
    for p in raw if isinstance(raw, list) else []:
        if isinstance(p, dict) and isinstance(p.get("name"), str) and \
                isinstance(p.get("s"), (int, float)) and not isinstance(p.get("s"), bool):
            out.append({"name": p["name"], "s": round(float(p["s"]), 3)})
    return out


def _read_json(path: Path) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


#: tries of one status read; the child replaces ``_status.json`` atomically, but on Windows a reader can still
#: meet a sharing violation or a half-visible file for a moment (piece B hand-back)
STATUS_TRIES = 3
STATUS_RETRY_S = 0.05


def _status_bytes(path: Path) -> bytes:
    """One share-delete read of the status file. On Windows the child's replace of the file can still fail
    while any reader holds it open (share-delete or not, measured), so the child retries its replace."""
    from quam_state_manager.core import safe_io
    with safe_io.open_shared(path) as f:
        return f.read()


def _read_status(path: Path, tries: int = STATUS_TRIES) -> Optional[dict]:
    """The child's ``_status.json``, retried on OSError and on bad JSON; None when no try gave a dict."""
    for i in range(max(1, tries)):
        try:
            got = json.loads(_status_bytes(Path(path)).decode("utf-8"))
        except OSError:
            if not Path(path).exists():
                return None
            got = None
        except ValueError:
            got = None
        if isinstance(got, dict):
            return got
        if i + 1 < tries:
            time.sleep(STATUS_RETRY_S)
    return None


def _read_text(path: Path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _scrub_files(work: Path, secret: Optional[str]) -> None:
    """Rewrite every text file of the run folder that carries the password (D-11)."""
    if not secret:
        return
    for path in Path(work).iterdir():
        if not path.is_file():
            continue
        text = _read_text(path)
        if secret in text:
            path.write_text(text.replace(secret, "***"), encoding="utf-8")


def _write_json(path: Path, data: Any) -> None:
    tmp = Path(path).with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, path)


# --- the cache (D-22) ----------------------------------------------------------

def cache_key(script: str, window_ns: int, qop: Any) -> str:
    """sha256(config + script + window_ns + QOP version); the script of D-24
    carries the generated config, so the script text holds both."""
    blob = json.dumps([str(script or ""), int(window_ns), str(qop or "")])
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def input_key(chip_dir: Path, node_file: Path, library: Path, overrides: dict,
              window_ns: int, qop: Any, python: str) -> str:
    """What SM can know before the build: the chip copy, the node file, the
    library's source fingerprints, the overrides, window, QOP and env. Equal
    inputs build the same script (PYTHONHASHSEED pinned, D-37)."""
    h = hashlib.sha256()
    for name in ("state.json", "wiring.json"):
        h.update(Path(chip_dir, name).read_bytes())
    h.update(Path(node_file).read_bytes())
    for folder in ("calibration_utils", "quam_config"):
        for path in sorted((Path(library) / folder).rglob("*.py")):
            st = path.stat()
            h.update(f"{path.relative_to(library).as_posix()}:{st.st_mtime_ns}:{st.st_size};".encode())
    h.update(json.dumps([overrides, int(window_ns), str(qop or ""), str(python)],
                        sort_keys=True).encode("utf-8"))
    return h.hexdigest()


class Cache:
    """At most :data:`CACHE_SIZE` entries, least recently used out first."""

    def __init__(self, size: int = CACHE_SIZE):
        self.size = size
        self.entries: OrderedDict = OrderedDict()
        self.by_input: dict = {}
        self._lock = threading.Lock()

    def get(self, ikey: str) -> Optional[dict]:
        with self._lock:
            key = self.by_input.get(ikey)
            if key is None or key not in self.entries:
                return None
            self.entries.move_to_end(key)
            return copy.deepcopy(self.entries[key])

    def put(self, ikey: str, key: str, value: dict) -> None:
        with self._lock:
            self.entries[key] = copy.deepcopy(value)
            self.entries.move_to_end(key)
            self.by_input[ikey] = key
            while len(self.entries) > self.size:
                old, _ = self.entries.popitem(last=False)
                self.by_input = {i: k for i, k in self.by_input.items() if k != old}


# --- runs ----------------------------------------------------------------------

class ClickRuns(ptr.Runs):
    """The port test's run lifecycle for the node simulations of one window."""

    def __init__(self, instance, window: str):
        self.window = window_key(window)
        super().__init__(instance, resume=False)

    def _runs_dir(self) -> Path:
        d = Path(self.instance) / "nodesim" / self.window
        d.mkdir(parents=True, exist_ok=True)
        return d

    def busy(self) -> bool:
        cur = self.current
        return bool(cur and cur["proc"].poll() is None)

    def status(self, rid: Optional[str] = None) -> dict:
        """The heartbeat touch and the run clock only (P2 spec 2.5). The port test's status also opens ``_status.json``;
        every open of it can make the child's replace of that file fail on Windows, so the click reads it
        once per poll, itself (``_read_status``)."""
        run = self.current
        if not run or (rid and run["id"] != rid):
            return {"state": "none"}
        run["seen"] = time.time()
        return {"id": run["id"], "state": "running" if run["proc"].poll() is None else "done",
                "elapsed_s": round(time.time() - run["started"], 1), "stopping": run["stop_at"] is not None}

    def launch(self, python: str, connection: dict, request: dict, meta: dict, *,
               popen: Callable, cwd: str, env: dict) -> str:
        """Start the click child (child protocol: two stdin lines, stdin kept open)."""
        with self._lock:
            if self.busy():
                raise RuntimeError("busy")
            rid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
            work = self._runs_dir() / rid
            work.mkdir()
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
            with open(work / "stdout.log", "w", encoding="utf-8") as out, \
                    open(work / "stderr.log", "w", encoding="utf-8") as err:
                proc = popen([python, "-B", str(CHILD), str(work)], stdin=subprocess.PIPE,
                             stdout=out, stderr=err, text=True, encoding="utf-8",
                             creationflags=flags, cwd=cwd, env=env)
            try:
                proc.stdin.write(json.dumps(connection) + "\n")
                proc.stdin.write(json.dumps(request) + "\n")
                proc.stdin.flush()
            except (OSError, ValueError, AttributeError):
                pass
            saved = self._saved(rid, proc, python, meta)
            _write_json(work / "_run.json", saved)
            self.current = {**saved, "work": work, "proc": proc, "seen": time.time(), "stop_at": None}
            threading.Thread(target=self._watch, args=(self.current,), daemon=True).start()
            self._prune()
            return rid

    @staticmethod
    def _saved(rid, proc, python, meta) -> dict:
        try:
            import psutil
            created = psutil.Process(proc.pid).create_time()
        except Exception:  # noqa: BLE001 - a fake process in tests
            created = None
        return {"id": rid, "pid": getattr(proc, "pid", None), "created": created, "python": python,
                "meta": dict(meta), "started": time.time(), "hardware_target": None,
                "SIMULATED": True}

    def record(self, meta: dict, result: dict, final: dict) -> str:
        """A finished click with no process (a cache hit): a run folder like the others."""
        with self._lock:
            rid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
            work = self._runs_dir() / rid
            work.mkdir()
            _write_json(work / "_run.json", {"id": rid, "meta": dict(meta), "started": time.time(),
                                             "hardware_target": None, "SIMULATED": True,
                                             "cached": True})
            _write_json(work / "_result.json", result)
            _write_json(work / "_answer.json", {**final, "id": rid})
            self._prune()
            return rid


def login_version(credentials: Optional[dict], environ: Any, saved_version: Any) -> Optional[dict]:
    """D-51: attach the QOP version and its source to the login the port-test ladder chose.

    The saved login keeps its saved version ("saved"). An environment login (SAAS_EMAIL / SAAS_PASSWORD / SAAS_HOST
    all set; they win over the saved login) uses SAAS_VERSION ("env"), else the saved login's version
    ("env_saved"), else none ("env", so the refusal names SAAS_VERSION).
    """
    if not credentials:
        return credentials
    out = dict(credentials)
    if "version" in credentials:
        out["version_source"] = "saved"
        return out
    env_version = str(environ.get("SAAS_VERSION") or "").strip()
    saved = saved_version.strip() if isinstance(saved_version, str) else ""
    if env_version:
        out.update(version=env_version, version_source="env")
    elif saved:
        out.update(version=saved, version_source="env_saved")
    else:
        out.update(version=None, version_source="env")
    return out


class Manager:
    """Every window's :class:`ClickRuns`, the jobs in flight, the finished
    answers and the cache. ``popen`` starts the child, ``probe`` reads the
    qm-saas version, ``make_copy`` / ``remove_copy`` handle the chip copy."""

    def __init__(self, instance, *, popen: Optional[Callable] = None,
                 probe: Callable = probe_qm_saas, make_copy: Optional[Callable] = None,
                 remove_copy: Optional[Callable] = None, digest: Callable = nodesim_spawn.live_digest,
                 limit_s: float = LIMIT_S, pool=None):
        """Use the warm pool by default; an injected cold transport retains P1 tests (P2 spec 2.7)."""
        from quam_state_manager.core import chip_copy
        self.instance = Path(instance)
        self.popen = popen or _owned_popen
        self.probe = probe
        self.make_copy = make_copy or (lambda store: chip_copy.make_chip_copies(store, (), prefix="sm_nodesim_"))
        self.remove_copy = remove_copy or chip_copy.remove_chip_copies
        self.digest = digest
        self.limit_s = limit_s
        self.cache = Cache()
        self._lock = threading.RLock()
        self.windows: dict = {}
        self.jobs: dict = {}
        self.final: dict = {}
        self.latest: dict = {}
        if pool is None and popen is None:
            from quam_state_manager.core.nodesim_pool import WarmPool
            pool = WarmPool(instance, probe=probe)
        self.pool = pool
        self.warm_jobs: dict = {}
        self.intent_answers: dict = {}
        self.intent_at: dict = {}
        self.latest_at: dict = {}

    def runs(self, window: Any) -> ClickRuns:
        key = window_key(window)
        with self._lock:
            if key not in self.windows:
                self.windows[key] = ClickRuns(self.instance, key)
            return self.windows[key]

    # -- start -------------------------------------------------------------

    def _version_refused(self, saas: Optional[dict]) -> Optional[dict]:
        """D-51: a warm login without a QOP version is refused at the entry, before any copy or spawn."""
        from quam_state_manager.core.nodesim_pool import version_refusal
        line = version_refusal(saas)
        return answer("spawn_failed", message=line) if line else None

    def start(self, window: Any, *, python: Optional[str], env_name: str, library: Optional[Path],
              info: Any, targets: Any, choices: list, window_us: Any, saas: Optional[dict],
              store: Any, live_folder: Any) -> dict:
        """Check, copy, digest and spawn one click; the answer (spec section 2.2)."""
        if not python:
            return answer("no_env")
        if library is None:
            return answer("no_library")
        if not saas or not all(str(saas.get(k) or "").strip() for k in ("email", "host", "password")):
            return answer("not_configured")
        if self.pool is not None:
            refused = self._version_refused(saas)
            if refused:
                return refused
        secret = saas["password"]
        runs = self.runs(window)
        if runs.busy():
            return answer("busy")
        reason = refusal(info)
        if reason:
            return answer("refused", reason=reason)
        if not isinstance(targets, list) or not targets:
            return answer("needs_targets")
        unknown = [str(t) for t in targets if not isinstance(t, str) or t not in choices]
        if unknown:
            return answer("failed", message="These targets are not on this chip: " + ", ".join(unknown))
        try:
            w_us = float(window_us)
        except (TypeError, ValueError):
            return answer("failed", message="The window must be a number of microseconds.")
        if not w_us > 0:
            return answer("failed", message="The window must be a number of microseconds.")
        if w_us > MAX_WINDOW_US:
            return answer("too_long", message=f"A {_g(w_us)} us window is above the "
                                              f"{MAX_WINDOW_US} us limit.")
        window_ns = int(round(w_us * 1000))
        try:
            found = self.probe(python)
        except Exception as exc:  # noqa: BLE001 - shown, never guessed
            return answer("failed", message=f"Could not read the qm-saas version in env {env_name}: {exc}",
                          secret=secret)
        if not version_ok(found):
            return answer("blocked", env=env_name, python=python, found=found)
        node_file = Path(info.file)
        est = estimate(node_file, w_us)
        t0 = time.monotonic()
        try:
            copies = self.make_copy(store)
        except Exception as exc:  # noqa: BLE001 - CopyError and friends
            return answer("failed", message=f"The chip copy could not be made: {exc}", secret=secret)
        copy_phase = {"name": "copy", "s": round(time.monotonic() - t0, 3)}
        keep = False
        try:
            scratch, chip = Path(copies.root).resolve(), Path(copies.after).resolve()
            if live_folder:
                live = Path(live_folder).resolve()
                if live.is_relative_to(scratch) or scratch.is_relative_to(live):
                    raise ValueError("live folder and scratch must not overlap")
            nodesim_spawn.prepare_scratch(scratch, chip)
            overrides = {getattr(info, "targets_name", "qubits") or "qubits": list(targets), "num_shots": 1}
            qop = saas.get("version") or ""
            mw = _has_mw(chip)
            ikey = input_key(chip, node_file, library, overrides, window_ns, qop, python)
            meta = {"target": "saas", "node": node_file.name, "node_sha": _sha8(node_file),
                    "targets": list(targets), "window_us": w_us, "window_ns": window_ns,
                    "seq": getattr(copies, "seq", None), "store_id": getattr(copies, "store_id", None),
                    "env": env_name, "qop": qop, "overrides": overrides}
            hit = self.cache.get(ikey)
            if hit is not None:
                t1 = time.monotonic()
                result = hit["result"]
                phases = [copy_phase, {"name": "cache", "s": round(time.monotonic() - t1, 3)}]
                final = self._done(result, meta, phases, mw, cached=True)
                rid = runs.record(meta, result, final)
                final["id"] = rid
                with self._lock:
                    self.final[rid] = final
                    self._mark_latest(runs.window, rid)
                return copy.deepcopy(final)
            before = self.digest(live_folder) if live_folder else None
            if self.pool is not None:
                return self._warm_start(window, python, library, live_folder, saas, copies, meta,
                                        ikey, before, mw, copy_phase, est, node_file)
            request = {"scratch": str(scratch), "chip_copy": str(chip), "library": str(library),
                       "node": str(node_file), "targets": list(targets), "window_ns": window_ns,
                       "overrides": overrides, "saas_host": saas.get("host") or ""}
            env = nodesim_spawn.pinned_env(scratch, library, chip)
            try:
                rid = runs.launch(python, {"saas": dict(saas)}, request, meta,
                                  popen=self.popen, cwd=str(scratch), env=env)
            except RuntimeError:
                return answer("busy")
            keep = True
            job = {"rid": rid, "runs": runs, "copies": copies, "secret": secret, "before": before,
                   "live": live_folder, "meta": meta, "library": str(library), "copy_phase": copy_phase, "mw": mw,
                   "ikey": ikey, "estimate_s": est["estimate_s"], "t0": time.monotonic()}
            with self._lock:
                self.jobs[rid] = job
                self._mark_latest(runs.window, rid)
            threading.Thread(target=self._reap, args=(job,), daemon=True).start()
            return answer("running", id=rid, phase="build", phases=[copy_phase],
                          estimate_s=est["estimate_s"], limit_s=self.limit_s, elapsed_s=0.0)
        except Exception as exc:  # noqa: BLE001 - one honest line, the copy goes
            return answer("failed", message=f"The simulation could not start: {exc}", secret=secret)
        finally:
            if not keep:
                self.remove_copy(copies)

    def _warm_start(self, window, python, library, live, saas, copies, meta, ikey, before, mw,
                    copy_phase, est, node_file):
        """Start an asynchronous warm click; share P1 cache and result rendering (D-22, P2 spec 2)."""
        from quam_state_manager.core.nodesim_pool import PoolError, clean
        rid = uuid.uuid4().hex
        job = dict(stop=threading.Event(), answer=answer("running", id=rid, phase="copy"),
                   window=window_key(window), started=time.monotonic(), seen=time.monotonic())
        with self._lock:
            if any(j["window"] == job["window"] and not j["done"] for j in self.warm_jobs.values()):
                return answer("busy")
            job["done"] = False
            # Review S P3-5: finished jobs no window still points at are dropped, so the table stays bounded.
            keep = set(self.latest.values())
            for old in [k for k, j in self.warm_jobs.items() if j["done"] and k not in keep]:
                self.warm_jobs.pop(old, None)
            self.warm_jobs[rid] = job
            self._mark_latest(job["window"], rid)
        # The start caller removes its transient copies in finally; the worker receives immutable bytes here.
        from types import SimpleNamespace
        detached = self.instance / "nodesim" / job["window"] / rid / "copy"
        detached.mkdir(parents=True)
        for name in ("state.json", "wiring.json"):
            (detached / name).write_bytes((Path(copies.after) / name).read_bytes())
        fixed = SimpleNamespace(after=detached, seq=copies.seq, store_id=copies.store_id)

        def notify(state, **fields):
            """Only scrubbed server lines cross the HTTP boundary (D-11, D-19)."""
            phases = [copy_phase] + fields.pop("phases", [])
            job["answer"] = clean(answer(state, id=rid, phases=phases, warm=True,
                                         elapsed_s=round(time.monotonic() - job["started"], 1), **fields),
                                  self.pool.secrets)

        def run():
            """Always finalise the job, so a failure in the fallback never leaves the window busy (review S P2-3)."""
            try:
                body()
            except Exception as exc:
                job["answer"] = clean(answer("failed", message=str(exc), id=rid),
                                      self.pool.secrets + [saas.get("password")])
            finally:
                job["done"] = True

        def body():
            """Complete and record the verdict off the request thread (D-9, D-22)."""
            import shutil
            try:
                request = dict(click_id=rid, node=str(node_file), targets=meta["targets"],
                               overrides=meta["overrides"], window_ns=meta["window_ns"], wait_cap_s=self.limit_s)
                result = self.pool.run(python=python, library=library, live=live, credentials=saas,
                                       copies=fixed, request=request, stop=job["stop"], notify=notify)
                if before is not None and self.digest(live) != before:
                    final = answer("chip_changed", id=rid)
                elif result["kind"] == "ok":
                    final = self._done(result, meta, job["answer"].get("phases", []), mw, cached=False)
                    self.cache.put(ikey, cache_key(result["script"], meta["window_ns"], meta["qop"]),
                                   {"result": result})
                else:
                    state = result["kind"] if result["kind"] in STATES else "failed"
                    final = answer(state, message=result.get("message"), targets_empty=not meta["targets"])
                result = clean(result, self.pool.secrets)
                work = detached.parent
                _write_json(work / "_run.json", {"id": rid, "meta": meta, "SIMULATED": True})
                _write_json(work / "_result.json", result)
            except PoolError as exc:
                from quam_state_manager.core.nodesim_pool import worker_key
                key = worker_key(python, library, live)
                if self.pool.allow_cold(exc.state, key):
                    notify("cold_fallback")
                    from types import SimpleNamespace
                    fixed.root = detached.parent
                    cold = Manager(self.instance, popen=self.popen, probe=self.probe,
                                   make_copy=lambda store: fixed, remove_copy=lambda copies: None,
                                   digest=self.digest, limit_s=self.limit_s)
                    cold.cache = self.cache
                    current = cold.start(window, python=python, env_name=meta["env"], library=library,
                                         info=SimpleNamespace(file=str(node_file), kind="node",
                                                              targets_name=next(iter(meta["overrides"]))),
                                         targets=meta["targets"], choices=meta["targets"], window_us=meta["window_us"],
                                         saas=saas, store=None, live_folder=live)
                    deadline = time.monotonic() + self.limit_s + 10
                    while current["state"] in ("running", "starting") and time.monotonic() < deadline:
                        if job["stop"].wait(.05):
                            cold.stop(window, current["id"])
                        current = cold.status(window, current["id"])
                        job["answer"] = current
                    final = current
                    final.setdefault("banners", []).append(LINES["cold_fallback"])
                    cold_work = cold.run_folder(current.get("id"))
                    if cold_work:
                        _write_json(detached.parent / "_run.json", {"id": rid, "meta": meta, "SIMULATED": True})
                        result = _read_json(cold_work / "_result.json")
                        if result:
                            _write_json(detached.parent / "_result.json", result)
                else:
                    final = answer(exc.state if exc.state in STATES else "broker_failed", **exc.fields)
            except Exception as exc:  # transport errors are scrubbed, never logged
                final = answer("failed", message=str(exc))
            finally:
                shutil.rmtree(detached, ignore_errors=True)
            final = clean({**final, "id": rid}, self.pool.secrets + [saas.get("password")])
            _write_json(detached.parent / "_answer.json", final)
            self.runs(window)._prune()
            job["answer"], job["done"] = final, True
        thread = threading.Thread(target=run, daemon=True)
        job["thread"] = thread
        thread.start()

        def watch():
            """Carry the page heartbeat and total deadline over to warm jobs (P2 spec 2.5, D-18)."""
            deadline = time.monotonic() + self.limit_s
            while not job["done"] and time.monotonic() < deadline:
                if time.monotonic() - job["seen"] > 12 or job["stop"].wait(.2):
                    break
            if not job["done"]:
                job["stop"].set()
        threading.Thread(target=watch, daemon=True).start()
        return dict(job["answer"])

    def intent(self, window, *, python, env_name, library, store, live_folder, saas, explicit=False, renew=False):
        """Panel opening prepares imports; only explicit intent acquires with setting off (D-12)."""
        from quam_state_manager.core.nodesim_pool import PoolError, clean
        if not self.pool:
            return answer("none")
        if not python:
            return answer("no_env")
        if not library:
            return answer("no_library")
        if not saas:
            return answer("not_configured")
        key = window_key(window)
        if renew or explicit or self.pool.keep_warm:
            # D-51: an intent that acquires checks the version first; a panel-open import warm needs none.
            refused = self._version_refused(saas)
            if refused:
                self._set_intent(key, refused)
                return refused
        self._set_intent(key, answer("warming", env=env_name))

        def run():
            """Keep the page responsive while imports or an intent spawn runs (D-19)."""
            copies = None
            try:
                copies = self.make_copy(store)
                operation = self.pool.renew if renew else self.pool.intent
                fields = {} if renew else dict(explicit=explicit)
                operation(python=python, library=library, live=live_folder, credentials=saas,
                          copies=copies, **fields,
                          notify=lambda state, **kw: self._set_intent(
                              key, clean(answer(state, **kw), self.pool.secrets)))
                self._set_intent(key, answer("ready"))
            except PoolError as exc:
                state = exc.state if exc.state in STATES else "broker_failed"
                self._set_intent(key, answer(state, **exc.fields))
            except Exception as exc:
                self._set_intent(key, answer("failed", message=str(exc), secret=saas.get("password")))
            finally:
                if copies:
                    self.remove_copy(copies)
        threading.Thread(target=run, daemon=True).start()
        return self.intent_answers[key]

    def _set_intent(self, key, value):
        """Record an intent answer with its time, for status's newest-answer rule (review S P2-5)."""
        self.intent_at[key] = time.monotonic()
        self.intent_answers[key] = value

    def _mark_latest(self, key, rid):
        """Record a window's newest click and its time (review S P2-5)."""
        self.latest_at[key] = time.monotonic()
        self.latest[key] = rid

    # -- end -----------------------------------------------------------------

    def _reap(self, job: dict, poll_s: float = 0.5) -> None:
        """Ask the run to stop past the limit; finalize once it ended (P2 spec 2.5, D-18)."""
        runs, rid = job["runs"], job["rid"]
        run = runs.current
        cap = self.limit_s + ptr.SAAS_STOP_GRACE_S + 60
        while run is not None and run["id"] == rid and run["proc"].poll() is None:
            el = time.monotonic() - job["t0"]
            if el > self.limit_s and run["stop_at"] is None:
                runs._request_stop(run, "time limit")
            if el > cap:
                try:
                    run["proc"].kill()
                except Exception:  # noqa: BLE001
                    pass
                break
            time.sleep(poll_s)
        self.finalize(rid)

    def finalize(self, rid: str) -> Optional[dict]:
        """Turn an ended child into its final answer, once (idempotent)."""
        with self._lock:
            if rid in self.final:
                return self.final[rid]
            job = self.jobs.get(rid)
            if job is None:
                return None
            run = job["runs"].current
            if run is None or run["id"] != rid or run["proc"].poll() is None:
                return None
            self.jobs.pop(rid, None)
        try:
            final = self._final(job, run)
        finally:
            self.remove_copy(job["copies"])
        work = run["work"]
        final = scrub(final, job["secret"])
        _scrub_files(work, job["secret"])
        _write_json(work / "_answer.json", final)
        with self._lock:
            self.final[rid] = final
        return final

    def _final(self, job: dict, run: dict) -> dict:
        work, meta, secret = run["work"], job["meta"], job["secret"]
        elapsed = round(time.monotonic() - job["t0"], 1)
        status = _read_status(work / "_status.json") or job.get("last_status") or {}
        result = _read_json(work / "_result.json")
        # the result carries the child's full phase list (a node error ends without a last status write)
        has = isinstance(result, dict) and isinstance(result.get("phases"), list)
        ran = result["phases"] if has else status.get("phases")
        phases = [job["copy_phase"]] + _clean_phases(ran)
        base = {"id": job["rid"], "elapsed_s": elapsed, "limit_s": self.limit_s, "phases": phases,
                "estimate_s": job["estimate_s"], "secret": secret}
        if job["live"]:
            try:
                after = self.digest(job["live"])
            except Exception:  # noqa: BLE001 - an unreadable live pair is not "unchanged"
                after = None
            if after != job["before"]:
                return answer("chip_changed", **base)
        stopped = run.get("stop_at") is not None
        if result is None:
            if stopped and run.get("stop_why") == "time limit":
                return answer("timeout", **{**base, "limit_s": self.limit_s})
            if stopped:
                return answer("stopped", **base)
            code = getattr(run["proc"], "returncode", None)
            return answer("failed", message=f"The simulation process ended without an answer (exit code {code}).",
                          **base)
        why = check_result(result, _read_text(work / "stdout.log"))
        if why:
            return answer("failed", message=why, **base)
        kind = result["kind"]
        if kind != "ok" and stopped:
            if run.get("stop_why") == "time limit":
                return answer("timeout", **{**base, "limit_s": self.limit_s})
            return answer("stopped", **base)
        message = scrub(str(result.get("message") or ""), secret)
        if kind == "node_error":
            return answer("node_error", message=message, **base)
        if kind == "serializer":
            return answer("serializer", message=message, targets_empty=not meta["targets"], **base)
        if kind == "guard":
            return answer("guard", guard_line=result["guard_lines"][0], **base)
        if kind == "refused":
            return answer("refused", reason=message, **base)
        if kind == "too_long":
            return answer("too_long", message=message, **base)
        if kind == "timeout":
            wait = [p["s"] for p in phases if p["name"] == "wait"]
            return answer("timeout", **{**base, "limit_s": wait[-1] if wait else elapsed})
        if kind == "failed":
            return answer("failed", message=message or "The simulation failed without a message.", **base)
        if not isinstance(result.get("report"), dict):
            return answer("failed", message="The cloud simulator gave no report.", **base)
        if "library_file" in result:
            try:
                nodesim_spawn.check_library(result, job["library"])
            except (RuntimeError, OSError, TypeError) as exc:
                return answer("failed", message="The node loaded a library from outside the chosen root "
                                                f"({type(exc).__name__}).", **base)
        clean = scrub(result, secret)
        final = self._done(clean, meta, phases, job["mw"], cached=False, base=base)
        key = cache_key(clean.get("script") or "", meta["window_ns"], clean.get("qop_version") or meta["qop"])
        self.cache.put(job["ikey"], key, {"result": clean})
        return final

    def _done(self, result: dict, meta: dict, phases: list, mw: bool, *, cached: bool,
              base: Optional[dict] = None) -> dict:
        qop = result.get("qop_version") or meta.get("qop") or "unknown"
        banners = ["REDUCED SIMULATION"]
        if result.get("partial_pass") is not False:
            banners.append("partial pass")
        if result.get("reset_skipped"):
            banners.append("reset skipped")
        if mw:
            banners.append(AMP_NOTES.get(_qop_key(qop), AMP_UNVERIFIED))
        if not result.get("script_ok"):
            banners.append("script incomplete")
        report = result.get("report") or {}
        notes = [NUM_SHOTS_NOTE]
        if not report.get("adc_acquisitions"):
            notes.append(NO_READOUT_NOTE)
        when = time.strftime("%Y-%m-%d %H:%M:%S")
        provenance = {"seq": meta.get("seq"), "store_id": meta.get("store_id"), "node": meta["node"],
                      "node_sha": meta["node_sha"], "qop": qop, "time": when, "env": meta.get("env"),
                      "overrides": meta.get("overrides"), "num_shots": NUM_SHOTS_NOTE,
                      "text": (f"Simulated from chip copy seq {meta.get('seq')}, {meta['node']} @ "
                               f"{meta['node_sha']}, QOP {qop}, {when}"),
                      "stale": False, "stale_line": None}
        provenance["line"] = provenance["text"]
        out = {"report": report, "lanes": None, "marks": None, "notes": notes, "cannot_draw": [],
               "script_ok": bool(result.get("script_ok")), "provenance": provenance,
               "window_ns": meta["window_ns"], "last_adc_end_ns": result.get("last_adc_end_ns"),
               "program_chars": result.get("program_chars")}
        kw = dict(base or {})
        kw.pop("secret", None)
        kw.setdefault("phases", phases)
        return answer("done", node=meta["node"], targets=meta["targets"], window_us=meta["window_us"],
                      banners=banners, result=out, cached=cached, **kw)

    # -- status / stop -------------------------------------------------------

    def status(self, window: Any, rid: Optional[str] = None, *, seq: Any = None,
               store_id: Any = None) -> dict:
        """Return warm or cold progress and mark stale snapshots (D-14, D-26)."""
        key = window_key(window)
        newest = not rid or rid == self.latest.get(key)
        if newest and key in self.intent_answers and self.intent_at.get(key, 0) > self.latest_at.get(key, -1):
            # Review S P2-5 / int r1 P2-2: renew / new-instance progress after a click is the newest answer, so it
            # shows; the page keeps polling with its last click id, which counts as asking for the newest answer.
            return copy.deepcopy(self.intent_answers[key])
        warm = self.warm_jobs.get(rid or self.latest.get(key))
        if warm:
            warm["seen"] = time.monotonic()
            out = copy.deepcopy(warm["answer"])
            prov = ((out.get("result") or {}).get("provenance") or {})
            if prov and seq is not None and (prov.get("seq") != seq or
                                             (store_id is not None and prov.get("store_id") != store_id)):
                prov.update(stale=True, stale_line=STALE_LINE)
                out["banners"].append(STALE_LINE)
            return out
        if not rid and key not in self.latest and key in self.intent_answers:
            return copy.deepcopy(self.intent_answers[key])
        runs = self.runs(window)
        rid = rid or self.latest.get(runs.window)
        if not rid:
            return answer("none")
        final = self.final.get(rid) or self.finalize(rid)
        if final is None:
            run = runs.current
            if run is None or run["id"] != rid:
                saved = _read_json(runs._runs_dir() / str(rid) / "_answer.json") \
                    if re.fullmatch(r"[0-9A-Za-z-]+", str(rid)) else None
                return copy.deepcopy(saved) if isinstance(saved, dict) else answer("none")
            st = runs.status(rid)  # touches the heartbeat
            job = self.jobs.get(rid) or {}
            raw = _read_status(run["work"] / "_status.json")
            if raw is None:  # a bad read keeps the last good one; it never ends the run
                raw = job.get("last_status") or {}
            else:
                job["last_status"] = raw
            phases = [job.get("copy_phase") or {"name": "copy", "s": 0.0}] + _clean_phases(raw.get("phases"))
            phase = "stopping" if st.get("stopping") else (raw.get("phase") or "build")
            state = "starting" if phase == "spawn" else "running"
            return answer(state, id=rid, phase=phase, phases=phases, elapsed_s=st.get("elapsed_s"),
                          limit_s=self.limit_s, estimate_s=job.get("estimate_s"), secret=job.get("secret"))
        out = copy.deepcopy(final)
        prov = ((out.get("result") or {}).get("provenance") or {})
        if prov and seq is not None and (prov.get("seq") != seq or
                                         (store_id is not None and prov.get("store_id") != store_id)):
            prov["stale"], prov["stale_line"] = True, STALE_LINE
            if STALE_LINE not in out.setdefault("banners", []):
                out["banners"].append(STALE_LINE)
        return out

    def stop(self, window: Any, rid: Optional[str] = None) -> dict:
        """Signal the active transport without moving lock ownership (D-18)."""
        warm = self.warm_jobs.get(rid or self.latest.get(window_key(window)))
        if warm:
            warm["stop"].set()
            return copy.deepcopy(warm["answer"])
        runs = self.runs(window)
        rid = rid or self.latest.get(runs.window)
        if rid and runs.stop(rid, "Stop pressed"):
            return self.status(window, rid)
        return self.status(window, rid)

    # -- script ----------------------------------------------------------------

    def run_folder(self, rid: Any) -> Optional[Path]:
        if not isinstance(rid, str) or not re.fullmatch(r"[0-9A-Za-z-]{1,64}", rid):
            return None
        root = self.instance / "nodesim"
        if not root.is_dir():
            return None
        for win in root.iterdir():
            cand = win / rid
            if (cand / "_run.json").is_file():
                return cand
        return None

    def script(self, rid: Any) -> Optional[dict]:
        work = self.run_folder(rid)
        if work is None:
            return None
        result = _read_json(work / "_result.json") or {}
        meta = (_read_json(work / "_run.json") or {}).get("meta") or {}
        return {"script": result.get("script") if isinstance(result.get("script"), str) else None,
                "script_ok": bool(result.get("script_ok")), "node": meta.get("node"),
                "overrides": meta.get("overrides"), "window_us": meta.get("window_us")}

    def save_script(self, rid: Any, folder: Any, *, forbidden: tuple = ()) -> dict:
        """Write ``<node stem>_simulated.py`` into the folder the person picked (D-24)."""
        got = self.script(rid)
        if not got or not got["script"]:
            return {"ok": False, "error": "This run has no script to save."}
        if not isinstance(folder, str) or not folder.strip() or not Path(folder).is_absolute():
            return {"ok": False, "error": "Pick a folder to save the script in."}
        target_dir = Path(folder).resolve()
        if not target_dir.is_dir():
            return {"ok": False, "error": "That folder does not exist."}
        for bad in [self.instance, *forbidden]:
            if not bad:
                continue
            b = Path(bad).resolve()
            if target_dir == b or target_dir.is_relative_to(b):
                return {"ok": False, "error": "Pick a folder outside the calibration library, the chip "
                                              "folder and SM's own folder."}
        stem = Path(str(got["node"] or "node")).stem
        path = target_dir / f"{stem}_simulated.py"
        if path.exists():
            return {"ok": False, "error": f"{path.name} is already in that folder; pick another folder."}
        header = ("# Simulated QUA script written by SM (report only, not run on hardware).\n"
                  f"# Window: {_g(got['window_us'])} us\n"
                  f"# Overrides: {json.dumps(got['overrides'], sort_keys=True)}\n")
        if not got["script_ok"]:
            header += "# script incomplete: the serializer did not finish this script.\n"
        path.write_text(header + got["script"], encoding="utf-8")
        return {"ok": True, "path": str(path)}


def _sha8(path: Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:8]
    except OSError:
        return "unknown"


def _has_mw(chip_dir: Path) -> bool:
    state = _read_json(Path(chip_dir) / "state.json") or {}
    ports = state.get("ports") if isinstance(state, dict) else None
    return bool(isinstance(ports, dict) and ports.get("mw_outputs"))


def _owned_popen(argv, **kw):
    """Start the child as an owned process tree with exactly the pinned env
    (no lab path prepended: PYTHONPATH must be the library root alone)."""
    from quam_state_manager.core import lab_worker
    return lab_worker._spawn(argv, prepend_lab=False, **kw)
