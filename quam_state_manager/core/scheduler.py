"""Experiment Scheduler — config, queue, and the background runner.

The Scheduler queues qualibrate experiment ``.py`` files (single calibration
*nodes* and user-authored *graph* files) and runs them sequentially on a chosen
chip with chosen parameters. See ``docs/40_scheduler.md`` for the full design.

This module owns several layers (kept in one file deliberately — the worker's
test seams are monkeypatched by name, so a split would churn the test suite for
a single-user local app; revisit if it keeps growing):

* **Config read** — :func:`read_effective_config` / :func:`scan_params` shell the
  chosen interpreter (``generator/run_experiment.py``) to learn the env's
  *effective* qualibrate config + per-node parameter schemas. SM never imports
  the QM/qualibrate stack itself.
* **Settings + dataset discovery** — ``instance/scheduler.json`` + the dataset
  roots under the storage location.
* **Pre-flight** — :func:`build_preflight`, the identity/safety checks gating a run.
* **Queue** — durable ``instance/scheduler_queue.json`` CRUD (add/reorder/…).
* **Runner** — the background daemon worker: spawn/kill (process groups), the
  dry-run + graph-library safety gates, failure policy, heartbeat, cancellation.
"""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from quam_state_manager.core import config_generator, history, node_inject, node_scan, safe_io

logger = logging.getLogger(__name__)


# The standalone runner, executed by the user-selected interpreter. Resolved the
# same way the generator scripts are (dev checkout vs frozen bundle), so it ships
# in the PyInstaller bundle automatically (the whole generator/ dir is data).
EXPERIMENT_SCRIPT = config_generator._script_path("run_experiment.py")


# ----------------------------------------------------------------------
# Subprocess: report-config
# ----------------------------------------------------------------------

def _run_experiment_script(python_path: str, mode: str, *, timeout: int,
                           extra_args: list[str] | None = None) -> dict:
    """Spawn ``run_experiment.py --mode <mode>`` and return its parsed result.

    Mirrors the config_generator runner contract: a private temp work dir holds
    ``_result.json``; spawning goes through ``config_generator._run_command``
    (a module global, so Scheduler tests can monkeypatch it without real
    processes). Never raises — every failure is reported in the returned dict.
    """
    blank = {
        "ok": False, "status": "error", "error": None,
        "returncode": None, "stdout": "", "stderr": "",
    }
    if not python_path:
        blank["error"] = "no interpreter selected"
        return blank
    if not EXPERIMENT_SCRIPT.exists():
        blank["error"] = f"runner script not found: {EXPERIMENT_SCRIPT}"
        return blank

    work_dir = Path(tempfile.mkdtemp(prefix="quamsched_"))
    try:
        argv = [
            python_path, str(EXPERIMENT_SCRIPT),
            "--mode", mode,
            *(extra_args or []),
            "--out", str(work_dir),
        ]
        returncode, stdout, stderr = config_generator._run_command(argv, timeout=timeout)
        result_file = work_dir / "_result.json"
        if not result_file.exists():
            blank["returncode"] = returncode
            blank["stdout"] = stdout
            blank["stderr"] = stderr
            blank["error"] = (
                f"runner produced no _result.json (rc={returncode}) — the "
                f"interpreter may have failed to start. stderr: "
                f"{(stderr or '').strip()[:300]}"
            )
            return blank
        try:
            parsed = json.loads(result_file.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            blank["error"] = f"could not read _result.json: {exc}"
            return blank
        parsed["returncode"] = returncode
        parsed["stdout"] = stdout
        parsed["stderr"] = stderr
        parsed["ok"] = parsed.get("status") == "ok"
        return parsed
    finally:
        config_generator._cleanup_work_dir(work_dir)


def read_effective_config(python_path: str, *, timeout: int = 60) -> dict:
    """Read the chosen env's effective qualibrate config + editable-install root.

    Returns the parsed ``run_experiment.py`` envelope: ``{ok, status, config:
    {config_file, project, state_path, storage_location,
    calibration_library_folder, source}, editable_install: {dist, path,
    editable}, versions, error, ...}``.
    """
    return _run_experiment_script(python_path, "report-config", timeout=timeout)


_SCAN_CACHE_FILENAME = "scheduler_scan_cache.json"


def _folder_fingerprint(folder) -> str:
    """Cheap content fingerprint of a folder's ``.py`` files (count + max mtime)."""
    try:
        files = list(Path(folder).glob("*.py"))
        mt = max((f.stat().st_mtime for f in files), default=0.0)
        return f"{len(files)}:{mt}"
    except OSError:
        return ""


def scan_params(python_path: str, folder: str, *, instance_path=None,
                timeout: int = 120, use_cache: bool = True) -> dict:
    """Inspection-based scan (subprocess): full parameter JSON-schemas per node/graph.

    Hardware-safe (qualibrate inspection mode stops at the constructor). Slow —
    it imports every file — so the result is cached under
    ``<instance>/scheduler_scan_cache.json`` keyed on (folder, interpreter mtime,
    folder fingerprint); a 2nd..Nth load with no changes is an instant disk read.
    """
    cache_path = Path(instance_path) / _SCAN_CACHE_FILENAME if instance_path else None
    key = "|".join([
        norm_path(folder) or "",
        python_path or "",
        str(config_generator._python_mtime(python_path)),
        _folder_fingerprint(folder),
    ])
    if use_cache and cache_path is not None and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(cached, dict) and cached.get("key") == key and cached.get("result"):
                result = dict(cached["result"])
                result["cached"] = True
                return result
        except (OSError, ValueError):
            pass
    result = _run_experiment_script(python_path, "scan", timeout=timeout,
                                    extra_args=["--folder", folder])
    if use_cache and cache_path is not None and result.get("ok"):
        try:
            safe_io.atomic_write_json(cache_path, {"key": key, "result": result})
        except OSError:
            logger.warning("Could not persist scan cache", exc_info=True)
    return result


# ----------------------------------------------------------------------
# Settings persistence (instance/scheduler.json)
# ----------------------------------------------------------------------

_SETTINGS_FILENAME = "scheduler.json"

_DEFAULTS: dict = {
    "calibrations_folder": "",
    "env_python": "",
    "quam_state_path": "",
    "failure_policy": "stop",       # "stop" | "continue"
    "global_simulate": True,        # default to a dry run for safety
    "default_timeout_s": 1800,
    "continue_without_ui": False,   # if False, pause the queue when the UI disconnects
    "effective_config": None,       # last-read snapshot (shown in Verify + read on the run path)
}


def settings_path(instance_path) -> Path:
    return Path(instance_path) / _SETTINGS_FILENAME


def load_settings(instance_path) -> dict:
    """Read persisted settings, merged over defaults; tolerant of a missing file."""
    path = settings_path(instance_path)
    data: dict = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, ValueError):
            logger.warning("Could not read scheduler settings %s", path, exc_info=True)
    merged = dict(_DEFAULTS)
    merged.update({k: v for k, v in data.items() if k in _DEFAULTS})
    return merged


