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
    """The qualibrate config ROOT directory.

    Honors QUAlibrate's own variable first: ``QUALIBRATE_CONFIG_FILE`` is
    dir-OR-file (qualibrate_config/vars.py — a file value points at the
    config.toml itself). SM's historical ``QUALIBRATE_CONFIG_DIR`` stays as a
    legacy alias — before this fix a user who redirected qualibrate via its
    official variable was invisible to SM (docs/55).
    """
    official = os.environ.get("QUALIBRATE_CONFIG_FILE")
    if official:
        p = Path(official)
        # dir-or-file semantics: a file path means "this IS config.toml"
        return p.parent if (p.suffix == ".toml" or p.is_file()) else p
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
    # QUAM_STATE_PATH is the variable quam's own serialiser honors
    # (JSONSerialiser._get_state_path); SM's QUALIBRATE_STATE_PATH stays as
    # the legacy alias.
    env = os.environ.get("QUAM_STATE_PATH") or os.environ.get(
        "QUALIBRATE_STATE_PATH")
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


# ---------------------------------------------------------------------------
# Projects browser (docs/55) — READ-ONLY over ~/.qualibrate
# ---------------------------------------------------------------------------
# Merge fidelity mirrors qualibrate_config 0.1.12 (read_config_file →
# recursive_update_dict): the per-project overlay is deep-merged OVER the
# root at read time; a 0-byte overlay is a pure inheritor; an EMPTY-STRING
# state_path is an explicit override (not omission); an overlay can never
# rename the active project; the default storage location is
# user_storage/${#/qualibrate/project} with the template substituted lazily.

import re as _re
import time as _time

_PROJECT_TEMPLATE = "${#/qualibrate/project}"
_WIN_DRIVE_RE = _re.compile(r"^([A-Za-z]):[\\/]")

# Config schema generations this reader's semantics are pinned to (docs/55
# version gate — writes elsewhere must degrade to read-only on mismatch).
SUPPORTED_QUALIBRATE_VERSION = 5
SUPPORTED_QUAM_VERSION = 3


