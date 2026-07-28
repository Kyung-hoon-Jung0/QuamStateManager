"""Resolve where Qualibrate actually writes the live QUAM state.

The ``/workbench`` co-display shell wants to nudge the State-Manager pane when
Qualibrate applies a fit (accept-all / a per-value checkbox), which writes the
live ``state.json`` + ``wiring.json``. To watch the right files we must resolve
the path **the way Qualibrate does** — which is NOT just the global config's
``quam.state_path`` (that value is a stale default; verified 2026-06-06 it
pointed at an old folder while writes went elsewhere).

Resolution order (first hit wins):
  1. ``QUALIBRATE_STATE_PATH`` env — SM's OWN explicit override (handy when
     the State Manager runs somewhere the config's native paths don't
     resolve, e.g. a WSL dev box reading a Windows config). Setting SM's
     dedicated variable is always deliberate, so it stays first.
  2. The **active project's EFFECTIVE config** — the same global⊕overlay
     deep-merge qualibrate performs: ``<cfg>/config.toml`` →
     ``[qualibrate] project`` → ``effective_config(project)`` →
     ``[quam] state_path``. A pure-inheritor overlay takes the GLOBAL
     value (real configs keep state_path global-side); an explicit
     ``state_path = ""`` means "this project has none" (resolves None,
     no fallback). Re-read LIVE on every poll, so switching the active
     project in qualibrate propagates immediately.
  3. ``QUAM_STATE_PATH`` env — quam's RUNNER variable. Demoted below the
     config (r6b fix): calibration shells export it routinely, so an SM
     launched from one inherits a FROZEN snapshot and kept showing the
     previous project's folder forever after a project switch — while the
     experiment correctly wrote to the new project. It remains the
     fallback for setups that run everything by env with no config.
  4. The global ``<cfg>/config.toml`` → ``[quam] state_path`` (the stale-ish
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


# SM-side config-location override (docs/63 §B): chosen in the UI when the
# default/env locations hold no config (e.g. qualibrate lives in a WSL distro
# while SM runs native Windows). Set from instance/qualibrate_location.json at
# app creation and by the /qualibrate/use-location route. Deliberately BELOW
# both env vars: an environment variable is deployment-level intent (and the
# test suite's isolation relies on it winning).
_dir_override: Path | None = None


def set_dir_override(value: str | Path | None) -> None:
    """Install (or clear, with None) the UI-chosen config directory."""
    global _dir_override
    _dir_override = Path(value) if value else None


def config_source() -> dict[str, Any]:
    """Where the config dir comes from: ``{"dir": str, "source":
    "env" | "override" | "default"}`` — surfaced in the UI so a user can see
    WHY a given tree is (not) being read."""
    if os.environ.get("QUALIBRATE_CONFIG_FILE") or os.environ.get(
            "QUALIBRATE_CONFIG_DIR"):
        return {"dir": str(_config_dir()), "source": "env"}
    if _dir_override is not None:
        return {"dir": str(_config_dir()), "source": "override"}
    return {"dir": str(_config_dir()), "source": "default"}


def _config_dir() -> Path:
    """The qualibrate config ROOT directory.

    Honors QUAlibrate's own variable first: ``QUALIBRATE_CONFIG_FILE`` is
    dir-OR-file (qualibrate_config/vars.py — a file value points at the
    config.toml itself). SM's historical ``QUALIBRATE_CONFIG_DIR`` stays as a
    legacy alias — before this fix a user who redirected qualibrate via its
    official variable was invisible to SM (docs/55). Below the env vars sits
    the UI-chosen override (docs/63 §B), then the ``~/.qualibrate`` default.
    """
    official = os.environ.get("QUALIBRATE_CONFIG_FILE")
    if official:
        p = Path(official)
        # dir-or-file semantics: a file path means "this IS config.toml"
        return p.parent if (p.suffix == ".toml" or p.is_file()) else p
    override = os.environ.get("QUALIBRATE_CONFIG_DIR")
    if override:
        return Path(override)
    if _dir_override is not None:
        return _dir_override
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
    # SM's OWN override variable — setting it is always deliberate.
    own = os.environ.get("QUALIBRATE_STATE_PATH")
    if own:
        return Path(own)

    cfg_dir = _config_dir()
    global_cfg = _load_toml(cfg_dir / "config.toml")

    # The ACTIVE project's EFFECTIVE config is live truth — the same
    # global⊕overlay deep-merge qualibrate itself performs (docs/55): a
    # pure-inheritor overlay takes the GLOBAL [quam] state_path, and
    # `state_path = ""` is an explicit "this project has none" override.
    # Re-read every call, so a project switch propagates to the watch/banner
    # instantly. (The first r6b fix walked only the overlay, missing the
    # inheritance case — the real user config keeps state_path global-side.)
    project = (global_cfg.get("qualibrate") or {}).get("project")
    if project:
        eff = effective_config(str(project), root_cfg=global_cfg, cfg_dir=cfg_dir)
        quam_cfg = eff.get("quam") or {}
        if "state_path" in quam_cfg:
            sp = quam_cfg.get("state_path")
            if isinstance(sp, str) and sp:
                return Path(sp)
            return None            # explicit "" — the project truly has none

    # QUAM_STATE_PATH (quam's serialiser variable, JSONSerialiser._get_state_path)
    # sits BELOW the effective config: it configures the RUNNER process, and an
    # SM launched from a calibration shell inherits a frozen snapshot that goes
    # stale on every project switch (the r6b stale-banner bug). Fallback only.
    env = os.environ.get("QUAM_STATE_PATH")
    if env:
        return Path(env)

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
_WSL_MNT_RE = _re.compile(r"^/mnt/([A-Za-z])(?:/|$)")
# \\wsl.localhost\<distro> (or the older \\wsl$\<distro>), either slash kind.
_WSL_UNC_RE = _re.compile(r"^[\\/]{2}(?:wsl\.localhost|wsl\$)[\\/]([^\\/]+)",
                          _re.IGNORECASE)

# Config schema generations this reader's semantics are pinned to (docs/55
# version gate — writes elsewhere must degrade to read-only on mismatch).
SUPPORTED_QUALIBRATE_VERSION = 5
SUPPORTED_QUAM_VERSION = 3


def native_path(raw: Any) -> Path | None:
    """A config path value in THIS process's dialect, or ``None``.

    Config values are written by qualibrate on Windows (``D:\\…``, sometimes
    lowercase ``d:``); when SM runs under WSL those must map to ``/mnt/d/…``
    or every existence badge lies. The INVERSE also holds (docs/63): a config
    written from WSL carries ``/mnt/d/…`` values, and SM on native Windows
    must map them to ``D:\\…`` or the project lens' reverse path-matching
    false-negatives on every load. When the config itself is read from a WSL
    distro share (``\\\\wsl.localhost\\<distro>\\…``, docs/63 §B), POSIX
    values OUTSIDE ``/mnt`` (``/home/u/…``) live on that distro's own
    filesystem and map to the same share. Native-dialect values pass through.
    Empty string → None (the explicit-override empty state_path)."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    return _to_native(raw, os.name, wsl_root=_wsl_root_of(_config_dir()))