def save_settings(instance_path, settings: dict) -> dict:
    """Persist settings atomically (only known keys); returns the merged result.

    Guarded by _QLOCK so a debounced settings POST can't lose-update against the
    effective-config write (both do a full read-modify-write of scheduler.json).
    A non-positive/invalid ``default_timeout_s`` is clamped to the default so the
    run watchdog can never be silently disabled.
    """
    with _QLOCK:
        current = load_settings(instance_path)
        current.update({k: v for k, v in (settings or {}).items() if k in _DEFAULTS})
        try:
            t = int(current.get("default_timeout_s"))
        except (TypeError, ValueError):
            t = _DEFAULTS["default_timeout_s"]
        current["default_timeout_s"] = t if t > 0 else _DEFAULTS["default_timeout_s"]
        safe_io.atomic_write_json(settings_path(instance_path), current)
        return current


# ----------------------------------------------------------------------
# Dataset-root discovery under the qualibrate storage location
# ----------------------------------------------------------------------

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _has_date_child(folder: Path) -> bool:
    try:
        for child in folder.iterdir():
            if child.is_dir() and _DATE_RE.match(child.name):
                return True
    except OSError:
        return False
    return False


def find_dataset_roots(storage_location) -> list[str]:
    """Folders directly containing ``YYYY-MM-DD`` run dirs under *storage_location*.

    qualibrate writes runs to ``storage.location/<project-subfolder>/<date>/#N…``,
    so the DatasetStore root SM must index is usually one level below the
    configured storage location. We find the real date-containing folders by
    scanning (the storage location itself, then one level down) — robust when
    runs already exist. Returns ``[]`` for an empty/fresh storage location.
    """
    if not storage_location:
        return []
    root = Path(storage_location)
    if not root.is_dir():
        return []
    found: list[str] = []
    if _has_date_child(root):
        found.append(str(root))
    try:
        children = sorted(c for c in root.iterdir() if c.is_dir())
    except OSError:
        children = []
    for sub in children:
        if _has_date_child(sub):
            found.append(str(sub))
    # de-dup, preserve order
    seen: set[str] = set()
    out: list[str] = []
    for f in found:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


# ----------------------------------------------------------------------
# Path helpers
# ----------------------------------------------------------------------

def norm_path(p) -> str | None:
    """Canonical comparison form for a folder path (resolve + casefold).

    Matches ``working_copy.key_for`` normalization — Windows is case-folding, so
    we lower-case before comparing. Returns ``None`` for a falsy path.
    """
    if not p:
        return None
    try:
        return str(Path(p).resolve()).lower()
    except OSError:
        return str(p).lower()


def paths_equal(a, b) -> bool:
    na, nb = norm_path(a), norm_path(b)
    return na is not None and na == nb


def folder_under_install(calibrations_folder, install_path) -> bool | None:
    """Is *calibrations_folder* inside the editable-install *install_path*?

    Returns ``None`` when the install path is unknown (can't decide).
    """
    if not install_path:
        return None
    if not calibrations_folder:
        return False
    try:
        cal = Path(calibrations_folder).resolve()
        inst = Path(install_path).resolve()
    except OSError:
        return False
    if paths_equal(cal, inst):
        return True
    # casefolded ancestor check (Windows)
    inst_s = str(inst).lower()
    cal_s = str(cal).lower()
    return cal_s == inst_s or cal_s.startswith(inst_s.rstrip("\\/") + ("\\" if "\\" in inst_s else "/"))


def align_folders(open_chip_folder, target_folder) -> str:
    """``history.align`` outcome for two chip folders (reads state/wiring)."""
    loaded = history.fingerprint_of(open_chip_folder) if open_chip_folder else None
    candidate = history.fingerprint_of(target_folder) if target_folder else None
    return history.align(loaded, candidate)


def storage_registered(dataset_roots, workspace_roots) -> bool:
    """Will SM index runs that land in *dataset_roots*?

    True if every found dataset root is at/under some registered workspace root.
    Empty *dataset_roots* (fresh storage, no runs yet) → False (nothing to
    confirm; the precise root is auto-registered after the first run in Phase 1).
    """
    if not dataset_roots:
        return False
    norm_ws = [n for n in (norm_path(r) for r in (workspace_roots or [])) if n]
    for ds in dataset_roots:
        nds = norm_path(ds)
        if nds is None:
            return False
        covered = any(
            nds == w or nds.startswith(w.rstrip("\\/") + ("\\" if "\\" in w else "/"))
            for w in norm_ws
        )
        if not covered:
            return False
    return True


# ----------------------------------------------------------------------
# Pre-flight
# ----------------------------------------------------------------------

def _check(key, label, status, detail=""):
    return {"key": key, "label": label, "status": status, "detail": detail}


