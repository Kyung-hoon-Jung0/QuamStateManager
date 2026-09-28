"""The Python environment each QUAlibrate project runs with (w9/labwarm).

SM has ONE selected environment (``config_generator.get_selected_env``): the
lab-code worker, the chip's class probe, the generated-config warm and the
Generate Config default all read it. A lab works in several projects, often
with a different env each, and used to re-pick it by hand in Generate Config
-- and until it did, nothing that needs the lab's own code could run.

This module is the per-project MEMORY in front of that one setting:

* ``instance/project_envs.json`` -- ``{"projects": {name: {"python", "at",
  "how"}}, "last_used": {"python", "project", "at"}}``, keyed by the
  QUAlibrate project NAME, the key ``project_dataset_roots.json`` and the
  project scope (``ctx["qualibrate_project"]``) already use;
* a project SYNCED with an env (the user confirmed or changed it) opens with
  that env selected, and nothing is re-discovered or re-probed for it;
* a project never synced is offered the env used most recently by any
  project -- or, before any project has one, the env already selected in
  Generate Config -- marked "suggested", and remembered only when the user
  confirms or changes it.

Opening a project makes its env (remembered or suggested) THE selected env;
the caller (routes) runs what a selection means. Nothing here spawns: the
display label is derived from the path, existence is one stat.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from quam_state_manager.core import safe_io

FILENAME = "project_envs.json"

_LOCK = threading.Lock()
#: one read-modify-write at a time (two windows confirming at once)
_WRITE_LOCK = threading.Lock()
# path -> ((mtime_ns, size), data): the memo the per-render badge reads
_MEMO: dict[str, tuple] = {}


def _file(inst) -> Path:
    return Path(inst) / FILENAME


def _stat(p: Path):
    try:
        st = p.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def load(inst) -> dict:
    """The memory, normalized; ``{"projects": {}, "last_used": None}`` when
    absent or unreadable (a corrupt memo never breaks a page)."""
    p = _file(inst)
    key = str(p)
    sig = _stat(p)
    with _LOCK:
        hit = _MEMO.get(key)
        if hit is not None and hit[0] == sig:
            return _copy(hit[1])
    data = {"projects": {}, "last_used": None}
    if sig is not None:
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = None
        if isinstance(raw, dict):
            projs = raw.get("projects")
            if isinstance(projs, dict):
                for name, rec in projs.items():
                    if (isinstance(name, str) and isinstance(rec, dict)
                            and isinstance(rec.get("python"), str) and rec["python"]):
                        data["projects"][name] = {
                            "python": rec["python"],
                            "at": float(rec.get("at") or 0.0),
                            "how": str(rec.get("how") or "confirmed")}
            lu = raw.get("last_used")
            if isinstance(lu, dict) and isinstance(lu.get("python"), str) and lu["python"]:
                data["last_used"] = {"python": lu["python"],
                                     "project": lu.get("project"),
                                     "at": float(lu.get("at") or 0.0)}
    with _LOCK:
        _MEMO[key] = (sig, data)
    return _copy(data)


def _copy(data: dict) -> dict:
    return {"projects": {k: dict(v) for k, v in data["projects"].items()},
            "last_used": dict(data["last_used"]) if data["last_used"] else None}


def _save(inst, data: dict) -> None:
    safe_io.atomic_write_json(_file(inst), data)
    with _LOCK:
        _MEMO.pop(str(_file(inst)), None)


def remembered(inst, project: str | None) -> str | None:
    """The env *project* was synced with, or None."""
    if not project:
        return None
    rec = load(inst)["projects"].get(project)
    return rec["python"] if rec else None


def remember(inst, project: str, python: str, how: str = "confirmed") -> None:
    """The user confirmed (``how="confirmed"``) or changed (``"changed"``)
    *project*'s env: remember it, and as the most recently used one."""
    if not project or not python:
        raise ValueError("project and python are required")
    with _WRITE_LOCK:
        data = load(inst)
        now = time.time()
        data["projects"][project] = {"python": python, "at": now, "how": how}
        data["last_used"] = {"python": python, "project": project, "at": now}
        _save(inst, data)


def mark_used(inst, project: str | None, python: str) -> None:
    """*python* was just made the selected env for *project* (an open): it
    becomes the most recently used env -- the suggestion for a project never
    synced. Writes only when that changes something."""
    if not python:
        return
    with _WRITE_LOCK:
        data = load(inst)
        lu = data.get("last_used") or {}
        if lu.get("python") == python and lu.get("project") == project:
            return
        data["last_used"] = {"python": python, "project": project, "at": time.time()}
        _save(inst, data)


def suggestion(inst, fallback: str | None = None) -> str | None:
    """The env a project never synced is offered: the one used most recently
    by any project, else the most recently synced one, else *fallback* (the
    env already selected in Generate Config)."""
    data = load(inst)
    lu = data.get("last_used")
    if lu and lu.get("python"):
        return lu["python"]
    projs = sorted(data["projects"].values(), key=lambda r: r.get("at") or 0.0)
    if projs:
        return projs[-1]["python"]
    return fallback or None


def label(python: str | None) -> str:
    """A short name for an interpreter path, from the path alone: a conda
    env's folder (``envs/KRISS_CZ/python.exe`` -> ``KRISS_CZ``), a venv's
    project (``proj/.venv/Scripts/python.exe`` -> ``proj (.venv)``)."""
    if not python:
        return ""
    p = Path(python)
    parts = [x for x in p.parts]
    low = [x.lower() for x in parts]
    # <env>/Scripts/python.exe | <env>/bin/python | <env>/python.exe
    env_dir = p.parent
    if env_dir.name.lower() in ("scripts", "bin"):
        env_dir = env_dir.parent
    if env_dir.name.lower() == ".venv":
        return f"{env_dir.parent.name} (.venv)"
    if "envs" in low and env_dir.parent.name.lower() == "envs":
        return env_dir.name
    return env_dir.name or str(p)


def view(inst, project: str | None, selected: str | None) -> dict:
    """What a project card / the env badge shows for *project*:
    ``{"python", "label", "state", "exists"}`` with state ``remembered`` (the
    project was synced), ``suggested`` (never synced; the offered env),
    ``none`` (nothing to offer)."""
    rem = remembered(inst, project)
    if rem:
        python, state = rem, "remembered"
    else:
        python = suggestion(inst, fallback=selected)
        state = "suggested" if python else "none"
    return {"python": python, "label": label(python), "state": state,
            "exists": bool(python) and os.path.isfile(python)}
