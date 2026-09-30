"""Move SM state out of a conda env's ``var/`` folder into the per-user folder.

docs/232 (customer 2026-09-30). ``default_instance_path()`` treated ANY
``pyproject.toml`` beside the installed package as "a repo checkout" and let
Flask derive ``<sys.prefix>/var/quam_state_manager.web.app-instance``. In the
customer's conda env, ``dash-bootstrap-components`` ships a
``pyproject.toml`` straight into ``site-packages``, so that env kept its own
state -- project env choices, dataset roots, the State History of the chip it
opened, working copies -- apart from every other env's.

The policy is fixed in ``web/app.py`` (only SM's OWN ``pyproject.toml`` means
a checkout). This module carries what such an env already recorded into the
per-user folder, ONCE, without losing anything on either side:

* the per-user folder WINS every conflict (it is where every other env has
  been writing all along);
* nothing in the env folder is deleted or changed except one marker file
  (``MOVED_TO_USER_FOLDER.json``) that makes the move one-shot and names what
  moved -- the env folder stays a complete backup;
* regenerable caches are not moved (schema caches, probe caches, scan caches,
  live-process registrations);
* a history chip folder whose NAME is already taken in the user folder by a
  different chip (both named ``quam_states`` on the customer's machine:
  one chip there, another here) is moved under a free name, and the alias file
  that routes the chip's declared name to its folder follows it;
* a history SQLite index is copied through SQLite's own backup API, so a
  write-ahead log is folded in rather than left behind.

Pure file work, no Flask. ``migrate(legacy, user)`` returns a report dict.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MARKER = "MOVED_TO_USER_FOLDER.json"
# Flask's instance_relative_config name for this package, in both spellings
# the app has been created under (``Flask(__name__)`` in web/app.py; an older
# layout used the package name).
_LEGACY_NAMES = ("quam_state_manager.web.app-instance", "quam_state_manager.web-instance")

# Regenerable, process-bound or superseded -- never carried over.
_SKIP = {
    MARKER, "instances", "workspace_cache", "story_cache",
    "state_schema_cache.json", "state_schema_catalog.json", "state_schema_baselines",
    "config_generator_probe_cache.json", "config_generator_capability_cache.json",
    "scheduler_ast_scan_cache.json",
    "migrated_v1.flag", "migrated_v2.flag", "migrated_v3.flag",
    ".migrate.lock",
}
_SQLITE_SIDE = re.compile(r"\.sqlite-(wal|shm|journal)$")


def legacy_dirs(prefix: str | None = None) -> list[Path]:
    """The env-local instance folders Flask would have derived for *prefix*."""
    base = Path(prefix or sys.prefix) / "var"
    return [base / n for n in _LEGACY_NAMES if (base / n).is_dir()]


def _read_json(p: Path) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_json(p: Path, data: Any) -> None:
    from quam_state_manager.core import safe_io
    safe_io.atomic_write_json(p, data)


def _norm(s: str) -> str:
    return os.path.normcase(os.path.normpath(str(s)))


def _union(first: list, second: list) -> list:
    out, seen = [], set()
    for x in list(first or []) + list(second or []):
        k = _norm(x) if isinstance(x, str) else json.dumps(x, sort_keys=True)
        if k not in seen:
            seen.add(k)
            out.append(x)
    return out


def _copy_sqlite(src: Path, dst: Path) -> None:
    """Copy a (possibly WAL-mode) SQLite db consistently via the backup API."""
    s = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True)
    try:
        d = sqlite3.connect(str(dst))
        try:
            s.backup(d)
        finally:
            d.close()
    finally:
        s.close()


def _copy_tree(src: Path, dst: Path) -> None:
    """copytree, with SQLite files through the backup API and their -wal/-shm
    side files left behind (the backup already folded them in)."""
    dst.mkdir(parents=True, exist_ok=True)
    for child in src.iterdir():
        target = dst / child.name
        if child.is_dir():
            _copy_tree(child, target)
        elif _SQLITE_SIDE.search(child.name):
            continue
        elif child.suffix == ".sqlite":
            _copy_sqlite(child, target)
        else:
            shutil.copy2(child, target)


def _copy_missing(src: Path, dst: Path, rel: str, report: dict) -> None:
    """Every entry of *src* that *dst* does not have yet (recursing into
    folders both have). Nothing in *dst* is overwritten."""
    dst.mkdir(parents=True, exist_ok=True)
    for child in sorted(src.iterdir()):
        target = dst / child.name
        name = f"{rel}/{child.name}"
        if _SQLITE_SIDE.search(child.name):
            continue
        if not target.exists():
            if child.is_dir():
                _copy_tree(child, target)
            elif child.suffix == ".sqlite":
                _copy_sqlite(child, target)
            else:
                shutil.copy2(child, target)
            report["copied"].append(name)
        elif child.is_dir() and target.is_dir():
            _copy_missing(child, target, name, report)
        else:
            report["kept_user"].append(name)


# ------------------------------------------------------------ JSON merges
def _merge_project_envs(old: dict, new: dict) -> dict:
    out = dict(new or {})
    projects = dict((old or {}).get("projects") or {})
    projects.update((new or {}).get("projects") or {})       # user wins
    out["projects"] = projects
    if not out.get("last_used") and (old or {}).get("last_used"):
        out["last_used"] = old["last_used"]
    return out


def _merge_list_map(old: dict, new: dict) -> dict:
    out = {k: list(v) for k, v in (new or {}).items()}
    for k, v in (old or {}).items():
        out[k] = _union(out.get(k, []), v)
    return out


def _merge_dict_user_wins(old: dict, new: dict) -> dict:
    out = dict(old or {})
    out.update(new or {})
    return out


def _merge_session(old: dict, new: dict) -> dict:
    out = dict(old or {})
    out.update({k: v for k, v in (new or {}).items() if v not in (None, "", [])})
    for k in ("recent_quam_state_paths", "workspace_excluded"):
        if (old or {}).get(k) or (new or {}).get(k):
            out[k] = _union((new or {}).get(k) or [], (old or {}).get(k) or [])
    if "recent_quam_state_paths" in out:
        out["recent_quam_state_paths"] = out["recent_quam_state_paths"][:10]
    return out


_JSON_MERGES = {
    "project_envs.json": _merge_project_envs,
    "workspace_roots.json": lambda o, n: _union(n or [], o or []),
    "project_dataset_roots.json": _merge_list_map,
    "project_roots_pending.json": _merge_dict_user_wins,
    "last_session.json": _merge_session,
    "chip_name_prompts.json": _merge_dict_user_wins,
    "env_acks.json": _merge_dict_user_wins,
}


# ---------------------------------------------------------------- history
def _free_name(taken: set[str], wanted: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", wanted).strip("._") or "chip"
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def _migrate_history(src: Path, dst: Path, report: dict) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    old_alias = _read_json(src / "_chip_aliases.json") or {}
    new_alias = _read_json(dst / "_chip_aliases.json") or {"version": 1}
    new_alias.setdefault("names", {})
    new_alias.setdefault("dirs", {})
    taken = {p.name for p in dst.iterdir() if p.is_dir()}
    renamed: dict[str, str] = {}
    for chip in sorted(p for p in src.iterdir() if p.is_dir()):
        if chip.name not in taken:
            _copy_tree(chip, dst / chip.name)
            taken.add(chip.name)
            report["copied"].append(f"history/{chip.name}")
            continue
        # the name is taken -- by THIS chip (an earlier partial copy would
        # have left the marker unwritten) or by a different one: never merge
        # two snapshot sets into one folder; move ours under a free name,
        # preferring the chip's declared display name
        display = ((old_alias.get("dirs") or {}).get(chip.name) or {}).get("display")
        new_name = _free_name(taken, display or chip.name)
        _copy_tree(chip, dst / new_name)
        taken.add(new_name)
        renamed[chip.name] = new_name
        report["copied"].append(f"history/{chip.name} -> history/{new_name}")
    # aliases: the user folder's entries win; ours follow any rename
    for name, entry in (old_alias.get("names") or {}).items():
        if name in new_alias["names"] or not isinstance(entry, dict):
            if name in new_alias["names"]:
                report["kept_user"].append(f"history alias '{name}'")
            continue
        e = dict(entry)
        e["dir"] = renamed.get(str(e.get("dir")), e.get("dir"))
        new_alias["names"][name] = e
    for d, entry in (old_alias.get("dirs") or {}).items():
        d2 = renamed.get(d, d)
        if d2 not in new_alias["dirs"]:
            new_alias["dirs"][d2] = entry
    _write_json(dst / "_chip_aliases.json", new_alias)
    report["renamed"] = renamed


# --------------------------------------------------------------- the move
def migrate(legacy: Path, user: Path) -> dict | None:
    """Carry *legacy* (an env-local instance folder) into *user*, once.

    Returns the report, or None when there was nothing to do (no legacy
    folder, the same folder, already moved, or another process is moving it
    right now)."""
    legacy, user = Path(legacy), Path(user)
    if not legacy.is_dir() or (legacy / MARKER).exists():
        return None
    try:
        if legacy.resolve() == user.resolve():
            return None
    except OSError:
        return None
    user.mkdir(parents=True, exist_ok=True)
    lock = user / ".migrate.lock"
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        try:
            if time.time() - lock.stat().st_mtime < 600:
                return None                 # another SM is moving it now
            lock.unlink()
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
        except OSError:
            return None
    report: dict = {"from": str(legacy), "to": str(user), "copied": [], "merged": [],
                    "kept_user": [], "skipped": [], "renamed": {}, "errors": []}
    try:
        for child in sorted(legacy.iterdir()):
            name = child.name
            try:
                if name in _SKIP:
                    report["skipped"].append(name)
                elif name == "history" and child.is_dir():
                    _migrate_history(child, user / "history", report)
                elif name in _JSON_MERGES and child.is_file():
                    target = user / name
                    old = _read_json(child)
                    if old is None:
                        report["skipped"].append(name + " (unreadable)")
                    elif not target.exists():
                        shutil.copy2(child, target)
                        report["copied"].append(name)
                    else:
                        _write_json(target, _JSON_MERGES[name](old, _read_json(target)))
                        report["merged"].append(name)
                elif child.is_dir():
                    _copy_missing(child, user / name, name, report)
                elif not (user / name).exists():
                    shutil.copy2(child, user / name)
                    report["copied"].append(name)
                else:
                    report["kept_user"].append(name)
            except Exception as e:  # noqa: BLE001 -- one bad entry never blocks the rest
                report["errors"].append(f"{name}: {e}")
                logger.warning("instance move: %s failed", name, exc_info=True)
        report["at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        try:
            (legacy / MARKER).write_text(json.dumps(report, indent=2), encoding="utf-8")
        except OSError:
            # a read-only env folder: the move happened, it just cannot be
            # marked -- every later start re-merges, which is idempotent
            logger.warning("instance move: could not write the marker in %s", legacy)
        logger.info("Moved SM state from %s to %s: %d copied, %d merged, %d kept, %d errors",
                    legacy, user, len(report["copied"]), len(report["merged"]),
                    len(report["kept_user"]), len(report["errors"]))
        return report
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


def migrate_env_local(user: str | Path, prefix: str | None = None) -> list[dict]:
    """Every env-local instance folder of *prefix* (default: this Python) into
    *user*. Never raises: a move that fails leaves both folders as they were
    and SM starts on the user folder regardless."""
    out = []
    for legacy in legacy_dirs(prefix):
        try:
            r = migrate(legacy, Path(user))
            if r is not None:
                out.append(r)
        except Exception:  # noqa: BLE001
            logger.warning("instance move from %s failed", legacy, exc_info=True)
    return out