def build_preflight(ctx: dict) -> dict:
    """Assemble the pre-flight check list from gathered facts (pure).

    *ctx* keys (the route gathers them, since several need Flask state or file
    reads): ``chip_open`` (bool), ``chip_type`` (str|None), ``open_chip_folder``,
    ``target_quam_state``, ``calibrations_folder``, ``effective_config`` (dict),
    ``editable_install_path``, ``align_result`` (history.align outcome),
    ``env_usable`` (bool|None), ``env_missing`` (list), ``chip_clean`` (bool),
    ``dataset_roots`` (list), ``workspace_roots`` (list).

    Returns ``{"ok": bool, "checks": [{key,label,status,detail}, ...]}`` where
    ``ok`` is True iff no check is ``"fail"``. Status ∈ pass|fail|warn|skip.
    """
    checks: list[dict] = []
    cfg = ctx.get("effective_config") or {}
    open_folder = ctx.get("open_chip_folder")
    target = ctx.get("target_quam_state")
    cal = ctx.get("calibrations_folder")

    # 1. A QUAM chip is open
    if ctx.get("chip_open") and ctx.get("chip_type") == "quam":
        checks.append(_check("chip_open", "A QUAM chip is open", "pass",
                             open_folder or ""))
    else:
        checks.append(_check("chip_open", "A QUAM chip is open", "fail",
                             "Load a quam_state chip in the State Manager first."))

    # 2. quam_state path matches the open chip (Strict)
    if not target:
        checks.append(_check("path_match", "quam_state matches the open chip", "fail",
                             "No quam_state path set."))
    elif not open_folder:
        checks.append(_check("path_match", "quam_state matches the open chip", "fail",
                             "No chip open to compare against."))
    elif paths_equal(target, open_folder):
        checks.append(_check("path_match", "quam_state matches the open chip", "pass",
                             target))
    else:
        checks.append(_check("path_match", "quam_state matches the open chip", "fail",
                             f"Scheduler target {target} != open chip {open_folder}."))

    # 3. Chip identity (fingerprint) match
    align = ctx.get("align_result")
    if align == history.ALIGN_ALIGNED:
        checks.append(_check("identity", "Chip identity matches (network + labels)", "pass", ""))
    elif align == history.ALIGN_RENAMED:
        checks.append(_check("identity", "Chip identity matches (network + labels)", "warn",
                             "Same hardware (host/cluster) but qubit/pair labels differ."))
    elif align == history.ALIGN_DIFFERENT_CHIP:
        checks.append(_check("identity", "Chip identity matches (network + labels)", "fail",
                             "Different hardware (host/cluster_name) — this is not the same chip."))
    else:
        checks.append(_check("identity", "Chip identity matches (network + labels)", "warn",
                             "Could not fingerprint one side (missing/unreadable state or wiring)."))

    # 4. env config state_path == open chip (Strict)
    cfg_state = cfg.get("state_path")
    if not cfg_state:
        checks.append(_check("config_state", "Env config state_path matches the open chip", "warn",
                             "Could not read the env's qualibrate state_path."))
    elif open_folder and paths_equal(cfg_state, open_folder):
        checks.append(_check("config_state", "Env config state_path matches the open chip", "pass",
                             cfg_state))
    else:
        checks.append(_check("config_state", "Env config state_path matches the open chip", "fail",
                             f"Env will load {cfg_state}, but the open chip is {open_folder}. "
                             f"Strict policy requires they match."))

    # 5. calibrations folder is inside the env's editable install
    under = folder_under_install(cal, ctx.get("editable_install_path"))
    if under is True:
        checks.append(_check("folder_install", "Calibrations folder matches the env's editable install",
                             "pass", ctx.get("editable_install_path") or ""))
    elif under is False:
        checks.append(_check("folder_install", "Calibrations folder matches the env's editable install",
                             "fail",
                             f"The env's editable install is {ctx.get('editable_install_path')}, "
                             f"not a parent of {cal}. Imports (quam_config/calibration_utils) "
                             f"would resolve to a different (possibly stale) tree."))
    else:
        checks.append(_check("folder_install", "Calibrations folder matches the env's editable install",
                             "warn",
                             "Env has no editable 'superconducting_calibrations' install to check against."))

    # 5b. graph member-node library folder (only bites graph items, so warn; the
    #     run-gate hard-fails a graph whose library folder doesn't match).
    lib_folder = cfg.get("calibration_library_folder")
    if not lib_folder:
        checks.append(_check("graph_library", "Graph member-node library matches the folder",
                             "warn", "Could not read the env's calibration_library.folder."))
    elif cal and paths_equal(lib_folder, cal):
        checks.append(_check("graph_library", "Graph member-node library matches the folder",
                             "pass", lib_folder))
    else:
        checks.append(_check("graph_library", "Graph member-node library matches the folder",
                             "warn",
                             f"Graphs resolve member nodes from {lib_folder}, not your folder "
                             f"{cal} — graph items will be refused at run until this matches."))

    # 6. env QM-stack usable
    if ctx.get("env_usable") is True:
        checks.append(_check("env_usable", "Env has the QM stack (qualang_tools/quam_builder/quam)",
                             "pass", ""))
    elif ctx.get("env_usable") is False:
        missing = ", ".join(ctx.get("env_missing") or []) or "unknown"
        checks.append(_check("env_usable", "Env has the QM stack (qualang_tools/quam_builder/quam)",
                             "fail", f"Missing: {missing}."))
    else:
        checks.append(_check("env_usable", "Env has the QM stack (qualang_tools/quam_builder/quam)",
                             "warn", "Env not probed yet."))

    # 7. open chip is clean (no unsaved/unapplied edits)
    if ctx.get("chip_clean"):
        checks.append(_check("chip_clean", "Open chip has no unsaved edits", "pass", ""))
    else:
        checks.append(_check("chip_clean", "Open chip has no unsaved edits", "fail",
                             "Apply or discard your working-copy edits before running — "
                             "experiment writes would collide with them."))

    # 8. storage registered as an SM dataset root
    dataset_roots = ctx.get("dataset_roots") or []
    if storage_registered(dataset_roots, ctx.get("workspace_roots") or []):
        checks.append(_check("storage", "Results folder is indexed by Datasets", "pass",
                             "; ".join(dataset_roots)))
    elif dataset_roots:
        checks.append(_check("storage", "Results folder is indexed by Datasets", "warn",
                             "Run results won't appear in Datasets until you register: "
                             + "; ".join(dataset_roots)))
    else:
        loc = cfg.get("storage_location")
        checks.append(_check("storage", "Results folder is indexed by Datasets", "warn",
                             f"No runs found under {loc} yet; the dataset root is auto-registered "
                             f"after the first run."))

    ok = all(c["status"] != "fail" for c in checks)
    return {"ok": ok, "checks": checks}


# ======================================================================
# Queue + background worker (Phase 1)
#
# All durable state lives in instance/scheduler_queue.json; per-run stdout in
# instance/scheduler_logs/<id>.log. The worker is a Flask-free daemon thread
# keyed on instance_path — it reads settings + queue from disk, spawns
# run_experiment.py (run mode) one item at a time, and writes status back. No
# Flask app context is required (mirrors the param-history backfill pattern but
# self-contained on disk). See docs/40_scheduler.md.
# ======================================================================

_QUEUE_FILENAME = "scheduler_queue.json"
_LOGS_DIRNAME = "scheduler_logs"

# Process-wide guard for queue-file read-modify-write + the runner registry.
_QLOCK = threading.RLock()
# instance_path -> {"thread", "cancel": Event, "proc": Popen|None, "proc_lock": Lock}
_RUNNERS: dict[str, dict] = {}

# In-memory UI heartbeat: the /scheduler/status poll proves the browser is alive.
# If it goes stale the worker pauses after the current item (unless tmux mode).
# 90s (not 30s): browsers clamp a *backgrounded* tab's timers to ~1 fire/60s, so a
# 30s window would falsely trip for a merely-hidden (not closed) tab. 90s survives
# the background clamp while still pausing within ~1.5 min of an actually-closed tab.
_LAST_UI_SEEN: dict[str, float] = {}
HEARTBEAT_TIMEOUT_S = 90.0


def touch_ui(instance_path) -> None:
    """Record that the UI just polled (browser-alive heartbeat)."""
    _LAST_UI_SEEN[str(instance_path)] = time.time()


# Post-node refresh hook (injected by the web layer). The Flask-free worker can't
# reconcile the chip / rescan datasets itself, so the web layer registers a hook
# that does so under an app context. Signature: fn(quam_state_path, item_id, status)
# — status is the just-finished item's terminal status ('done'/'failed'/…).
_refresh_hook = None


def set_refresh_hook(fn) -> None:
    global _refresh_hook
    _refresh_hook = fn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def queue_path(instance_path) -> Path:
    return Path(instance_path) / _QUEUE_FILENAME


