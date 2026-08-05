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
* **Queue** — durable per-chip ``<scope>/scheduler_queue.json`` CRUD
  (add/reorder/…); see :func:`scope_dir` for what a scope is (docs/80).
* **Runner** — the background daemon worker: spawn/kill (process groups), the
  dry-run + graph-library safety gates, failure policy, heartbeat, cancellation.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from quam_state_manager.core import (
    config_generator,
    history,
    instances,
    node_inject,
    node_scan,
    path_match,
    safe_io,
)

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
    ``<instance>/scheduler_scan_cache.json`` keyed on (folder, env signature,
    folder fingerprint); a 2nd..Nth load with no changes is an instant disk read.
    The env signature folds in site-packages mtime, so a ``pip install`` that
    changes a library a node imports re-scans (interpreter mtime alone wouldn't).
    """
    cache_path = Path(instance_path) / _SCAN_CACHE_FILENAME if instance_path else None
    key = "|".join([
        norm_path(folder) or "",
        python_path or "",
        str(config_generator._env_signature(python_path)),
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
# Per-chip scope (docs/80 Part 4)
# ----------------------------------------------------------------------
#
# The runner's state used to live directly in the instance dir — ONE
# scheduler.json and ONE scheduler_queue.json for the whole machine. That is
# what stopped two windows from driving two different chips: not PIDs, not
# anything in ``qm`` (which is happily multi-client), just our storage layout.
# Window 2 picking its chip rewrote the ``quam_state_path`` window 1's worker
# re-reads per item, and both windows saw one queue.
#
# So runner state is now keyed by the chip it belongs to. Every scheduler
# entry point already takes a directory, and the web layer resolves that
# directory in exactly one place (``routes._sched_inst``), so the change is a
# different path rather than a different API.

_SCOPE_ROOT = "scheduler"
_SCOPE_FALLBACK = "_nochip"

# Memo for scope resolution. Resolving a scope costs a real filesystem call
# (``Path.resolve`` inside both ``fs_key`` and ``chip_name_for``) — measured at
# ~205us locally, and a 9p/network workspace is far worse. That was fine when
# the web layer's scope lookup was an attribute read, but ``_sched_inst`` now
# resolves one on EVERY non-GET request (the edit-lock guard) and several times
# per 2.5s status poll. The mapping is a pure function of its inputs and the
# number of distinct chips in a session is tiny (the context cache holds 10),
# so a small bounded memo removes the cost entirely.
_SCOPE_MEMO: dict[tuple[str, str], Path] = {}
_SCOPE_MEMO_MAX = 64
_SCOPE_MEMO_LOCK = threading.Lock()


def scope_dir(instance_path, chip_path=None) -> Path:
    key = (str(instance_path), str(chip_path or ""))
    with _SCOPE_MEMO_LOCK:
        hit = _SCOPE_MEMO.get(key)
    if hit is not None:
        return hit
    out = _scope_dir_uncached(instance_path, chip_path)
    with _SCOPE_MEMO_LOCK:
        if len(_SCOPE_MEMO) >= _SCOPE_MEMO_MAX:
            _SCOPE_MEMO.clear()
        _SCOPE_MEMO[key] = out
    return out


def _scope_dir_uncached(instance_path, chip_path=None) -> Path:
    """Where THIS chip's runner state lives.

    ``<instance>/scheduler/<chip-name>-<path-hash>``. The hash is over
    :func:`path_match.fs_key`, the same per-OS canonical identity the working
    copies use, so a chip is one scope no matter how its path was spelled; the
    readable prefix is there so the folder means something to a human.

    With no chip open we fall back to a shared ``_nochip`` scope: the runner
    page is not usable without a chip anyway (the preflight requires one), and
    inventing a scope per empty session would scatter state.
    """
    root = Path(instance_path) / _SCOPE_ROOT
    if not chip_path:
        return root / _SCOPE_FALLBACK
    try:
        key = path_match.fs_key(chip_path) or str(chip_path)
    except Exception:       # noqa: BLE001
        key = str(chip_path)
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]
    # Same readable-prefix rule the working copies use: every chip folder is
    # literally named "quam_state", so the leaf name alone would make every
    # scope indistinguishable on disk.
    try:
        label = history.chip_name_for(Path(str(chip_path)))
    except Exception:       # noqa: BLE001
        label = Path(str(chip_path)).name
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", label or "")[:40] or "chip"
    return root / f"{name}-{digest}"


def shared_settings_path(scope) -> Path:
    """Machine-level settings, one level up from any scope."""
    return Path(scope).parent / "_shared.json"


def migrate_legacy_scope(instance_path) -> dict:
    """Move pre-scope runner state into the scope it belongs to (docs/80).

    The old layout put ``scheduler.json`` / ``scheduler_queue.json`` /
    ``scheduler_logs/`` straight in the instance dir. Which chip did that queue
    belong to? The settings say so: ``quam_state_path`` is the chip the worker
    was pointed at. With no chip recorded it lands in the no-chip scope, where
    the first session without a chip open will find it.

    Run once at startup (flag-file gated) rather than lazily at scope
    resolution, so WHICH scope adopts the legacy queue does not depend on who
    asked first. Idempotent and never fatal — a failed migration must leave the
    old files exactly where they are rather than lose a queue.
    """
    inst = Path(instance_path)
    marker = inst / _SCOPE_ROOT / ".migrated_v1"
    out = {"migrated": False, "scope": None}
    legacy_settings = inst / _SETTINGS_FILENAME
    legacy_queue = inst / _QUEUE_FILENAME
    if marker.exists():
        return out
    try:
        (inst / _SCOPE_ROOT).mkdir(parents=True, exist_ok=True)
        if not legacy_settings.exists() and not legacy_queue.exists():
            marker.write_text("nothing to migrate\n", encoding="utf-8")
            return out
        settings = _read_settings_file(legacy_settings)
        target = scope_dir(inst, settings.get("quam_state_path") or None)
        target.mkdir(parents=True, exist_ok=True)

        if settings:
            shared = {k: v for k, v in settings.items() if k in _SHARED_KEYS}
            own = {k: v for k, v in settings.items()
                   if k in _DEFAULTS and k not in _SHARED_KEYS}
            if shared and not shared_settings_path(target).exists():
                safe_io.atomic_write_json(shared_settings_path(target), shared)
            if own and not settings_path(target).exists():
                safe_io.atomic_write_json(settings_path(target), own)
        if legacy_queue.exists() and not queue_path(target).exists():
            shutil.copy2(legacy_queue, queue_path(target))
        legacy_logs = inst / _LOGS_DIRNAME
        if legacy_logs.is_dir() and not (target / _LOGS_DIRNAME).exists():
            shutil.copytree(legacy_logs, target / _LOGS_DIRNAME)

        # Copy first, VERIFY, only then delete. A move that turns out wrong
        # loses a queue; this cannot, because nothing is removed until the new
        # copy has been read back and found to hold the same items. If the
        # check fails we keep both and say so — a leftover file is inert (no
        # code reads these paths any more), a lost queue is not.
        if _verify_migrated(legacy_queue, target, settings):
            legacy_queue.unlink(missing_ok=True)
            legacy_settings.unlink(missing_ok=True)
            if legacy_logs.is_dir():
                shutil.rmtree(legacy_logs, ignore_errors=True)
            out["removed_legacy"] = True
        else:
            logger.warning(
                "scheduler: legacy runner state copied to %s but could not be "
                "verified; the originals were kept", target)

        marker.write_text(f"migrated to {target.name}\n", encoding="utf-8")
        out.update({"migrated": True, "scope": target.name})
        logger.info("scheduler: legacy runner state adopted into scope %s", target.name)
    except Exception:       # noqa: BLE001 — never block startup
        logger.warning("scheduler legacy scope migration failed", exc_info=True)
    return out


def _verify_migrated(legacy_queue: Path, target: Path, settings: dict) -> bool:
    """Is the migrated copy provably as good as the original?

    Checks the only two things whose loss would matter: every queue item id
    survived, and the settings that were set are readable back through the
    normal (shared + per-chip) load path. Anything unexpected reads as "not
    verified", which keeps the originals.
    """
    try:
        if legacy_queue.exists():
            old = json.loads(legacy_queue.read_text(encoding="utf-8"))
            new = load_queue(target)
            old_ids = [i.get("id") for i in (old.get("queue") or [])]
            new_ids = [i.get("id") for i in (new.get("queue") or [])]
            if old_ids != new_ids:
                return False
        if settings:
            got = load_settings(target)
            for key, value in settings.items():
                if key in _DEFAULTS and got.get(key) != value:
                    return False
        return True
    except Exception:       # noqa: BLE001 — unverifiable ⇒ keep the originals
        logger.debug("migration verification failed", exc_info=True)
        return False


def cancel_all_local() -> list[str]:
    """Cancel every run THIS process is actually driving (docs/80).

    The window-close path used to cancel by instance dir, which with per-chip
    scopes would reach at most one of this process's runs — and, before
    ownership existed, could reach someone else's. Iterating our own runner
    registry is both complete and incapable of touching another window's run.
    """
    cancelled = []
    for scope in list(_RUNNERS.keys()):
        try:
            cancel(scope)
            cancelled.append(scope)
        except Exception:   # noqa: BLE001
            logger.warning("cancel on exit failed for %s", scope, exc_info=True)
    return cancelled


# ----------------------------------------------------------------------
# Settings persistence
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

# Which settings are facts about the MACHINE rather than about a chip.
#
# Splitting these out is the difference between per-chip scoping being a
# feature and being a chore: the conda env and the node library are the same
# for every chip in a lab, and asking for them again per chip would be a
# regression dressed up as isolation. Everything NOT listed here is per-chip —
# above all ``quam_state_path``, which IS the chip, and which the worker
# re-reads per item (a shared copy is precisely how window 2 used to redirect
# window 1's run).
_SHARED_KEYS = frozenset({
    "calibrations_folder", "env_python", "default_timeout_s",
    "continue_without_ui",
})


def settings_path(scope) -> Path:
    return Path(scope) / _SETTINGS_FILENAME


def _read_settings_file(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}
    except (OSError, ValueError):
        logger.warning("Could not read scheduler settings %s", path, exc_info=True)
        return {}


def load_settings(scope) -> dict:
    """Settings for this scope: defaults ← machine-level ← this chip's."""
    merged = dict(_DEFAULTS)
    shared = _read_settings_file(shared_settings_path(scope))
    merged.update({k: v for k, v in shared.items() if k in _SHARED_KEYS})
    own = _read_settings_file(settings_path(scope))
    merged.update({k: v for k, v in own.items()
                   if k in _DEFAULTS and k not in _SHARED_KEYS})
    return merged


def save_settings(scope, settings: dict) -> dict:
    """Persist settings atomically, routing each key to its home.

    Guarded by _QLOCK so a debounced settings POST can't lose-update against the
    effective-config write (both do a read-modify-write).
    A non-positive/invalid ``default_timeout_s`` is clamped to the default so the
    run watchdog can never be silently disabled.
    """
    with _QLOCK:
        current = load_settings(scope)
        current.update({k: v for k, v in (settings or {}).items() if k in _DEFAULTS})
        try:
            t = int(current.get("default_timeout_s"))
        except (TypeError, ValueError):
            t = _DEFAULTS["default_timeout_s"]
        current["default_timeout_s"] = t if t > 0 else _DEFAULTS["default_timeout_s"]

        Path(scope).mkdir(parents=True, exist_ok=True)
        shared_path = shared_settings_path(scope)
        shared = _read_settings_file(shared_path)
        shared.update({k: current[k] for k in _SHARED_KEYS})
        safe_io.atomic_write_json(shared_path, shared)
        safe_io.atomic_write_json(
            settings_path(scope),
            {k: v for k, v in current.items() if k not in _SHARED_KEYS})
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
    """Canonical comparison form for a folder path (per-OS identity).

    Delegates to :func:`path_match.fs_key` — case-folded ONLY on hosts whose
    default filesystem is case-insensitive (Windows/macOS); the old
    unconditional ``.lower()`` falsely equated distinct case-variant folders
    on Linux. Returns ``None`` for a falsy path.
    """
    if not p:
        return None
    return path_match.fs_key(p)


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
    # Ancestor check on the RESOLVED Paths — the old separator-sniffed
    # ``startswith`` falsely equated Linux case-twins (both sides lowered)
    # and mis-sniffed a backslash inside a POSIX dir name as a separator.
    # Path comparison is per-OS (case-folded on Windows).
    return cal.is_relative_to(inst)


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
    ws_paths = []
    for r in (workspace_roots or []):
        if not r:
            continue
        try:
            ws_paths.append(Path(r).resolve())
        except OSError:
            continue
    for ds in dataset_roots:
        if not ds:
            return False
        try:
            dsp = Path(ds).resolve()
        except OSError:
            return False
        # Equality via fs_key (per-OS case fold); ancestry via is_relative_to
        # on resolved Paths — the old separator-sniffed startswith falsely
        # matched Linux case-twins and backslash-bearing POSIX names.
        covered = any(
            paths_equal(dsp, w) or dsp.is_relative_to(w) for w in ws_paths
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
# All durable state lives in <scope>/scheduler_queue.json; per-run stdout in
# <scope>/scheduler_logs/<id>.log, where <scope> is the per-chip directory
# resolved by scope_dir (docs/80). The worker is a Flask-free daemon thread
# keyed on that scope — it reads settings + queue from disk, spawns
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
    """Record that the UI just polled (browser-alive heartbeat).

    Feeds EVERY runner this process is driving, not just the polled scope
    (docs/80 Part 4). The heartbeat has always meant "a browser is still
    there" — it exists to notice a CLOSED tab, which is why its window is 90s
    (long enough to survive a backgrounded tab's clamped timers). Once runner
    state became per-chip, keying it strictly to the polled scope would have
    made *switching chips* look like a disconnect and paused the run on the
    chip you navigated away from — precisely what a two-chip user does when
    they start a run on chip A and go look at chip B.
    """
    now = time.time()
    _LAST_UI_SEEN[str(instance_path)] = now
    for scope in list(_RUNNERS.keys()):
        _LAST_UI_SEEN[scope] = now


# ----------------------------------------------------------------------
# Cross-process run ownership (docs/80)
# ----------------------------------------------------------------------
#
# The queue is a file; ``is_running`` is an in-memory registry. A SECOND State
# Manager process therefore sees "the file says running" and "no worker of
# mine", which is indistinguishable from a crashed worker — and it acted on
# that, with three reproduced consequences: a mere /scheduler/status poll
# reconciled the other window's live run to idle and marked its in-flight item
# failed; pressing Start spawned a SECOND worker over the same queue (two
# workers driving one OPX); and closing the window wrote "cancelled" over a run
# it did not own.
#
# ``run.owner_pid`` closes all three by making the distinction recordable. It
# is deliberately a hint, not a lock: an entry with no owner (a queue written
# before this existed, or by a genuinely crashed process) keeps the exact
# pre-existing behaviour, so crash recovery is untouched.

def _run_owner(state: dict) -> tuple[int | None, int | None]:
    run = state.get("run") or {}
    try:
        pid = int(run.get("owner_pid")) if run.get("owner_pid") else None
    except (TypeError, ValueError):
        pid = None
    try:
        port = int(run.get("owner_port")) if run.get("owner_port") else None
    except (TypeError, ValueError):
        port = None
    return pid, port


def foreign_owner(state: dict) -> tuple[int, int | None] | None:
    """``(pid, port)`` of ANOTHER live process that owns this run, else None.

    None covers all three safe cases: nobody claimed it, we claimed it, or the
    claimant is gone (a real crash — reconcile away).
    """
    pid, port = _run_owner(state)
    if not pid or pid == os.getpid():
        return None
    if not instances.pid_alive(pid):
        return None
    return pid, port


def owner_label(pid: int, port: int | None) -> str:
    return f"port {port} · PID {pid}" if port else f"PID {pid}"


# This process's HTTP port, stamped into a run we claim so the OTHER window can
# name us in its warning. Set by the web layer once the first request reveals it
# (the port is chosen outside create_app). Deliberately a module global rather
# than a registry lookup: the scheduler's directory argument becomes a per-chip
# scope in a later step, so it must not be the key for a process-level fact.
_OWN_PORT: int | None = None


def set_own_port(port: int | None) -> None:
    global _OWN_PORT
    _OWN_PORT = int(port) if port else None


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


def save_queue_dir(instance_path) -> Path:
    """Ensure the scope dir exists before a queue write lands in it."""
    d = Path(instance_path)
    d.mkdir(parents=True, exist_ok=True)
    return d


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
    save_queue_dir(instance_path)
    safe_io.atomic_write_json(queue_path(instance_path), state)


def _persist_worker_pid(instance_path, pid) -> None:
    """Record (pid) or clear (pid=None) the running experiment subprocess's OS
    PID in the queue file, so a post-crash _reconcile_orphaned can probe whether
    that subprocess outlived a killed SM process before it unlocks editing.
    Non-fatal: PID tracking is a safety hint, never a gate."""
    try:
        with _QLOCK:
            state = load_queue(instance_path)
            state["run"]["worker_pid"] = int(pid) if pid else None
            save_queue(instance_path, state)
    except Exception:   # noqa: BLE001
        logger.debug("could not persist worker pid", exc_info=True)


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
        it = _find(state, item_id)
        if it is not None and it.get("status") == "running":
            # Never drop the running item — its subprocess is driving hardware, and
            # removing the row would hide a live run (the worker's terminal write
            # then finds nothing → result/log/error silently lost) WITHOUT killing
            # it. Mirrors load_preset's 'never drop a running item' invariant.
            return
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
        if it.get("status") == "running":
            # Expanding the running item would delete its row (dangling current_id,
            # lost result) AND enqueue fresh per-target copies that RE-RUN on
            # hardware after the in-flight all-targets run finishes — double
            # execution the user never intended. Refuse.
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


def presets_path(scope) -> Path:
    """Queue presets live BESIDE the scopes, not inside one (docs/80 Part 4).

    A preset is "these nodes, in this order, with these overrides" — a recipe
    for a measurement routine, not a fact about one device. A lab that builds
    a good tune-up sequence on chip A wants it on chip B; scoping presets per
    chip would silently hide the user's own saved work the moment they opened
    a different device. So they sit one level up, next to ``_shared.json``.
    """
    return Path(scope).parent / _PRESETS_FILENAME


def list_presets(instance_path) -> list[dict]:
    try:
        data = json.loads(Path(presets_path(instance_path)).read_text(encoding="utf-8"))
        out = data.get("presets") or []
        return out if isinstance(out, list) else []
    except (OSError, ValueError):
        return []


def _save_presets(instance_path, presets: list[dict]) -> None:
    path = presets_path(instance_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_io.atomic_write_json(path, {"presets": presets})


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


# Best-effort EXISTENCE probe for a pid — never kills, only checks. Used by
# _reconcile_orphaned to tell a genuinely-gone worker (safe to unlock editing)
# from an experiment subprocess that outlived a crashed SM process (still
# driving the OPX → must NOT silently unlock), and by the instance registry to
# tell a live sibling window from a crashed one. ONE implementation, in
# core.instances, so the two can never drift; the alias keeps this module's
# established name (and its pins) working.
_pid_alive = instances.pid_alive


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

        # Cancel can fire during the (hundreds-of-ms on a 9p mount) pre-spawn prep
        # above — re-classify, splice, mkdtemp, temp copy. Check right before launch
        # so a cancelled item never starts an experiment on the OPX in the window
        # after cancel() already flipped the UI back to idle.
        if runner["cancel"].is_set():
            return {"status": "cancelled", "error": "cancelled by user",
                    "returncode": None, "log_file": log_str}
        proc, logf = _spawn(argv, log_path)
        with runner["proc_lock"]:
            runner["proc"] = proc
            # Re-check under the SAME lock cancel() takes: if cancel landed between
            # the check above and registering proc, its _kill saw proc=None and did
            # nothing — so kill it here. Closes the spawn/kill race.
            if runner["cancel"].is_set():
                _kill(proc)
        # Persist the OS PID so a post-crash restart can tell a live orphan
        # (still driving the OPX) from a genuinely-gone worker before unlocking.
        _persist_worker_pid(instance_path, getattr(proc, "pid", None))
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
            _persist_worker_pid(instance_path, None)   # subprocess done → clear

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
                                         "message": "queue complete",
                                         # release the docs/80 claim
                                         "owner_pid": None, "owner_port": None})
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
            if state["run"].get("status") != "running":
                state["run"]["owner_pid"] = None      # docs/80: claim released
                state["run"]["owner_port"] = None
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
        # docs/80: another LIVE State Manager process owns this run. Its worker
        # is alive in ITS memory and invisible in ours, so "no worker of mine"
        # means nothing here. Touch nothing — a poll from a second window used
        # to flip the owner's run to idle and mark its in-flight item failed,
        # releasing the edit lock while an experiment was still driving the OPX.
        owner = foreign_owner(state)
        if owner is not None:
            pid, port = owner
            msg = (f"Another State Manager window ({owner_label(pid, port)}) "
                   "is running the Experiment Runner.")
            if (state.get("run") or {}).get("message") != msg:
                state["run"]["message"] = msg
                save_queue(instance_path, state)
            return state
        # Hardware safety: if the file says 'running' but no in-memory worker,
        # the experiment subprocess MIGHT have outlived a killed SM process and
        # still be driving the OPX. Probe the persisted PID: if it's alive, keep
        # the run flagged 'running' (editing stays locked) with a clear warning
        # rather than silently unlocking — the user verifies the OPX is idle and
        # then Start clears it (start() resets stale 'running' items). Only when
        # the worker is provably gone do we reconcile to idle.
        if state["run"].get("status") == "running" and _pid_alive(state["run"].get("worker_pid")):
            warn = ("⚠ An experiment from a previous session (PID "
                    f"{state['run'].get('worker_pid')}) may still be running on the "
                    "OPX. Verify it is idle, then Start to clear.")
            if state["run"].get("message") != warn:
                state["run"]["message"] = warn
                save_queue(instance_path, state)
            return state

        changed = False
        msg = "interrupted (worker stopped or app restarted)"
        if state["run"].get("status") == "running":
            state["run"].update({"status": "idle", "current_id": None,
                                  "message": msg, "worker_pid": None,
                                  "owner_pid": None, "owner_port": None})
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


class ForeignRunnerError(RuntimeError):
    """Another live State Manager process owns this queue's run (docs/80)."""

    def __init__(self, pid: int, port: int | None):
        self.pid = pid
        self.port = port
        super().__init__(
            f"Another State Manager window ({owner_label(pid, port)}) is running "
            "the Experiment Runner. Two runners on one queue would drive the "
            "same OPX at once.")


def start(instance_path) -> dict:
    """Start (or resume) the worker. Returns the current run state.

    Raises :class:`ForeignRunnerError` when another live process owns the run.
    This is the ONE place multi-window use is refused rather than merely
    reported: a second worker over the same queue means two processes driving
    one OPX, which no warning can make safe.
    """
    instance_path = str(instance_path)
    with _QLOCK:
        if is_running(instance_path):
            return load_queue(instance_path)["run"]
        owner = foreign_owner(load_queue(instance_path))
        if owner is not None:
            raise ForeignRunnerError(*owner)
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
                             "completed_count": 0, "pause_requested": False,
                             # Claim the run for THIS process (docs/80).
                             "owner_pid": os.getpid(), "owner_port": _OWN_PORT})
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
    if runner is None and foreign_owner(load_queue(instance_path)) is not None:
        # docs/80: not ours to cancel. This path is reached by the window-close
        # handler (main._kill_scheduler), so without this guard merely CLOSING
        # a second window rewrote another window's live run as "cancelled".
        return load_queue(instance_path)["run"]
    if runner is not None:
        runner["cancel"].set()
        with runner["proc_lock"]:
            _kill(runner.get("proc"))
        # Sweep the injected _sched_*.py temp copy the killed item left in the
        # customer's calibrations folder: the worker's own finally cleanup does NOT
        # run when cancel (or the os._exit window-close via _kill_scheduler) kills
        # it mid-run, and a leftover marked copy can be picked up by qualibrate's
        # own scanner as a real node. Best-effort.
        try:
            folder = load_settings(instance_path).get("calibrations_folder")
            if folder:
                node_inject.cleanup_orphan_temp_copies(folder)
        except Exception:  # noqa: BLE001
            logger.warning("scheduler temp-copy sweep on cancel failed", exc_info=True)
    with _QLOCK:
        state = load_queue(instance_path)
        if is_running(instance_path):
            # A live worker will do the terminal idle-flip in its own finally once
            # it reaps the killed item. Do NOT flip to 'idle' here: is_active()
            # keys the edit lock on status=='running', and flipping it now would
            # release the lock while the just-killed experiment may still be tearing
            # down (or, in the pre-spawn window, about to launch — now caught by the
            # cancel check in _run_item). Keep 'running' so the lock holds through
            # the cancel window; the worker settles it to 'idle' within moments.
            state["run"]["message"] = "cancelling…"
            state["run"]["pause_requested"] = False
        else:
            state["run"].update({"status": "idle", "current_id": None,
                                 "message": "cancelled", "pause_requested": False,
                                 "owner_pid": None, "owner_port": None})
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