def native_path(raw: Any) -> Path | None:
    """A config path value in THIS process's dialect, or ``None``.

    Config values are written by qualibrate on Windows (``D:\\…``, sometimes
    lowercase ``d:``); when SM runs under WSL those must map to ``/mnt/d/…``
    or every existence badge lies. Native Windows / native Linux values pass
    through. Empty string → None (the explicit-override empty state_path)."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    m = _WIN_DRIVE_RE.match(raw)
    if m and os.name != "nt":
        drive = m.group(1).lower()
        rest = raw[m.end():].replace("\\", "/")
        return Path(f"/mnt/{drive}/{rest}")
    return Path(raw)


def _deep_merge(base: dict, overlay: dict) -> dict:
    """qualibrate's ``recursive_update_dict``: dict-in-dict recursive, scalars
    and lists overridden, new overlay keys allowed. Returns a NEW dict."""
    out = dict(base)
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _load_toml_retry(path: Path) -> dict[str, Any]:
    """User-facing read: one short retry over :func:`_load_toml` — qualibrate's
    root write is non-atomic (truncate-in-place), so a mid-write read can see
    a torn file. Polls keep the plain tolerant read; pages get one retry."""
    out = _load_toml(path)
    if out or not path.exists():
        return out
    _time.sleep(0.15)
    return _load_toml(path)


def effective_config(project: str, *, root_cfg: dict | None = None,
                     cfg_dir: Path | None = None) -> dict[str, Any]:
    """The config qualibrate would resolve if *project* were active.

    root deep-merged with the project overlay, with ``[qualibrate].project``
    forced to *project* (qualibrate force-sets it before merging, so an
    overlay can never rename the active project)."""
    cfg_dir = cfg_dir or _config_dir()
    if root_cfg is None:
        root_cfg = _load_toml_retry(cfg_dir / "config.toml")
    overlay = _load_toml_retry(cfg_dir / "projects" / project / "config.toml")
    merged = _deep_merge(root_cfg, overlay)
    merged.setdefault("qualibrate", {})
    if isinstance(merged["qualibrate"], dict):
        merged["qualibrate"]["project"] = project
    return merged


def _storage_location(effective: dict, project: str,
                      cfg_dir: Path) -> tuple[str | None, str]:
    """``(location_string, source)`` — source ∈ own/inherited/default.

    Substitutes the lazy ``${#/qualibrate/project}`` template and applies
    qualibrate's default ``user_storage/<project>`` when nothing is set."""
    loc = ((effective.get("qualibrate") or {}).get("storage") or {}).get(
        "location")
    if isinstance(loc, str) and loc:
        return loc.replace(_PROJECT_TEMPLATE, project), "config"
    return str(cfg_dir / "user_storage" / project), "default"


def active_project(cfg_dir: Path | None = None) -> str | None:
    """The root config's ``[qualibrate].project``, or None."""
    cfg_dir = cfg_dir or _config_dir()
    root_cfg = _load_toml_retry(cfg_dir / "config.toml")
    project = (root_cfg.get("qualibrate") or {}).get("project")
    return str(project) if project else None


def list_projects(cfg_dir: Path | None = None) -> dict[str, Any]:
    """Everything the Projects sidebar/page needs, in one READ-ONLY pass.

    Returns::

        {"ok": bool, "config_dir": str, "config_exists": bool,
         "active": str|None, "source": "env:..."|"default",
         "versions": {"qualibrate": int|None, "quam": int|None,
                      "supported": bool},
         "projects": [{"name", "active", "overlay_empty",
                       "state_path": {"raw", "native", "exists", "source"},
                       "storage":    {...same...},
                       "calibration_library": {...same...}}, ...]}

    ``source`` per value: "own" (in this project's overlay), "inherited"
    (from the root), "default" (qualibrate's built-in), or "empty" (the
    explicit ``state_path = ""`` override).
    """
    cfg_dir = cfg_dir or _config_dir()
    root_path = cfg_dir / "config.toml"
    root_cfg = _load_toml_retry(root_path)
    active = (root_cfg.get("qualibrate") or {}).get("project")

    src = ("env:QUALIBRATE_CONFIG_FILE" if os.environ.get("QUALIBRATE_CONFIG_FILE")
           else "env:QUALIBRATE_CONFIG_DIR" if os.environ.get("QUALIBRATE_CONFIG_DIR")
           else "default")
    q_ver = (root_cfg.get("qualibrate") or {}).get("version")
    m_ver = (root_cfg.get("quam") or {}).get("version")

    projects_dir = cfg_dir / "projects"
    names: list[str] = []
    try:
        names = sorted(p.name for p in projects_dir.iterdir() if p.is_dir())
    except OSError:
        pass

    def _value(effective: dict, overlay: dict, section: tuple[str, ...],
               key: str) -> tuple[Any, str]:
        node_o: Any = overlay
        for s in section:
            node_o = node_o.get(s) if isinstance(node_o, dict) else None
        own = isinstance(node_o, dict) and key in node_o
        node_e: Any = effective
        for s in section:
            node_e = node_e.get(s) if isinstance(node_e, dict) else None
        val = node_e.get(key) if isinstance(node_e, dict) else None
        if own and val == "":
            return val, "empty"
        return val, ("own" if own else
                     "inherited" if val not in (None, "") else "default")

    out_projects = []
    for name in names:
        overlay_path = projects_dir / name / "config.toml"
        overlay = _load_toml_retry(overlay_path)
        try:
            overlay_empty = overlay_path.stat().st_size == 0
        except OSError:
            overlay_empty = not overlay
        eff = _deep_merge(root_cfg, overlay)

        def _entry(section: tuple[str, ...], key: str) -> dict:
            raw, source = _value(eff, overlay, section, key)
            native = native_path(raw)
            return {
                "raw": raw,
                "native": str(native) if native else None,
                "exists": bool(native and native.exists()),
                "source": source,
            }

        state = _entry(("quam",), "state_path")
        storage = _entry(("qualibrate", "storage"), "location")
        if storage["raw"] in (None, ""):
            loc, _ = _storage_location(eff, name, cfg_dir)
            native = native_path(loc)
            storage = {"raw": loc, "native": str(native) if native else None,
                       "exists": bool(native and native.exists()),
                       "source": "default"}
        elif isinstance(storage["raw"], str) and _PROJECT_TEMPLATE in storage["raw"]:
            loc = storage["raw"].replace(_PROJECT_TEMPLATE, name)
            native = native_path(loc)
            storage = {**storage, "raw": loc,
                       "native": str(native) if native else None,
                       "exists": bool(native and native.exists())}
        calib = _entry(("qualibrate", "calibration_library"), "folder")

        out_projects.append({
            "name": name,
            "active": name == active,
            "overlay_empty": overlay_empty,
            "state_path": state,
            "storage": storage,
            "calibration_library": calib,
        })

    return {
        "ok": bool(root_cfg),
        "config_dir": str(cfg_dir),
        "config_exists": root_path.exists(),
        "active": str(active) if active else None,
        "source": src,
        "versions": {
            "qualibrate": q_ver, "quam": m_ver,
            "supported": (q_ver == SUPPORTED_QUALIBRATE_VERSION
                          and m_ver == SUPPORTED_QUAM_VERSION),
        },
        "projects": out_projects,
    }


# Stat-keyed cache for tray_status: the topbar badge renders on EVERY page /
# tray swap, so re-parsing TOML each time is waste. Keyed on (cfg_dir, root
# mtime_ns) + the active overlay's mtime_ns — reads happen only when a config
# file actually changed; steady-state cost is two os.stat calls.
_tray_cache: dict[str, Any] = {}


def tray_status(cfg_dir: Path | None = None) -> dict[str, Any]:
    """Cheap active-project summary for the topbar badge.

    ``{"config_exists", "active", "state_raw", "state_native",
    "state_exists"}``. READ-ONLY, never raises; existence is re-stat-ed every
    call (a folder can appear/vanish without any config edit)."""
    cfg_dir = cfg_dir or _config_dir()
    root_path = cfg_dir / "config.toml"
    try:
        root_m = root_path.stat().st_mtime_ns
    except OSError:
        return {"config_exists": False, "active": None,
                "state_raw": None, "state_native": None, "state_exists": False}
    c = _tray_cache
    if c.get("key") != (str(cfg_dir), root_m):
        root_cfg = _load_toml(root_path)
        active = (root_cfg.get("qualibrate") or {}).get("project")
        c.clear()
        c.update(key=(str(cfg_dir), root_m),
                 active=str(active) if active else None,
                 root_cfg=root_cfg, overlay_m=None, state=None)
    active = c["active"]
    if not active:
        return {"config_exists": True, "active": None,
                "state_raw": None, "state_native": None, "state_exists": False}
    overlay_path = cfg_dir / "projects" / active / "config.toml"
    try:
        overlay_m = overlay_path.stat().st_mtime_ns
    except OSError:
        overlay_m = -1
    if c.get("state") is None or c.get("overlay_m") != overlay_m:
        overlay = _load_toml(overlay_path) if overlay_m >= 0 else {}
        eff = _deep_merge(c["root_cfg"], overlay)
        raw = (eff.get("quam") or {}).get("state_path")
        native = native_path(raw)
        c["overlay_m"] = overlay_m
        c["state"] = {"raw": raw, "native": str(native) if native else None}
    st = c["state"]
    return {
        "config_exists": True,
        "active": active,
        "state_raw": st["raw"],
        "state_native": st["native"],
        "state_exists": bool(st["native"] and Path(st["native"]).is_dir()),
    }


def lint(listing: dict[str, Any]) -> list[dict[str, Any]]:
    """Doctor findings over a :func:`list_projects` result (pure; no I/O
    beyond what listing already did). Each: {severity, project, code, message,
    suggestion?}. Presents, never fixes (docs/55 — collisions may be
    deliberate)."""
    findings: list[dict[str, Any]] = []
    if not listing.get("config_exists"):
        findings.append({"severity": "error", "project": None,
                         "code": "no_config",
                         "message": f"no config.toml under {listing.get('config_dir')}"})
        return findings
    if not listing.get("ok"):
        findings.append({"severity": "error", "project": None,
                         "code": "unparseable_config",
                         "message": "config.toml could not be parsed (torn "
                                    "write in progress, or corrupt)"})
        return findings
    if not (listing.get("versions") or {}).get("supported", False):
        v = listing.get("versions") or {}
        findings.append({"severity": "warning", "project": None,
                         "code": "version_drift",
                         "message": (f"config versions qualibrate={v.get('qualibrate')} / "
                                     f"quam={v.get('quam')} differ from the supported "
                                     f"{SUPPORTED_QUALIBRATE_VERSION}/{SUPPORTED_QUAM_VERSION} "
                                     "— SM stays read-only for these configs")})

    storage_users: dict[str, list[str]] = {}
    for p in listing.get("projects", []):
        name = p["name"]
        st = p["state_path"]
        is_active = p.get("active")
        if st["source"] == "empty":
            findings.append({"severity": "warning", "project": name,
                             "code": "state_path_empty",
                             "message": "state_path is explicitly empty — "
                                        "quam falls back to the working "
                                        "directory at run time"})
        elif st["native"] and not st["exists"]:
            sev = "error" if is_active else "warning"
            finding = {"severity": sev, "project": name,
                       "code": "state_path_dangling",
                       "message": f"state_path does not exist: {st['raw']}"}
            # sibling suggestion: existing dirs next to the dangling target
            try:
                parent = Path(st["native"]).parent
                sibs = [d.name for d in parent.iterdir() if d.is_dir()][:4]
                if sibs:
                    finding["suggestion"] = (
                        "existing sibling folder(s): " + ", ".join(sibs))
            except OSError:
                pass
            findings.append(finding)
        if p["calibration_library"]["native"] and not p["calibration_library"]["exists"]:
            findings.append({"severity": "warning", "project": name,
                             "code": "calibration_library_dangling",
                             "message": ("calibration_library folder does not "
                                         f"exist: {p['calibration_library']['raw']}")})
        if p["storage"]["native"]:
            storage_users.setdefault(p["storage"]["native"], []).append(name)

    for loc, users in storage_users.items():
        if len(users) > 1:
            findings.append({
                "severity": "info", "project": None,
                "code": "storage_shared",
                "message": (f"{len(users)} projects share one dataset root "
                            f"({loc}): " + ", ".join(sorted(users)) +
                            " — runs from different campaigns land in the "
                            "same tree (may be deliberate)"),
            })
    return findings