def _logs_dir(instance_path) -> Path:
    d = Path(instance_path) / _LOGS_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _blank_state() -> dict:
    return {"queue": [], "run": {"status": "idle", "current_id": None,
                                 "started_at": None, "message": ""}}


def load_queue(instance_path) -> dict:
    """Read the queue state; tolerant of a missing/corrupt file."""
    path = queue_path(instance_path)
    if not path.exists():
        return _blank_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("Could not read scheduler queue %s", path, exc_info=True)
        return _blank_state()
    if not isinstance(data, dict):
        return _blank_state()
    data.setdefault("queue", [])
    data.setdefault("run", _blank_state()["run"])
    return data


def save_queue(instance_path, state: dict) -> None:
    safe_io.atomic_write_json(queue_path(instance_path), state)


def _find(state: dict, item_id: str) -> dict | None:
    for it in state["queue"]:
        if it.get("id") == item_id:
            return it
    return None


def _renumber(state: dict) -> None:
    for i, it in enumerate(sorted(state["queue"], key=lambda x: x.get("order", 0))):
        it["order"] = i
    state["queue"].sort(key=lambda x: x.get("order", 0))


# ----------------------------------------------------------------------
# Queue mutations
# ----------------------------------------------------------------------

def _new_item(info: dict, targets: list | None) -> dict:
    return {
        "id": uuid.uuid4().hex[:8],
        "source_file": info.get("file") or info.get("source_file"),
        "name": info.get("name"),
        "kind": info.get("kind", "node"),
        "has_hook": bool(info.get("has_hook")),
        "targets_name": info.get("targets_name") or "qubits",
        "targets": list(targets or []),
        "param_overrides": node_inject.strip_reserved_overrides(
            info.get("param_overrides"), info.get("targets_name")),
        "enabled": True,
        "order": 0,
        "status": "queued",
        "started_at": None,
        "ended_at": None,
        "returncode": None,
        "error": None,
        "log_file": None,
        "result_ref": None,
        # --- sequence-editor fields ---
        "label": str(info.get("label") or ""),   # user-named step, e.g. "retune qA1"
        "on_outcome": [dict(r) for r in (info.get("on_outcome") or [])],
        "inserted_by": None,       # {"rule": idx, "parent_item": id, "depth": int} on auto-inserts
        "outcome_note": None,      # why an outcome rule no-op'd (attribution miss etc.)
    }


def add_item(instance_path, info: dict, targets: list | None = None,
             *, after_id: str | None = None) -> dict:
    """Add a queue item built from a NodeInfo-like dict. Returns the item.

    ``after_id`` inserts the new entry directly after that item (sequence-editor
    insert-at-position); default appends. An unknown ``after_id`` falls back to
    append rather than erroring — the anchor may have been removed concurrently.
    """
    with _QLOCK:
        state = load_queue(instance_path)
        item = _new_item(info, targets)
        anchor = _find(state, after_id) if after_id else None
        if anchor is not None:
            item["order"] = anchor.get("order", 0) + 0.5
            state["queue"].append(item)
            _renumber(state)
        else:
            item["order"] = len(state["queue"])
            state["queue"].append(item)
        save_queue(instance_path, state)
        return item


def remove_item(instance_path, item_id: str) -> None:
    with _QLOCK:
        state = load_queue(instance_path)
        state["queue"] = [it for it in state["queue"] if it.get("id") != item_id]
        _renumber(state)
        save_queue(instance_path, state)


def toggle_item(instance_path, item_id: str, enabled: bool | None = None) -> None:
    with _QLOCK:
        state = load_queue(instance_path)
        it = _find(state, item_id)
        if it is not None:
            it["enabled"] = (not it.get("enabled", True)) if enabled is None else bool(enabled)
            save_queue(instance_path, state)


def set_targets(instance_path, item_id: str, targets: list) -> None:
    with _QLOCK:
        state = load_queue(instance_path)
        it = _find(state, item_id)
        if it is not None:
            it["targets"] = list(targets or [])
            save_queue(instance_path, state)


def set_item_result(instance_path, item_id: str, result_ref: dict) -> None:
    """Attach the finished run's dataset reference (uid/run_id/name) to an item.

    Also records ``run.last_assigned_run_id`` so the hook never re-attributes the
    same run to a later (no-output) item.
    """
    with _QLOCK:
        state = load_queue(instance_path)
        it = _find(state, item_id)
        if it is not None:
            it["result_ref"] = result_ref
            rid = result_ref.get("run_id") if isinstance(result_ref, dict) else None
            if isinstance(rid, int):
                state["run"]["last_assigned_run_id"] = rid
            save_queue(instance_path, state)


def bump_chip_rev(instance_path) -> None:
    """Increment the run's chip-revision counter (the UI re-renders when it advances)."""
    with _QLOCK:
        state = load_queue(instance_path)
        state["run"]["chip_rev"] = state["run"].get("chip_rev", 0) + 1
        save_queue(instance_path, state)


def set_param_overrides(instance_path, item_id: str, overrides: dict) -> None:
    with _QLOCK:
        state = load_queue(instance_path)
        it = _find(state, item_id)
        if it is not None:
            # Never persist reserved keys (simulate / targets) — they are owned by
            # the Dry-run toggle + the targets row, not by param overrides.
            it["param_overrides"] = node_inject.strip_reserved_overrides(
                overrides, it.get("targets_name"))
            save_queue(instance_path, state)


def reorder(instance_path, ordered_ids: list[str]) -> None:
    """Reorder by id. A partial/duplicated list is total-ordered safely: listed
    ids take 0..k-1, unlisted items keep their prior relative order after them."""
    with _QLOCK:
        state = load_queue(instance_path)
        seen: set[str] = set()
        listed = [i for i in (ordered_ids or []) if not (i in seen or seen.add(i))]
        rank = {iid: i for i, iid in enumerate(listed)}
        unlisted = sorted(
            (it for it in state["queue"] if it.get("id") not in rank),
            key=lambda x: x.get("order", 0),
        )
        for offset, it in enumerate(unlisted):
            it["order"] = len(listed) + offset
        for it in state["queue"]:
            if it.get("id") in rank:
                it["order"] = rank[it["id"]]
        _renumber(state)
        save_queue(instance_path, state)


def duplicate_item(instance_path, item_id: str) -> dict | None:
    """Duplicate an item, inserting the copy directly AFTER the original
    (a copied step almost always belongs next to its source, matching
    ``expand_per_qubit``'s in-place behaviour)."""
    with _QLOCK:
        state = load_queue(instance_path)
        it = _find(state, item_id)
        if it is None:
            return None
        dup = _new_item(it, it.get("targets"))
        dup["order"] = it.get("order", len(state["queue"])) + 0.5
        state["queue"].append(dup)
        _renumber(state)
        save_queue(instance_path, state)
        return dup


