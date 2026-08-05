"""Who else is running? (docs/80 Part 1)

Two State Manager windows on one machine share ONE instance directory — the
same working copies, the same history store, the same scheduler queue — but
nothing in the tree ever knew the other process existed. Every lock is a
``threading.RLock``, i.e. in-process only, so a second window could reconcile
away the first window's running experiment, or quietly overwrite the same
chip's edits, with no surface anywhere saying two windows were involved.

This module is the missing fact: a tiny registry of live State Manager
processes and what each currently holds.

Two design choices worth keeping:

**One file per process** (``instance/instances/<pid>.json``), not one shared
JSON. A shared document would need a read-modify-write from every process,
which is precisely the lost-update pattern this whole document exists to
close — two windows would delete each other's entries. Per-process files have
no write contention at all: each process only ever writes its own, reading is
a glob, and cleanup is an unlink.

**Liveness by PID probe, not heartbeat.** A heartbeat means a timer, and the
docs/78 constraint stands: no new background pollers. A PID probe is exact at
the moment it matters (when someone asks "is that other window still there?")
and costs one syscall. Its failure modes are bounded and known: a reused PID
reads as alive (a warning that should not be there — harmless), and a probe
that errors reads as dead (no worse than having no registry at all).

Nothing here is ever fatal. A registry that cannot be written must not stop
the app from starting, and a registry that cannot be read must not stop a
page from rendering — the feature it powers is a warning, not a gate.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from quam_state_manager.core import path_match, safe_io

logger = logging.getLogger(__name__)

_DIRNAME = "instances"

# A registry entry older than this with no live PID is junk from a crashed
# process; we drop it on sight. Generous because the entry is refreshed only
# when something actually changes (a chip opens, a root is added), not on a
# timer -- an idle window can legitimately go untouched for a whole day.
_STALE_AFTER_S = 7 * 24 * 3600


# ----------------------------------------------------------------------
# Liveness
# ----------------------------------------------------------------------

def pid_alive(pid) -> bool:
    """Best-effort EXISTENCE probe for *pid* — never kills, only checks.

    Bounded, safe failure modes: a false 'alive' (PID reused) is a warning
    that need not have been shown; a false 'dead' leaves us exactly where we
    were before this module existed.

    (Lives here rather than in :mod:`scheduler` so both the registry and the
    scheduler's orphan reconciliation can use it without an import cycle;
    ``scheduler._pid_alive`` remains as an alias.)
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h:
                return False
            code = ctypes.c_ulong()
            ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
            k32.CloseHandle(h)
            return bool(ok) and code.value == STILL_ACTIVE
        except Exception:   # noqa: BLE001 — probe failure ⇒ treat as gone (safe default)
            return False
    # POSIX: signal 0 is the classic existence check.
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True          # exists but owned by another user → still alive
    except OSError:
        return False


# ----------------------------------------------------------------------
# Records
# ----------------------------------------------------------------------

@dataclass
class Peer:
    """Another live State Manager process and what it is holding."""

    pid: int
    port: int | None = None
    started_utc: str = ""
    updated_utc: str = ""
    chip_path: str = ""
    chip_fs_key: str = ""
    chip_name: str = ""
    project: str = ""
    roots: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        """How this peer is named to the user — port first, because that is
        what distinguishes two windows on screen."""
        if self.port:
            return f"port {self.port} · PID {self.pid}"
        return f"PID {self.pid}"

    def to_dict(self) -> dict:
        return {"pid": self.pid, "port": self.port, "label": self.label,
                "started_utc": self.started_utc, "updated_utc": self.updated_utc,
                "chip_path": self.chip_path, "chip_name": self.chip_name,
                "project": self.project, "roots": list(self.roots)}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dir(instance_path) -> Path:
    return Path(instance_path) / _DIRNAME


def _path_for(instance_path, pid: int) -> Path:
    return _dir(instance_path) / f"{pid}.json"


def _fs_key(p) -> str:
    if not p:
        return ""
    try:
        return path_match.fs_key(p) or ""
    except Exception:       # noqa: BLE001 — a weird path must not break the registry
        return ""


# ----------------------------------------------------------------------
# Writing our own entry
# ----------------------------------------------------------------------

def register(instance_path, *, port: int | None = None) -> None:
    """Record this process. Idempotent; safe to call more than once."""
    try:
        _dir(instance_path).mkdir(parents=True, exist_ok=True)
        path = _path_for(instance_path, os.getpid())
        existing = safe_io.scan_json(path) if path.exists() else None
        record = {
            "pid": os.getpid(),
            "port": port if port is not None else (existing or {}).get("port"),
            "started_utc": (existing or {}).get("started_utc") or _now_iso(),
            "updated_utc": _now_iso(),
            "chip_path": (existing or {}).get("chip_path", ""),
            "chip_fs_key": (existing or {}).get("chip_fs_key", ""),
            "chip_name": (existing or {}).get("chip_name", ""),
            "project": (existing or {}).get("project", ""),
            "roots": list((existing or {}).get("roots") or []),
        }
        safe_io.atomic_write_json(path, record)
    except Exception:       # noqa: BLE001 — never block startup on bookkeeping
        logger.debug("instance registry: register failed", exc_info=True)