def _wsl_root_of(path: Path | str) -> str | None:
    """``\\\\wsl.localhost\\<distro>`` (verbatim prefix spelling) when *path*
    is on a WSL distro share, else None. Pure string work — no I/O."""
    m = _WSL_UNC_RE.match(str(path))
    return m.group(0) if m else None


def _to_native(raw: str, os_name: str, wsl_root: str | None = None) -> Path:
    """Dialect mapping with the host OS as an argument — pure and therefore
    testable for BOTH directions on any host (patching ``os.name`` breaks
    pathlib's own class selection). *wsl_root* (Windows host only) anchors
    non-``/mnt`` POSIX values onto the distro share the config came from —
    ``/mnt/<x>`` still prefers the direct drive (same bytes, faster I/O)."""
    m = _WIN_DRIVE_RE.match(raw)
    if m and os_name != "nt":
        drive = m.group(1).lower()
        rest = raw[m.end():].replace("\\", "/")
        return Path(f"/mnt/{drive}/{rest}")
    m = _WSL_MNT_RE.match(raw)
    if m and os_name == "nt":
        drive = m.group(1).upper()
        rest = raw[m.end():].replace("/", "\\")
        return Path(f"{drive}:\\{rest}")
    if wsl_root and os_name == "nt" and raw.startswith("/"):
        rest = raw.lstrip("/").replace("/", "\\")
        return Path(wsl_root + "\\" + rest)
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
    a torn file. Polls keep the plain tolerant read; pages get one retry.

    A genuinely EMPTY file is NOT a torn write: a 0-byte project overlay is a
    supported "pure inheritor" (docs/55), and retrying it cost a 150 ms sleep
    per empty overlay per listing — the studied real config has four of them,
    which would put ~0.6 s of pure sleep on every listing (and, since the
    project lens, on the chip-activation path). Skip the retry when the file
    is empty; a mid-truncate root config is also momentarily 0-byte, but the
    next poll/render re-reads it — same tolerance the plain read already has."""
    out = _load_toml(path)
    if out or not path.exists():
        return out
    try:
        if path.stat().st_size == 0:
            return out
    except OSError:
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
           else "sm-override" if _dir_override is not None
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
    # Rebuilds assign ONE immutable entry tuple and afterwards read only
    # locals — concurrent first hits (pywebview window + workbench iframe
    # both landing on GET /) may waste a rebuild but can never observe a
    # half-built cache. The previous clear()/update()/read-back sequence
    # could KeyError between two racing threads.
    c = _tray_cache
    key = (str(cfg_dir), root_m)
    entry = c.get("entry")
    if entry is None or entry[0] != key:
        root_cfg = _load_toml(root_path)
        active_v = (root_cfg.get("qualibrate") or {}).get("project")
        entry = (key, str(active_v) if active_v else None, root_cfg, {})
        c["entry"] = entry
    _, active, root_cfg, state_memo = entry
    if not active:
        return {"config_exists": True, "active": None,
                "state_raw": None, "state_native": None, "state_exists": False}
    overlay_path = cfg_dir / "projects" / active / "config.toml"
    try:
        overlay_m = overlay_path.stat().st_mtime_ns
    except OSError:
        overlay_m = -1
    st = state_memo.get(overlay_m)
    if st is None:
        overlay = _load_toml(overlay_path) if overlay_m >= 0 else {}
        eff = _deep_merge(root_cfg, overlay)
        raw = (eff.get("quam") or {}).get("state_path")
        native = native_path(raw)
        st = {"raw": raw, "native": str(native) if native else None}
        state_memo.clear()          # one overlay state per entry, like before
        state_memo[overlay_m] = st
    return {
        "config_exists": True,
        "active": active,
        "state_raw": st["raw"],
        "state_native": st["native"],
        "state_exists": bool(st["native"] and Path(st["native"]).is_dir()),
    }


# Stat-keyed cache for the project↔state_path reverse index (docs/63 project
# lens): the web layer reverse-matches every chip activation against it, so it
# must cost a handful of os.stat calls steady-state — TOML parsing happens
# only when the root or any overlay actually changed (or a project dir
# appeared/vanished). Same discipline as _tray_cache. NEVER calls lint()
# (which stats/iterdirs arbitrary configured paths, potentially dead mounts).
_state_index_cache: dict[str, Any] = {}


def project_state_paths(cfg_dir: Path | None = None) -> dict[str, Any]:
    """``{"active": str|None, "projects": [(name, native_state_path|None)]}``.

    The minimal payload the project lens needs to reverse-match a loaded
    folder onto a project: every project's EFFECTIVE ``[quam].state_path``
    (root deep-merged with its overlay; explicit ``""`` override → None) in
    this process's path dialect. READ-ONLY, never raises."""
    cfg_dir = cfg_dir or _config_dir()
    root_path = cfg_dir / "config.toml"
    try:
        root_m = root_path.stat().st_mtime_ns
    except OSError:
        return {"active": None, "projects": []}
    proj_dir = cfg_dir / "projects"
    try:
        names = sorted(d.name for d in proj_dir.iterdir() if d.is_dir())
    except OSError:
        names = []
    key_parts: list[Any] = [str(cfg_dir), root_m]
    for n in names:
        try:
            key_parts.append((n, (proj_dir / n / "config.toml").stat().st_mtime_ns))
        except OSError:
            key_parts.append((n, -1))
    key = tuple(key_parts)
    cached = _state_index_cache.get("entry")
    if cached is not None and cached[0] == key:
        return cached[1]

    root_cfg = _load_toml_retry(root_path)
    active = (root_cfg.get("qualibrate") or {}).get("project")
    projects: list[tuple[str, str | None]] = []
    for n in names:
        overlay = _load_toml_retry(proj_dir / n / "config.toml")
        eff = _deep_merge(root_cfg, overlay)
        native = native_path((eff.get("quam") or {}).get("state_path"))
        projects.append((n, str(native) if native else None))
    result = {"active": str(active) if active else None, "projects": projects}
    _state_index_cache["entry"] = (key, result)
    return result


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

    # state_path grouping uses filesystem identity (fs_key), not raw string
    # equality: the scope engine's ambiguity rule (_project_for_path) treats
    # case/spelling variants of ONE folder as the same chip via same_folder,
    # so the Doctor hint that explains its None-abstention must cluster the
    # same way. Deferred import — path_match pulls in history; lint only runs
    # on Doctor renders. (storage_shared keeps its historical raw grouping.)
    from quam_state_manager.core import path_match

    storage_users: dict[str, list[str]] = {}
    state_users: dict[str, list[str]] = {}
    state_locs: dict[str, str] = {}     # fs_key → first spelling, for display
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
            # sibling suggestion: existing dirs next to the dangling target.
            # ValueError: scandir raises it (not OSError) on an embedded NUL
            # — legal in a TOML basic string, and lint must never 500 the
            # landing over an adversarial config value.
            try:
                parent = Path(st["native"]).parent
                sibs = [d.name for d in parent.iterdir() if d.is_dir()][:4]
                if sibs:
                    finding["suggestion"] = (
                        "existing sibling folder(s): " + ", ".join(sibs))
            except (OSError, ValueError):
                pass
            findings.append(finding)
        if p["calibration_library"]["native"] and not p["calibration_library"]["exists"]:
            findings.append({"severity": "warning", "project": name,
                             "code": "calibration_library_dangling",
                             "message": ("calibration_library folder does not "
                                         f"exist: {p['calibration_library']['raw']}")})
        if p["storage"]["native"]:
            storage_users.setdefault(p["storage"]["native"], []).append(name)
        if st["native"]:
            k = path_match.fs_key(st["native"])
            state_users.setdefault(k, []).append(name)
            state_locs.setdefault(k, st["native"])

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
    # Mirrors storage_shared for state_path (docs/63): inheritance makes it
    # structurally easy for several projects to resolve to ONE chip folder,
    # and SM's project lens then can't auto-attribute the chip to a single
    # project on a plain folder load (an explicit Open pins the choice).
    for k, users in state_users.items():
        if len(users) > 1:
            findings.append({
                "severity": "info", "project": None,
                "code": "state_path_shared",
                "message": (f"{len(users)} projects share one state_path "
                            f"({state_locs[k]}): " + ", ".join(sorted(users)) +
                            " — a plain folder load can't tell which project "
                            "is meant; open the project explicitly to pin it"),
            })
    return findings
