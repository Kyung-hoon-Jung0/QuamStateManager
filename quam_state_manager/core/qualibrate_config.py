"""Resolve where Qualibrate actually writes the live QUAM state.

The ``/workbench`` co-display shell wants to nudge the State-Manager pane when
Qualibrate applies a fit (accept-all / a per-value checkbox), which writes the
live ``state.json`` + ``wiring.json``. To watch the right files we must resolve
the path **the way Qualibrate does** — which is NOT just the global config's
``quam.state_path`` (that value is a stale default; verified 2026-06-06 it
pointed at an old folder while writes went elsewhere).

Resolution order (first hit wins):
  1. ``QUALIBRATE_STATE_PATH`` env — a direct override (handy when the State
     Manager runs somewhere the config's native paths don't resolve, e.g. a
     WSL dev box reading a Windows config, or any custom setup).
  2. The **active project's** per-project config:
     ``<cfg>/config.toml`` → ``[qualibrate] project`` → then
     ``<cfg>/projects/<project>/config.toml`` → ``[quam] state_path``.
  3. The global ``<cfg>/config.toml`` → ``[quam] state_path`` (the stale-ish
     fallback).
Where ``<cfg>`` is ``$QUALIBRATE_CONFIG_DIR`` or ``~/.qualibrate``.

Pure Python — no Flask. The web layer calls :func:`live_state_status`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

try:  # Python 3.11+ stdlib
    import tomllib as _toml
except ModuleNotFoundError:  # pragma: no cover - older interpreters
    try:
        import tomli as _toml  # type: ignore
    except ModuleNotFoundError:  # pragma: no cover
        _toml = None  # type: ignore


def _config_dir() -> Path:
    override = os.environ.get("QUALIBRATE_CONFIG_DIR")
    if override:
        return Path(override)
    return Path.home() / ".qualibrate"


def _load_toml(path: Path) -> dict[str, Any]:
    """Parse a TOML file, returning ``{}`` on any problem (missing, unreadable,
    malformed, or no parser available)."""
    if _toml is None:
        return {}
    try:
        with open(path, "rb") as fh:
            return _toml.load(fh)
    except (OSError, ValueError):
        # ValueError covers tomllib's TOMLDecodeError subclass.
        return {}


def resolve_live_state_path() -> Path | None:
    """Resolve the directory Qualibrate writes the live QUAM state into.

    Returns ``None`` if it cannot be determined. See module docstring for the
    resolution order.
    """
    env = os.environ.get("QUALIBRATE_STATE_PATH")
    if env:
        return Path(env)

    cfg_dir = _config_dir()
    global_cfg = _load_toml(cfg_dir / "config.toml")

    project = (global_cfg.get("qualibrate") or {}).get("project")
    if project:
        proj_cfg = _load_toml(cfg_dir / "projects" / str(project) / "config.toml")
        state_path = (proj_cfg.get("quam") or {}).get("state_path")
        if state_path:
            return Path(state_path)

    state_path = (global_cfg.get("quam") or {}).get("state_path")
    if state_path:
        return Path(state_path)
    return None


def _max_json_mtime(directory: Path) -> tuple[float | None, int]:
    """Return ``(max_mtime, file_count)`` across ``*.json`` in *directory*.

    Using the max mtime over all state JSON files makes the watch robust to
    both single-file (``state.json`` + ``wiring.json``) and per-component
    split layouts, and to the non-atomic multi-write Qualibrate does per
    accept. Returns ``(None, 0)`` if the dir is missing or empty.
    """
    try:
        files = list(directory.glob("*.json"))
    except OSError:
        return None, 0
    mtime: float | None = None
    count = 0
    for f in files:
        try:
            m = f.stat().st_mtime
        except OSError:
            continue
        count += 1
        if mtime is None or m > mtime:
            mtime = m
    return mtime, count


def live_state_status() -> dict[str, Any]:
    """Lightweight status for the ``/workbench`` watch poll.

    ``{"ok": bool, "path": str|None, "mtime": float|None, "files": int,
       "reason": str (only when not ok)}``. ``mtime`` is the newest ``*.json``
    mtime in the resolved state dir — the frontend compares it to a baseline to
    detect that Qualibrate wrote the state.
    """
    path = resolve_live_state_path()
    if path is None:
        return {"ok": False, "path": None, "mtime": None, "files": 0,
                "reason": "could not resolve Qualibrate state path from config"}
    if not path.is_dir():
        return {"ok": False, "path": str(path), "mtime": None, "files": 0,
                "reason": "resolved state path does not exist"}
    mtime, count = _max_json_mtime(path)
    return {"ok": True, "path": str(path), "mtime": mtime, "files": count}
