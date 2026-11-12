"""The ONE answer to "which calibrations folder does this window use?"

Order (decisions D-1/D-3/D-5/D-6, agents ver180):

1. the person's override for the scoped project (kept in SM's setup record,
   keyed by project AND the qualibrate config dir, so a second config dir
   never matches);
2. the project's ``[qualibrate.calibration_library].folder`` -- read live
   from its config, the ``native`` form, and it must be an existing folder;
3. the legacy machine-wide scheduler setting ``calibrations_folder`` -- ONLY
   when no project is scoped.

A scoped project whose value is unset, unreadable, unmappable or missing
resolves to ``None`` with a reason; it never falls back to the legacy
setting silently (the page offers that as one explicit click).

SM never writes ``~/.qualibrate`` and never writes the scheduler setting
from here.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

_MEMO: dict = {}
_LOCK = threading.Lock()


def _norm(p: str | os.PathLike | None) -> str:
    if not p:
        return ""
    try:
        return os.path.normcase(os.path.realpath(str(p)))
    except (OSError, ValueError):
        return os.path.normcase(os.path.abspath(str(p)))


def _stat_key(path: str | os.PathLike | None) -> tuple:
    try:
        st = os.stat(path)  # type: ignore[arg-type]
        return (st.st_mtime_ns, st.st_size)
    except (OSError, TypeError, ValueError):
        return (None,)


def _is_dir(p: str | None) -> bool:
    try:
        return bool(p) and Path(p).is_dir()
    except OSError:
        return False


def load_override(inst, project: str | None, cfg_dir: str | None) -> dict | None:
    """The person's override for *project* under *cfg_dir*, or None."""
    if not project:
        return None
    from quam_state_manager.core import agent_setup
    ov = (agent_setup.load_record(inst).get("overrides") or {}).get(project)
    if not isinstance(ov, dict) or not ov.get("folder"):
        return None
    if _norm(ov.get("config_dir")) != _norm(cfg_dir):
        return None
    return ov


def set_override(inst, project: str, cfg_dir: str, folder: str | None, *, by: str = "") -> dict:
    """Store (or, with ``folder=None``, clear) the override for *project*."""
    from quam_state_manager.core import agent_setup
    rec = agent_setup.load_record(inst)
    ovs = dict(rec.get("overrides") or {})
    if folder:
        ovs[project] = {"folder": str(folder), "config_dir": str(cfg_dir), "at": time.time(), "by": by}
    else:
        ovs.pop(project, None)
    agent_setup.save_record(inst, {"overrides": ovs})
    with _LOCK:
        _MEMO.clear()
    return ovs


def resolve(inst, project: str | None, *, legacy: str | None = None, cfg_dir=None) -> dict[str, Any]:
    """Resolve the calibrations folder. ``legacy`` = the scheduler setting's
    raw value (the caller reads it; this module never writes it)."""
    from quam_state_manager.core import agent_setup, qualibrate_config as qc
    legacy = str(legacy or "").strip() or None
    legacy_ok = legacy if _is_dir(legacy) else None
    if not project:
        return {"folder": legacy_ok, "source": "legacy" if legacy_ok else None, "project": None,
                "reason": None if legacy_ok else ("missing" if legacy else "no_project"),
                "legacy": legacy, "legacy_ok": bool(legacy_ok), "override": None, "entry": None, "files": None}
    try:
        cdir = str(Path(cfg_dir) if cfg_dir else qc._config_dir())
    except Exception:  # noqa: BLE001
        cdir = ""
    key = (str(inst), project, cdir, legacy,
           _stat_key(os.path.join(cdir, "config.toml")),
           _stat_key(os.path.join(cdir, "projects", project, "config.toml")),
           _stat_key(agent_setup.record_path(inst)))
    with _LOCK:
        hit = _MEMO.get(key)
    if hit is not None and _is_dir(hit.get("folder")) == bool(hit.get("folder")):
        return dict(hit)
    out = _resolve_uncached(inst, project, cdir, legacy, legacy_ok)
    with _LOCK:
        if len(_MEMO) > 64:
            _MEMO.clear()
        _MEMO[key] = out
    return dict(out)


def _resolve_uncached(inst, project, cdir, legacy, legacy_ok) -> dict[str, Any]:
    from quam_state_manager.core import qualibrate_config as qc
    base = {"project": project, "config_dir": cdir, "legacy": legacy, "legacy_ok": bool(legacy_ok)}
    try:
        paths = qc.project_paths(project, Path(cdir) if cdir else None)
    except Exception:  # noqa: BLE001 -- an unreadable config is a reason, never a crash
        paths = None
    entry = (paths or {}).get("calibration_library")
    files = (paths or {}).get("files")
    ov = load_override(inst, project, cdir)
    if ov:
        f = ov["folder"]
        return {**base, "folder": f if _is_dir(f) else None, "source": "override",
                "reason": None if _is_dir(f) else "override_missing", "override": ov,
                "entry": entry, "files": files}
    if paths is None or not (files or {}).get("root", {}).get("read") and not (files or {}).get("project", {}).get("read"):
        reason = "no_config"
    elif entry["source"] in ("default", "empty"):
        reason = "unset" if entry["source"] == "default" else "empty"
    elif not entry.get("native"):
        reason = "unmapped"
    elif not entry.get("is_dir"):
        reason = "missing"
    else:
        reason = None
    return {**base, "folder": entry["native"] if reason is None else None,
            "source": "project" if reason is None else None, "reason": reason, "override": None,
            "entry": entry, "files": files}


def folder(inst, project: str | None, *, legacy: str | None = None) -> str | None:
    return resolve(inst, project, legacy=legacy).get("folder")