def set_item_label(instance_path, item_id: str, label: str) -> None:
    with _QLOCK:
        state = load_queue(instance_path)
        it = _find(state, item_id)
        if it is not None:
            it["label"] = str(label or "")[:120]
            save_queue(instance_path, state)


# ----------------------------------------------------------------------
# Outcome rules (sequence-editor chaining)
# ----------------------------------------------------------------------
# Rule shape (persisted on the item as ``on_outcome: [rule, ...]``):
#   {"when": "fit_fail" | "item_failed",
#    "insert": [{"source_file": str, "name": str, "kind": "node",
#                "has_hook": bool, "targets_name": str,
#                "param_overrides": dict}, ...],
#    "targets_mode": "failed_only" | "inherit"}
# v1 keeps exactly two conditions: ``fit_fail`` (any effective target with
# fit_results[q]["success"] == False in the attributed run's data.json) and
# ``item_failed`` (the queue item itself ended failed). Auto-inserted children
# NEVER inherit rules (loop guard) and carry ``inserted_by`` provenance.

_MAX_AUTOINSERT_DEPTH = 2

_ALLOWED_RULE_WHEN = ("fit_fail", "item_failed")
_ALLOWED_TARGETS_MODE = ("failed_only", "inherit")


def set_item_rules(instance_path, item_id: str, rules: list) -> str | None:
    """Validate + persist an item's outcome rules. Returns an error string or None."""
    cleaned = []
    for r in rules or []:
        if not isinstance(r, dict):
            return "rule entries must be objects"
        when = r.get("when")
        if when not in _ALLOWED_RULE_WHEN:
            return f"unknown rule condition: {when!r}"
        mode = r.get("targets_mode", "failed_only")
        if mode not in _ALLOWED_TARGETS_MODE:
            return f"unknown targets_mode: {mode!r}"
        inserts = r.get("insert") or []
        if not isinstance(inserts, list) or not inserts:
            return "rule needs at least one node to insert"
        keep = []
        for ins in inserts:
            if not isinstance(ins, dict) or not ins.get("source_file") or not ins.get("name"):
                return "each insert needs source_file + name"
            keep.append({
                "source_file": str(ins["source_file"]),
                "name": str(ins["name"]),
                "kind": ins.get("kind", "node"),
                "has_hook": bool(ins.get("has_hook")),
                "targets_name": ins.get("targets_name") or "qubits",
                "param_overrides": node_inject.strip_reserved_overrides(
                    ins.get("param_overrides"), ins.get("targets_name")),
            })
        cleaned.append({"when": when, "targets_mode": mode, "insert": keep})
    with _QLOCK:
        state = load_queue(instance_path)
        it = _find(state, item_id)
        if it is None:
            return "item not found"
        it["on_outcome"] = cleaned
        save_queue(instance_path, state)
    return None


def plan_outcome_inserts(item: dict, status: str, fit_results: dict | None) -> tuple[list[dict], str | None]:
    """PURE rule evaluation for one finished item.

    ``fit_results`` is the attributed run's per-qubit dict (or None when no run
    could be attributed — rules that need it then no-op with a note; NEVER
    guess). Returns ``(planned_items, note)`` where each planned item is an
    ``add_item``-ready info dict with targets + ``inserted_by`` filled in.
    """
    rules = item.get("on_outcome") or []
    if not rules:
        return [], None
    depth = ((item.get("inserted_by") or {}).get("depth") or 0)
    if depth >= _MAX_AUTOINSERT_DEPTH:
        return [], f"auto-insert depth cap ({_MAX_AUTOINSERT_DEPTH}) reached"
    planned: list[dict] = []
    note = None
    for idx, rule in enumerate(rules):
        when = rule.get("when")
        if when == "item_failed":
            if status != "failed":
                continue
            targets = list(item.get("targets") or [])
        elif when == "fit_fail":
            if status != "done":
                continue
            if not isinstance(fit_results, dict):
                note = "fit_fail rule skipped: no run attributed (or data.json unreadable)"
                continue
            failed = [q for q, v in fit_results.items()
                      if isinstance(v, dict) and v.get("success") is False]
            eff = item.get("targets") or []
            if eff:
                failed = [q for q in failed if q in eff]
            if not failed:
                continue
            targets = failed if rule.get("targets_mode", "failed_only") == "failed_only" \
                else list(item.get("targets") or [])
        else:
            continue
        for ins in rule.get("insert") or []:
            info = dict(ins)
            info["label"] = f"auto: after {item.get('name')}"
            info["_targets"] = targets
            info["_inserted_by"] = {
                "rule": idx, "parent_item": item.get("id"), "depth": depth + 1,
            }
            planned.append(info)
    return planned, note


def apply_outcome_inserts(instance_path, item_id: str, planned: list[dict],
                          note: str | None) -> int:
    """Insert planned follow-ups directly after the finished item (in order).

    Runs under ``_QLOCK`` before the worker's next ``_next_queued`` read, so the
    inserts are picked up seamlessly mid-run. Also records ``outcome_note``.
    """
    made = 0
    with _QLOCK:
        state = load_queue(instance_path)
        it = _find(state, item_id)
        if it is None:
            return 0
        if note:
            it["outcome_note"] = note
        base = it.get("order", 0)
        for k, info in enumerate(planned):
            child = _new_item(info, info.get("_targets"))
            child["on_outcome"] = []                    # children never inherit rules
            child["inserted_by"] = info.get("_inserted_by")
            child["order"] = base + (k + 1) / (len(planned) + 1.0)
            state["queue"].append(child)
            made += 1
        if made:
            _renumber(state)
        save_queue(instance_path, state)
    return made


def expand_per_qubit(instance_path, item_id: str, targets: list[str]) -> int:
    """Explode one item into one copy per target (each a single-element targets)."""
    with _QLOCK:
        state = load_queue(instance_path)
        it = _find(state, item_id)
        if it is None or not targets:
            return 0
        base_order = it.get("order", len(state["queue"]))
        made = 0
        for t in targets:
            dup = _new_item(it, [t])
            dup["order"] = base_order
            state["queue"].append(dup)
            made += 1
        # drop the original; renumber so the explosion sits where it was
        state["queue"] = [x for x in state["queue"] if x.get("id") != item_id]
        _renumber(state)
        save_queue(instance_path, state)
        return made


def clear_finished(instance_path) -> None:
    with _QLOCK:
        state = load_queue(instance_path)
        keep = {"queued", "running"}
        state["queue"] = [it for it in state["queue"] if it.get("status") in keep]
        _renumber(state)
        save_queue(instance_path, state)


# ----------------------------------------------------------------------
# Sequence presets — reusable named sequences (instance/scheduler_presets.json)
# ----------------------------------------------------------------------

_PRESETS_FILENAME = "scheduler_presets.json"

# Runtime fields stripped when snapshotting the queue into a preset; everything
# regenerated on load (fresh ids, queued status).
_PRESET_STRIP = ("id", "status", "started_at", "ended_at", "returncode",
                 "error", "log_file", "result_ref", "inserted_by", "outcome_note")