def update(instance_path, **fields) -> None:
    """Merge *fields* into this process's entry (creating it if needed).

    Accepts ``port``, ``chip_path``, ``chip_name``, ``project``, ``roots``.
    ``chip_fs_key`` is derived from ``chip_path`` so callers never have to
    remember the canonical-identity rule.
    """
    try:
        _dir(instance_path).mkdir(parents=True, exist_ok=True)
        path = _path_for(instance_path, os.getpid())
        record = safe_io.scan_json(path) if path.exists() else None
        if not isinstance(record, dict):
            record = {"pid": os.getpid(), "started_utc": _now_iso(),
                      "port": None, "chip_path": "", "chip_fs_key": "",
                      "chip_name": "", "project": "", "roots": []}
        for key in ("port", "chip_path", "chip_name", "project"):
            if key in fields:
                record[key] = fields[key]
        if "roots" in fields:
            record["roots"] = [str(r) for r in (fields["roots"] or [])]
        if "chip_path" in fields:
            record["chip_fs_key"] = _fs_key(fields["chip_path"])
        record["pid"] = os.getpid()
        record["updated_utc"] = _now_iso()
        safe_io.atomic_write_json(path, record)
    except Exception:       # noqa: BLE001
        logger.debug("instance registry: update failed", exc_info=True)


def deregister(instance_path) -> None:
    """Drop this process's entry. Best-effort — a leftover file is harmless
    because every reader probes the PID anyway."""
    try:
        _path_for(instance_path, os.getpid()).unlink(missing_ok=True)
    except Exception:       # noqa: BLE001
        logger.debug("instance registry: deregister failed", exc_info=True)


# ----------------------------------------------------------------------
# Reading everyone else's
# ----------------------------------------------------------------------

def peers(instance_path, *, include_self: bool = False) -> list[Peer]:
    """Live State Manager processes other than this one, newest first.

    Dead entries are unlinked as we go: the registry is self-cleaning, so a
    crashed window never leaves a permanent phantom in someone's banner.
    """
    out: list[Peer] = []
    me = os.getpid()
    root = _dir(instance_path)
    try:
        if not root.is_dir():
            return []
        entries = sorted(root.glob("*.json"))
    except OSError:
        return []
    for path in entries:
        try:
            pid = int(path.stem)
        except (TypeError, ValueError):
            continue
        if pid == me and not include_self:
            continue
        record = safe_io.scan_json(path)
        if not isinstance(record, dict):
            _drop(path)
            continue
        if pid != me and not pid_alive(pid):
            _drop(path)
            continue
        out.append(Peer(
            pid=pid,
            port=record.get("port"),
            started_utc=record.get("started_utc", ""),
            updated_utc=record.get("updated_utc", ""),
            chip_path=record.get("chip_path", "") or "",
            chip_fs_key=record.get("chip_fs_key", "") or "",
            chip_name=record.get("chip_name", "") or "",
            project=record.get("project", "") or "",
            roots=list(record.get("roots") or []),
        ))
    out.sort(key=lambda p: p.updated_utc, reverse=True)
    return out


def _drop(path: Path) -> None:
    try:
        stat = path.stat()
        if time.time() - stat.st_mtime < 5:
            return          # just written by a process starting up; leave it
    except OSError:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.debug("instance registry: could not drop %s", path, exc_info=True)


# ----------------------------------------------------------------------
# Conflicts
# ----------------------------------------------------------------------

def conflicts(instance_path, chip_path=None) -> dict:
    """What about the current chip collides with another live window?

    Returns ``{"same_chip": [Peer], "peers": [Peer]}``.

    Only ONE thing is reported as a conflict: another window holding the SAME
    state path. That is the case that was reproduced losing data — one working
    copy, two in-memory stores, and whichever applies last silently wins.

    A shared *data* folder is deliberately NOT a conflict. Running two
    experiment lines against one chip out of one data folder is a real
    workflow, and the only loss it used to cause (the whole-file tags write)
    was fixed at the source in Part 0. Warning about it would train users to
    ignore the banner that carries the warning that matters.
    """
    live = peers(instance_path)
    key = _fs_key(chip_path)
    same = [p for p in live if key and p.chip_fs_key == key]
    return {"same_chip": same, "peers": live}
