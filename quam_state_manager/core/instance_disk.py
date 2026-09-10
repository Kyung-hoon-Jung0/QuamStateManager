"""How much disk SM's own instance folder is using, and what of it is reclaimable.

Customer, 2026-09-11, after a machine-wide temp audit found 28.79 GB under one
scratch folder:

  "우리 SM이 엄청나게 캐시를 계속 쌓아두나봐. 이거 자동으로 정리하게 하거나,
   최소한 유저에게 일정 용량되면(20GB정도?) 알려줘서 삭제하든 옮기든 알려주자."

MEASURED first, because the answer changed what to build. That 28.79 GB was
browser-automation Chrome profiles from the assistant's own verification runs,
not SM: the live instance on the same machine was **0.19 GB** (history 171 MB /
1,309 files, working_state 15 MB, workspace_cache 7 MB).

But the concern is right even though the diagnosis was not. Nothing in SM ever
deletes a snapshot: ``DEFAULT_MAX_SNAPSHOTS`` is 100,000, so ``_prune`` never
fires in practice, and docs/143's aging audit explicitly left snapshot disk
copies alone ("disk, not speed"). Measured growth on this machine's busiest
chip: 1,309 snapshot files in 3 days -- about **56 MB a day**, which is ~20 GB a
year for one chip that is being calibrated hard.

So this module measures, and says so before the disk does. It does NOT delete
anything on its own: a snapshot is a person's record of what their chip was, and
the one thing worse than a full disk is a tool that quietly threw away the
version you needed. The reclaimable list names only what SM can genuinely
rebuild.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

# Directories SM can rebuild from scratch. Deleting one costs the next open some
# time and nothing else -- each is documented in its own module as an
# accelerator, never a source of truth.
REBUILDABLE = {
    "workspace_cache": "Folder listings — rebuilt by the next scan (docs/142).",
    "story_cache": "Per-run story cards — rebuilt on demand.",
    "state_schema_cache": "Env class schemas — re-probed per environment.",
    "capabilities_cache": "Env capability manifests — re-probed per environment.",
}

# Directories that are the USER'S RECORD. Never offered as a one-click delete.
PRECIOUS = {
    "history": "Chip snapshots — your version history. Prune from State History.",
    "working_state": "Unapplied edits per chip. Losing one loses work.",
    "journal": "The calibration log.",
    "agent_events": "What the agent did, per day.",
}

DEFAULT_LIMIT_GB = 20.0
_CACHE_TTL_S = 300.0

_lock = threading.Lock()
_cache: dict[str, Any] = {"key": None, "at": 0.0, "value": None}


def config_path(instance_path) -> Path:
    return Path(instance_path) / "disk_guard.json"


def settings(instance_path) -> dict:
    """``{"limit_gb": float, "muted_until_gb": float|None}``.

    ``muted_until_gb`` is how a person says "yes, I know" without turning the
    warning off for ever: it comes back when the folder grows past THAT, so a
    dismissal cannot hide a problem that is still getting worse.
    """
    try:
        cfg = json.loads(config_path(instance_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
    try:
        limit = float(cfg.get("limit_gb", DEFAULT_LIMIT_GB))
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT_GB
    muted = cfg.get("muted_until_gb")
    try:
        muted = float(muted) if muted is not None else None
    except (TypeError, ValueError):
        muted = None
    return {"limit_gb": max(0.1, limit), "muted_until_gb": muted}


def write_settings(instance_path, **kw) -> dict:
    cfg = settings(instance_path)
    cfg.update({k: v for k, v in kw.items() if k in ("limit_gb", "muted_until_gb")})
    p = config_path(instance_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return settings(instance_path)


def _dir_size(path: Path) -> tuple[int, int]:
    """``(bytes, files)`` under *path*. Unreadable entries are skipped, not
    guessed: a number that silently omits a locked file is still closer to the
    truth than no number, and the caller is told nothing was invented."""
    total = 0
    count = 0
    stack = [str(path)]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            total += e.stat(follow_symlinks=False).st_size
                            count += 1
                    except OSError:
                        continue
        except OSError:
            continue
    return total, count


def measure(instance_path, *, force: bool = False) -> dict:
    """Per-directory sizes of the instance folder, memoized for five minutes.

    A full walk of a busy instance is thousands of stats, so it is NOT run on
    every request. Five minutes is far finer than the thing it watches -- the
    fastest growth measured on a real machine is ~56 MB a day.
    """
    root = Path(instance_path)
    key = str(root)
    now = time.time()
    with _lock:
        c = _cache
        if (not force and c["key"] == key and c["value"] is not None
                and now - c["at"] < _CACHE_TTL_S):
            return c["value"]

    dirs: list[dict] = []
    total = 0
    files = 0
    try:
        entries = sorted(os.scandir(root), key=lambda e: e.name)
    except OSError:
        entries = []
    loose = 0
    loose_n = 0
    for e in entries:
        try:
            if e.is_dir(follow_symlinks=False):
                b, n = _dir_size(Path(e.path))
                total += b
                files += n
                dirs.append({
                    "name": e.name, "bytes": b, "files": n,
                    "rebuildable": e.name in REBUILDABLE,
                    "why": REBUILDABLE.get(e.name) or PRECIOUS.get(e.name) or "",
                })
            elif e.is_file(follow_symlinks=False):
                loose += e.stat(follow_symlinks=False).st_size
                loose_n += 1
        except OSError:
            continue
    total += loose
    files += loose_n
    dirs.sort(key=lambda d: -d["bytes"])

    free = None
    try:
        import shutil as _sh
        free = _sh.disk_usage(str(root)).free
    except Exception:  # noqa: BLE001 -- a missing drive stat must not break the page
        free = None

    out = {
        "root": str(root), "bytes": total, "files": files,
        "gb": round(total / (1024 ** 3), 3),
        "dirs": dirs,
        "loose_bytes": loose,
        "reclaimable_bytes": sum(d["bytes"] for d in dirs if d["rebuildable"]),
        "free_bytes": free,
        "measured_at": now,
    }
    with _lock:
        _cache.update(key=key, at=now, value=out)
    return out


def status(instance_path, *, force: bool = False) -> dict:
    """The measurement plus the verdict a banner renders.

    ``level`` is ``ok`` / ``warn`` / ``over``. The warning starts at 80 % of the
    limit rather than at the limit itself: told at 20.0 GB exactly, a person has
    already been surprised.
    """
    m = measure(instance_path, force=force)
    cfg = settings(instance_path)
    limit_b = cfg["limit_gb"] * (1024 ** 3)
    ratio = (m["bytes"] / limit_b) if limit_b else 0.0
    level = "over" if ratio >= 1.0 else ("warn" if ratio >= 0.8 else "ok")
    muted = cfg["muted_until_gb"]
    if muted is not None and m["gb"] <= muted:
        level = "ok"
    return dict(m, limit_gb=cfg["limit_gb"], muted_until_gb=muted,
                ratio=round(ratio, 3), level=level,
                reclaimable_gb=round(m["reclaimable_bytes"] / (1024 ** 3), 3))


def reclaim(instance_path, names: list[str]) -> dict:
    """Delete the named REBUILDABLE directories. Anything else is refused.

    Refused by name, not filtered silently: a caller asking to delete `history`
    has misunderstood something, and answering "done" would be a lie about the
    thing this module exists to protect.
    """
    root = Path(instance_path)
    freed = 0
    removed: list[str] = []
    refused: list[str] = []
    for name in names or ():
        if name not in REBUILDABLE:
            refused.append(name)
            continue
        p = root / name
        if not p.is_dir():
            continue
        b, _n = _dir_size(p)
        try:
            import shutil as _sh
            _sh.rmtree(p, ignore_errors=True)
        except Exception:  # noqa: BLE001
            continue
        if not p.exists():
            freed += b
            removed.append(name)
    with _lock:
        _cache.update(key=None, at=0.0, value=None)   # the measurement moved
    return {"ok": not refused, "removed": removed, "refused": refused,
            "freed_bytes": freed, "freed_gb": round(freed / (1024 ** 3), 3)}