def presets_path(instance_path) -> Path:
    return Path(instance_path) / _PRESETS_FILENAME


def list_presets(instance_path) -> list[dict]:
    try:
        data = json.loads(Path(presets_path(instance_path)).read_text(encoding="utf-8"))
        out = data.get("presets") or []
        return out if isinstance(out, list) else []
    except (OSError, ValueError):
        return []


def _save_presets(instance_path, presets: list[dict]) -> None:
    safe_io.atomic_write_json(presets_path(instance_path), {"presets": presets})


def save_preset(instance_path, name: str) -> dict:
    """Snapshot the current queue (ordered, runtime fields stripped) as a preset."""
    with _QLOCK:
        state = load_queue(instance_path)
        items = []
        for it in sorted(state["queue"], key=lambda x: x.get("order", 0)):
            snap = {k: v for k, v in it.items() if k not in _PRESET_STRIP}
            items.append(snap)
        preset = {
            "id": uuid.uuid4().hex[:8],
            "name": str(name or "preset")[:80],
            "created_at": _now(),
            "items": items,
        }
        presets = [p for p in list_presets(instance_path)]
        presets.append(preset)
        _save_presets(instance_path, presets)
        return preset


def delete_preset(instance_path, preset_id: str) -> None:
    with _QLOCK:
        _save_presets(instance_path,
                      [p for p in list_presets(instance_path) if p.get("id") != preset_id])


def load_preset(instance_path, preset_id: str, mode: str = "append") -> tuple[dict | None, list[str]]:
    """Materialise a preset into the queue (``mode`` = "append" | "replace").

    Every entry's ``source_file`` is re-scanned fresh (files drift under
    presets: renamed/edited nodes would otherwise fail at RUN time with the
    fail-closed kind/hook drift gate) — missing or reclassified files are
    dropped with a warning instead of poisoning the queue.

    Returns ``(state, warnings)``; state is None when the preset id is unknown.
    """
    from . import node_scan  # local import: keep module import-light for tests
    preset = next((p for p in list_presets(instance_path) if p.get("id") == preset_id), None)
    if preset is None:
        return None, ["preset not found"]
    warnings: list[str] = []
    fresh_items: list[dict] = []
    for entry in preset.get("items") or []:
        src = entry.get("source_file")
        try:
            info = node_scan.scan_file(src)
        except Exception:
            info = None
        if info is None or not getattr(info, "name", None) or getattr(info, "error", None):
            warnings.append(f"skipped (missing/unscannable): {src}")
            continue
        fresh = dict(entry)
        # trust the FRESH classification, keep the preset's overrides/targets/rules
        fresh["source_file"] = src
        fresh["file"] = src
        fresh["name"] = info.name
        fresh["kind"] = info.kind
        fresh["has_hook"] = bool(getattr(info, "has_hook", False))
        fresh["targets_name"] = getattr(info, "targets_name", None) or entry.get("targets_name") or "qubits"
        if entry.get("kind") and entry["kind"] != info.kind:
            warnings.append(f"{info.name}: kind changed since preset was saved ({entry['kind']} → {info.kind})")
        fresh_items.append(fresh)
    with _QLOCK:
        state = load_queue(instance_path)
        if mode == "replace":
            # never drop a running item — replace only replaces the editable tail
            state["queue"] = [it for it in state["queue"] if it.get("status") == "running"]
        base = len(state["queue"])
        for k, info in enumerate(fresh_items):
            item = _new_item(info, info.get("targets"))
            item["order"] = base + k
            state["queue"].append(item)
        _renumber(state)
        save_queue(instance_path, state)
        return state, warnings


# ----------------------------------------------------------------------
# Subprocess spawn / kill (platform-aware)
# ----------------------------------------------------------------------

def _spawn(argv: list[str], log_path: Path):
    """Popen *argv* in its own process group, stdout+stderr → *log_path*."""
    logf = open(log_path, "wb")
    kwargs: dict = {"stdout": logf, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(argv, **kwargs)
    except Exception:
        logf.close()  # don't leak the handle if the interpreter path is bad
        raise
    return proc, logf


def _kill(proc) -> None:
    """Terminate a process *and its descendants* (qm/grpc children)."""
    if proc is None or proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True)
        else:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(pgid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        logger.debug("kill failed for pid %s", getattr(proc, "pid", "?"), exc_info=True)


def _classify_result(work_dir: Path, returncode: int) -> tuple[str, str | None]:
    """Map a finished run to (status, error) from its ``_result.json``."""
    result_file = work_dir / "_result.json"
    if not result_file.exists():
        return "failed", f"no _result.json (rc={returncode}) — the run did not complete"
    try:
        parsed = json.loads(result_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return "failed", f"could not read _result.json: {exc}"
    if parsed.get("status") == "ok":
        return "done", None
    return "failed", parsed.get("error") or "the run reported an error"


# ----------------------------------------------------------------------
# Run one item
# ----------------------------------------------------------------------

def _prepare_content(item: dict, settings: dict) -> str:
    """Build the prepared source (overrides spliced) for an item, or verbatim."""
    src = Path(item["source_file"]).read_text(encoding="utf-8")
    kind = item.get("kind")
    if kind == "node" and item.get("has_hook"):
        overrides = node_inject.build_node_overrides(
            item.get("targets_name"), item.get("targets"),
            simulate=bool(settings.get("global_simulate", True)),
            extra=item.get("param_overrides"),
        )
        return node_inject.splice_node(src, overrides)
    if kind == "graph" and item.get("targets"):
        # Override the single graph-level targets field (Phase 3); the runtime
        # fans it out to every member node. No targets = run the graph as-authored.
        return node_inject.splice_graph(
            src, item.get("targets_name") or "qubits", item.get("targets"))
    # hookless node, or a graph with no targets override → run verbatim
    return src


def _run_item(instance_path, item: dict, settings: dict, runner: dict) -> dict:
    """Spawn + wait for one item. Returns {status, error, returncode, log_file}."""
    log_path = _logs_dir(instance_path) / f"{item['id']}.log"
    log_str = str(log_path)
    source = Path(item["source_file"])
    if not source.exists():
        return {"status": "failed", "error": f"source file missing: {source}",
                "returncode": None, "log_file": log_str}
    if not settings.get("env_python"):
        return {"status": "failed", "error": "no env selected",
                "returncode": None, "log_file": log_str}

    # --- Re-classify from the CURRENT file bytes (airtight safety) --------
    # The queued kind/has_hook/targets_name were server-derived at add time, but
    # the .py may have changed since (an overnight edit, or a stat-cache that
    # missed a mtime+size-preserving change). Re-derive fresh and REFUSE on any
    # mismatch — never run a graph as a node, or on the wrong target type.
    fresh = node_scan.scan_file(source)
    if fresh.error is not None:                  # can't re-classify -> fail CLOSED, don't run blind
        return {"status": "failed",
                "error": f"source unreadable/unparseable since queued ({fresh.error}) — remove and re-add",
                "returncode": None, "log_file": log_str}
    if (fresh.kind != item.get("kind")
            or bool(fresh.has_hook) != bool(item.get("has_hook"))
            or fresh.targets_name != item.get("targets_name")):
        return {"status": "failed",
                "error": ("source changed since queued (was "
                          + f"{item.get('kind')}/{item.get('targets_name')}"
                          + (", hook" if item.get("has_hook") else "")
                          + f"; now {fresh.kind}/{fresh.targets_name}"
                          + (", hook" if fresh.has_hook else "")
                          + ") — remove and re-add"),
                "returncode": None, "log_file": log_str}

    # --- Dry-run safety gate ---------------------------------------------
    # A graph (no per-graph simulate field) and a hookless node can't have
    # simulate injected, so a dry run would silently hit hardware — refuse while
    # Dry run is on. With Dry run off, the user has accepted a real run.
    if settings.get("global_simulate", True) and (
            item.get("kind") == "graph" or not item.get("has_hook")):
        why = ("graph (no per-graph simulate field)" if item.get("kind") == "graph"
               else "no custom_param hook")
        return {"status": "skipped",
                "error": "dry-run can't be enforced (" + why + ") — "
                         "turn off Dry run to run this on hardware",
                "returncode": None, "log_file": log_str}

    # A graph resolves its member nodes BY NAME from the env's qualibrate
    # calibration_library.folder (not the file's own folder). If that differs
    # from the chosen calibrations folder the graph would run the wrong/stale
    # member nodes on hardware — refuse it.
    if item.get("kind") == "graph":
        eff = settings.get("effective_config") or {}
        lib = eff.get("calibration_library_folder")
        cal = settings.get("calibrations_folder")
        if lib and cal and not paths_equal(lib, cal):
            return {"status": "failed",
                    "error": ("graph member-node library mismatch: the env resolves "
                              "member nodes from " + str(lib) + ", but your calibrations "
                              "folder is " + str(cal) + " — point the env's qualibrate "
                              "calibration_library.folder at your folder"),
                    "returncode": None, "log_file": log_str}

    try:
        content = _prepare_content(item, settings)
    except (OSError, SyntaxError, ValueError,
            node_inject.NoHookError, node_inject.SpliceError) as exc:
        return {"status": "failed", "error": f"prepare failed: {exc}",
                "returncode": None, "log_file": log_str}

    timeout = settings.get("default_timeout_s")
    if not isinstance(timeout, int) or timeout <= 0:
        timeout = _DEFAULTS["default_timeout_s"]

    work_dir = None
    temp = None
    try:
        # Create the work dir + temp copy under one try/finally so neither leaks
        # if the other raises (a leftover _sched_*.py in the calibrations folder
        # is a documented hazard — docs/40 §1).
        work_dir = Path(tempfile.mkdtemp(prefix="quamsched_run_"))
        temp = node_inject.make_temp_copy(source, content)

        argv = [settings["env_python"], str(EXPERIMENT_SCRIPT),
                "--mode", "run", "--target", str(temp), "--out", str(work_dir)]
        if settings.get("quam_state_path"):
            argv += ["--state-path", settings["quam_state_path"]]
        # Pin the qualibrate config so storage.location / library come from the
        # verified config, not whatever is ambient in the env.
        cfg_file = (settings.get("effective_config") or {}).get("config_file")
        if cfg_file:
            argv += ["--config-file", cfg_file]

        proc, logf = _spawn(argv, log_path)
        with runner["proc_lock"]:
            runner["proc"] = proc
        timed_out = False
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill(proc)
            proc.wait()
        finally:
            logf.close()
            with runner["proc_lock"]:
                runner["proc"] = None

        rc = proc.returncode
        # Prefer the real result: if the node wrote a successful _result.json it
        # ran to completion, even if a timeout/cancel kill landed during the slow
        # qm/grpc teardown. Only override to cancelled/failed when it did NOT
        # complete.
        status, error = _classify_result(work_dir, rc)
        if status != "done":
            if runner["cancel"].is_set():
                status, error = "cancelled", "cancelled by user"
            elif timed_out:
                status, error = "failed", f"timed out after {timeout}s"
        return {"status": status, "error": error, "returncode": rc, "log_file": log_str}
    finally:
        if temp is not None:
            node_inject.cleanup_temp_copy(temp)
        if work_dir is not None:
            config_generator._cleanup_work_dir(work_dir)


# ----------------------------------------------------------------------
# Worker loop + control
# ----------------------------------------------------------------------

def _next_queued(state: dict) -> dict | None:
    for it in sorted(state["queue"], key=lambda x: x.get("order", 0)):
        if it.get("enabled", True) and it.get("status") == "queued":
            return it
    return None


def _worker(instance_path: str) -> None:
    runner = _RUNNERS[instance_path]
    cancel = runner["cancel"]
    try:
        while not cancel.is_set():
            with _QLOCK:
                state = load_queue(instance_path)
                if state["run"].get("status") == "paused":
                    break
                # Honor a pause requested while the previous item was running — now
                # that it's reaped, the lock can safely release.
                if state["run"].get("pause_requested"):
                    state["run"].update({"status": "paused", "message": "paused",
                                         "pause_requested": False, "current_id": None})
                    save_queue(instance_path, state)
                    break
                settings = load_settings(instance_path)
                # Heartbeat: pause if the browser stopped polling (unless tmux mode).
                if not settings.get("continue_without_ui"):
                    seen = _LAST_UI_SEEN.get(instance_path)
                    if seen is not None and time.time() - seen > HEARTBEAT_TIMEOUT_S:
                        state["run"].update({"status": "paused", "current_id": None,
                                             "message": "paused: browser disconnected"})
                        save_queue(instance_path, state)
                        break
                item = _next_queued(state)
                if item is None:
                    state["run"].update({"status": "idle", "current_id": None,
                                         "message": "queue complete"})
                    save_queue(instance_path, state)
                    break
                item["status"] = "running"
                item["started_at"] = _now()
                state["run"].update({"status": "running", "current_id": item["id"]})
                save_queue(instance_path, state)
                item_snapshot = dict(item)

            try:
                result = _run_item(instance_path, item_snapshot, settings, runner)
            except Exception as exc:  # noqa: BLE001 - keep the queue alive
                logger.exception("scheduler item %s crashed", item_snapshot.get("id"))
                result = {"status": "failed", "error": f"worker error: {exc}",
                          "returncode": None, "log_file": None}

            with _QLOCK:
                state = load_queue(instance_path)
                it = _find(state, item_snapshot["id"])
                if it is not None:
                    it["status"] = result["status"]
                    it["ended_at"] = _now()
                    it["returncode"] = result.get("returncode")
                    it["error"] = result.get("error")
                    it["log_file"] = result.get("log_file")
                # Bump a monotonic counter the UI watches as a progress signal.
                state["run"]["completed_count"] = state["run"].get("completed_count", 0) + 1
                # NB: leave status == 'running' here (don't flip to paused yet) so
                # the UI lock still covers the post-node refresh below.
                save_queue(instance_path, state)
            stop = (result["status"] == "failed"
                    and settings.get("failure_policy") == "stop")

            # Post-node refresh while the lock is still on: the injected hook pulls
            # the evolving chip + rescans datasets under an app context — runs even
            # with no browser tab open. Failures never sink the queue. The item's
            # status is passed so only a *successful* item gets a dataset ref.
            if _refresh_hook is not None and settings.get("quam_state_path"):
                try:
                    _refresh_hook(settings["quam_state_path"], item_snapshot["id"],
                                  result["status"])
                except Exception:  # noqa: BLE001
                    logger.exception("post-node refresh hook failed")

            if stop:
                with _QLOCK:
                    state = load_queue(instance_path)
                    state["run"].update({"status": "paused", "current_id": None,
                                         "message": f"stopped: {item_snapshot['name']} failed"})
                    save_queue(instance_path, state)
                break
            if cancel.is_set():
                break
    finally:
        with _QLOCK:
            state = load_queue(instance_path)
            if cancel.is_set():
                state["run"].update({"status": "idle", "message": "cancelled"})
            state["run"]["current_id"] = None
            save_queue(instance_path, state)
            # Clear liveness INSIDE the lock so is_running() and the persisted
            # run-state flip atomically (a resume start() can't be dropped).
            runner["thread"] = None


def is_running(instance_path) -> bool:
    runner = _RUNNERS.get(str(instance_path))
    t = runner["thread"] if runner else None
    return bool(t and t.is_alive())


def is_active(instance_path) -> bool:
    """True if a queue is actively running — UI mutators should be locked.

    Reconciles a crashed worker first so a stale 'running' flag can't lock the
    UI forever.
    """
    _reconcile_orphaned(instance_path)
    with _QLOCK:
        return load_queue(instance_path)["run"].get("status") == "running"


def _reconcile_orphaned(instance_path) -> dict | None:
    """If the file says 'running' but no live worker, mark it interrupted.

    Handles a Flask process restart mid-run (the daemon worker is gone). Full
    restart recovery / detached runs are v2.

    Returns the (possibly reconciled) queue state it loaded so callers like
    ``runner_status`` can reuse it instead of re-reading + re-parsing the queue
    file a second time per poll (finding B26). Returns ``None`` when it
    short-circuits because a live worker is running and no load was needed.
    """
    with _QLOCK:
        # Check liveness INSIDE the lock — otherwise a poll that read
        # is_running()==False while idle could acquire the lock just after a
        # start() spawned a live worker and mis-flag its in-flight item.
        if is_running(instance_path):
            return None
        state = load_queue(instance_path)
        changed = False
        msg = "interrupted (worker stopped or app restarted)"
        if state["run"].get("status") == "running":
            state["run"].update({"status": "idle", "current_id": None, "message": msg})
            changed = True
        for it in state["queue"]:
            if it.get("status") == "running":
                it["status"] = "failed"
                it["error"] = msg
                it["ended_at"] = _now()
                changed = True
        if changed:
            save_queue(instance_path, state)
        return state


def start(instance_path) -> dict:
    """Start (or resume) the worker. Returns the current run state."""
    instance_path = str(instance_path)
    with _QLOCK:
        if is_running(instance_path):
            return load_queue(instance_path)["run"]
        runner = {"thread": None, "cancel": threading.Event(),
                  "proc": None, "proc_lock": threading.Lock()}
        _RUNNERS[instance_path] = runner
        settings = load_settings(instance_path)
        # Sweep any orphan _sched_*.py temp copies left by a crashed/interrupted
        # run before we start spawning (docs/40 §1 hazard).
        folder = settings.get("calibrations_folder")
        if folder:
            node_inject.cleanup_orphan_temp_copies(folder)
        state = load_queue(instance_path)
        # Clear a stale 'running' item from a prior crashed run before starting.
        for it in state["queue"]:
            if it.get("status") == "running":
                it["status"] = "queued"
        state["run"].update({"status": "running", "started_at": _now(),
                             "current_id": None, "message": "",
                             "completed_count": 0, "pause_requested": False})
        save_queue(instance_path, state)
        touch_ui(instance_path)  # fresh heartbeat so the worker doesn't pause immediately
        t = threading.Thread(target=_worker, args=(instance_path,), daemon=True)
        runner["thread"] = t
        t.start()
        return state["run"]


def pause(instance_path) -> dict:
    """Request a pause: the worker stops AFTER the current item is reaped.

    Sets a flag rather than flipping status to 'paused' immediately — otherwise
    the UI lock (which keys on status=='running') would release while the current
    node subprocess is still driving the chip/OPX.
    """
    instance_path = str(instance_path)
    with _QLOCK:
        state = load_queue(instance_path)
        if state["run"].get("status") == "running":
            state["run"]["pause_requested"] = True
            state["run"]["message"] = "pausing after the current item…"
            save_queue(instance_path, state)
        return state["run"]


def cancel(instance_path) -> dict:
    """Cancel: kill the running item (and its descendants) and stop the queue."""
    instance_path = str(instance_path)
    runner = _RUNNERS.get(instance_path)
    if runner is not None:
        runner["cancel"].set()
        with runner["proc_lock"]:
            _kill(runner.get("proc"))
    with _QLOCK:
        state = load_queue(instance_path)
        state["run"].update({"status": "idle", "current_id": None, "message": "cancelled",
                             "pause_requested": False})
        save_queue(instance_path, state)
        return state["run"]


def runner_status(instance_path) -> dict:
    """Snapshot for the UI poll: reconciles orphans, returns queue + run state.

    Also records the browser-alive heartbeat (this endpoint is the poll).
    """
    touch_ui(instance_path)
    # Reuse the queue state ``_reconcile_orphaned`` already loaded — it returns
    # None only when it short-circuited (a live worker is running and nothing
    # was read), in which case we load once here (finding B26: avoids a second
    # read + JSON parse of the queue on every ~2.5s poll).
    with _QLOCK:
        state = _reconcile_orphaned(instance_path)
        if state is None:
            state = load_queue(instance_path)
        state["running"] = is_running(instance_path)
        return state


def tail_log(instance_path, item_id: str, max_bytes: int = 16384) -> str:
    """Return the tail of an item's stdout log (best-effort)."""
    if not re.fullmatch(r"[0-9a-f]{8}", item_id or ""):
        return ""
    log_path = _logs_dir(instance_path) / f"{item_id}.log"
    try:
        with open(log_path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            data = f.read()
    except OSError:
        return ""
    return data.decode("utf-8", errors="replace")
