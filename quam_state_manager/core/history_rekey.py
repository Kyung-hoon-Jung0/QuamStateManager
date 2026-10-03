"""Param History re-key v4: every experiment snapshot at its run's instant (docs/262).

Before docs/262 an ingested run's snapshot key (``YYYYMMDD_HHMMSS_NNN``) was
the run's ``created_at`` digits read in THIS machine's zone. For a run
recorded in the machine's own zone that is the right UTC second; for any
other zone it is wrong by the difference (a -04:00 archive on a +09:00 machine:
13 h early). ``HistoryManager._entry_timestamp`` now reads the instant
(``timefmt.run_instant`` via ``run_time.resolve``); this migration moves the
snapshots stored under the old reading to the key the new code gives the same
run, so the backfill, the live ingest and the stored history agree.

What moves: a snapshot dir whose ``meta.json`` is an experiment row
(``kind == "exp"`` or ``trigger == "experiment"``) and names its run folder,
whose ``node.json`` dates it with evidence (quality ``offset`` or
``archive_offset``), and whose key differs from that instant's key. Nothing
else is touched. A run folder that cannot be read now, or a run dated only by
assuming this machine's zone (the old reading's own assumption), keeps its key
and is listed in the journal.

How (per chip dir, journal ``<instance>/history_rekey_v4/<chip>.json``):

1. plan: target = ``run_time.snapshot_key(instant, run_id)``; a target held
   by ANY other snapshot dir or index row is a collision and takes the next
   free ``run_time.collision_key`` -- two snapshots are never merged and a
   free name is never taken twice. The journal (with every moved
   ``meta.json``'s original bytes + times) is written BEFORE anything moves.
2. rename each dir (atomic, same volume), then rewrite its ``meta.json`` with
   the new ``timestamp`` and ``rekeyed_from``;
3. relabel the index rows (``HistoryManager._relabel_index_rows``, the
   docs/200 re-stamp machinery), then -- because a 13 h move can change the
   ORDER of snapshots, which the curated change-point companion and the leaf
   change points depend on -- drop the curated companion
   (``_cp_invalidate``) and mark the leaf index dirty; delete the snapshot
   manifest (rebuilt from the dirs on the next listing);
4. rebuild the leaf index from the dirs (``leaf_index.rebuild``).

Every step checks before it acts, so a killed process resumes the journal on
the next start (a half-done apply finishes; a half-done revert finishes).
:func:`revert_history_rekey_v4` walks the journal backwards and restores each
``meta.json`` byte-for-byte (and its mtime) when nothing edited it since.

Idempotent: the plan maps a snapshot already at its instant's key (or one of
its collision keys) to itself, so a second run moves nothing and writes
nothing. Gated by ``<instance>/migrated_v4.flag`` (status ``migrated`` or
``reverted``); an unfinished journal is resumed regardless of the flag.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quam_state_manager.core import leaf_index, run_time, safe_io

logger = logging.getLogger(__name__)

FLAG_NAME = "migrated_v4.flag"
JOURNAL_DIR = "history_rekey_v4"
_LOCK_NAME = ".rekey_v4.lock"
_KEY_RE = re.compile(r"^\d{8}_\d{6}_(\d{3})(\d{2})?$")
_SKIP_DIRS = re.compile(r"^pytest-\d+$")


class RekeyError(RuntimeError):
    """A chip's re-key cannot proceed safely (its journal stays for a retry)."""


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def _chip_dirs(history_root: Path) -> list[Path]:
    try:
        return sorted(d for d in history_root.iterdir()
                      if d.is_dir() and not _SKIP_DIRS.match(d.name)
                      and d.name != "Temp")
    except OSError:
        return []


def _read_meta(snap_dir: Path) -> tuple[bytes, dict] | None:
    try:
        raw = (snap_dir / "meta.json").read_bytes()
        data = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError):
        return None
    return (raw, data) if isinstance(data, dict) else None


def _is_exp(meta: dict) -> bool:
    return meta.get("kind") == "exp" or meta.get("trigger") == "experiment"


def _index_timestamps(idx: Path) -> set[str]:
    out: set[str] = set()
    if not idx.exists():
        return out
    try:
        # Read-only: a plan that moves nothing must leave the chip untouched.
        # With no -wal file the main file is the whole database, and
        # ``immutable`` reads it without creating -wal/-shm sidecars; with
        # one, ``mode=ro`` reads through it (its -shm already exists).
        wal = idx.with_name(idx.name + "-wal")
        mode = "mode=ro" if wal.exists() else "immutable=1"
        conn = sqlite3.connect(idx.resolve().as_uri() + "?" + mode, uri=True,
                               timeout=10.0)
        try:
            for (ts,) in conn.execute("SELECT DISTINCT timestamp FROM param_history"):
                out.add(ts)
            try:
                for (ts,) in conn.execute("SELECT ts FROM leaf_snaps"):
                    out.add(ts)
            except sqlite3.Error:
                pass
        finally:
            conn.close()
    except sqlite3.Error:
        logger.warning("re-key v4: could not read %s", idx, exc_info=True)
    return out


def _new_meta_bytes(meta: dict, new_ts: str, old_ts: str) -> bytes:
    data = dict(meta)
    data["timestamp"] = new_ts
    data["rekeyed_from"] = old_ts          # audit trail; SnapshotMeta ignores it
    return json.dumps(data, indent=2).encode("utf-8")


def _write_bytes_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".rekey.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _journal_path(instance: Path, chip: str) -> Path:
    return instance / JOURNAL_DIR / f"{chip}.json"


def _read_journal(p: Path) -> dict | None:
    try:
        j = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return j if isinstance(j, dict) and j.get("v") == 4 else None


def _machine_offset() -> str:
    return datetime.now().astimezone().strftime("%z")


# --------------------------------------------------------------------------
# the instance lock (one migrating process at a time)
# --------------------------------------------------------------------------

class _InstanceLock:
    def __init__(self, history_root: Path):
        self.path = history_root / _LOCK_NAME
        self.held = False

    def __enter__(self) -> "_InstanceLock":
        from quam_state_manager.core.instances import pid_alive
        for _ in range(2):
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    pid = int(self.path.read_text(encoding="utf-8").strip() or 0)
                except (OSError, ValueError):
                    pid = 0
                if pid and pid != os.getpid() and pid_alive(pid):
                    return self                 # another process is migrating
                try:
                    self.path.unlink()          # stale lock of a dead process
                except OSError:
                    return self
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(str(os.getpid()))
            self.held = True
            return self
        return self

    def __exit__(self, *exc) -> None:
        if self.held:
            try:
                self.path.unlink()
            except OSError:
                pass


# --------------------------------------------------------------------------
# plan
# --------------------------------------------------------------------------

def plan_chip(chip_dir: Path) -> dict:
    """What a re-key of *chip_dir* would do. Reads only (the history dir and
    the run folders' ``node.json``)."""
    names: list[str] = []
    metas: dict[str, tuple[bytes, dict]] = {}
    try:
        for de in sorted(os.scandir(chip_dir), key=lambda d: d.name):
            if not de.is_dir():
                continue
            got = _read_meta(Path(de.path))
            names.append(de.name)
            if got is not None:
                metas[de.name] = got
    except OSError as exc:
        raise RekeyError(f"cannot list {chip_dir}: {exc}") from exc
    index_ts = _index_timestamps(chip_dir / "index.sqlite")
    occupied = set(names) | index_ts
    moves: list[dict] = []
    unresolved: list[dict] = []
    kept_assumed: list[dict] = []
    collisions = 0
    n_exp = 0
    for name in names:
        got = metas.get(name)
        if got is None:
            continue
        raw, meta = got
        if not _is_exp(meta):
            continue
        n_exp += 1
        folder = meta.get("experiment_folder_path")
        if not folder:
            unresolved.append({"ts": name, "reason": "no run folder recorded"})
            continue
        node = run_time.read_node_times(folder)
        if node is None:
            unresolved.append({"ts": name, "run_folder": folder,
                               "reason": "node.json not readable"})
            continue
        utc_us, quality = run_time.resolve(
            node.get("created_at"), (node.get("metadata") or {}).get("run_end"),
            folder, read_node=False)
        if utc_us is None:
            unresolved.append({"ts": name, "run_folder": folder,
                               "reason": "node.json dates nothing"})
            continue
        run_id = meta.get("run_id")
        if run_id is None:
            m = _KEY_RE.match(name)
            run_id = int(m.group(1)) if m else 0
        base = run_time.snapshot_key(utc_us, run_id)
        if run_time.is_key_of(name, base):
            continue                                    # already at its instant
        if quality not in ("offset", "archive_offset"):
            # The old key read the digits in this machine's zone too: moving
            # it would trade one assumption for another.
            kept_assumed.append({"ts": name, "run_folder": folder,
                                 "would_be": base, "quality": quality})
            continue
        target = base
        k = 0
        while target in occupied:
            k += 1
            if k > run_time.COLLISION_MAX:
                target = None
                break
            target = run_time.collision_key(base, k)
        if target is None:
            unresolved.append({"ts": name, "run_folder": folder,
                               "reason": f"every collision key of {base} is taken"})
            continue
        if k:
            collisions += 1
        occupied.add(target)
        snap_meta = chip_dir / name / "meta.json"
        st = snap_meta.stat()
        new_bytes = _new_meta_bytes(meta, target, name)
        moves.append({
            "from": name, "to": target, "base": base, "quality": quality,
            "instant_us": utc_us, "run_folder": folder,
            "shift_s": _shift_s(name, target),
            "meta_b64": base64.b64encode(raw).decode("ascii"),
            "meta_sha": _sha(raw), "new_meta_sha": _sha(new_bytes),
            "meta_atime_ns": st.st_atime_ns, "meta_mtime_ns": st.st_mtime_ns,
        })
    return {"chip": chip_dir.name, "snapshots": len(names), "exp": n_exp,
            "moves": moves, "unresolved": unresolved,
            "kept_assumed_local": kept_assumed, "collisions": collisions}


def _shift_s(old: str, new: str) -> int | None:
    try:
        a = datetime.strptime(old[:15], "%Y%m%d_%H%M%S")
        b = datetime.strptime(new[:15], "%Y%m%d_%H%M%S")
    except ValueError:
        return None
    return int((b - a).total_seconds())


# --------------------------------------------------------------------------
# apply / revert one chip
# --------------------------------------------------------------------------

def _move_dir(chip_dir: Path, src: str, dst: str) -> None:
    a, b = chip_dir / src, chip_dir / dst
    if a.exists() and not b.exists():
        os.rename(a, b)
    elif a.exists() and b.exists():
        raise RekeyError(f"{chip_dir.name}: both {src} and {dst} exist")
    elif not a.exists() and not b.exists():
        raise RekeyError(f"{chip_dir.name}: snapshot {src} is gone")
    # else: already moved


def _relabel(chip_dir: Path, pairs: list[tuple[str, str, dict]]) -> None:
    """Relabel index rows for every (old, new, meta) pair, then invalidate what
    depends on ORDER. No index file: nothing to relabel (never created here)."""
    from quam_state_manager.core.history import (
        HistoryManager, _cp_invalidate, _ensure_param_history_schema,
        _index_write_lock)
    idx = chip_dir / "index.sqlite"
    if not idx.exists():
        return
    _ensure_param_history_schema(idx)
    with _index_write_lock(idx):
        conn = sqlite3.connect(str(idx), isolation_level=None, timeout=10.0)
        try:
            leaf_index.ensure_schema(conn)
            for old, new, meta in pairs:
                HistoryManager._relabel_index_rows(conn, old, new, meta)
            conn.execute("BEGIN IMMEDIATE")
            try:
                _cp_invalidate(conn)
                leaf_index.mark_dirty(conn, "history re-key v4 (docs/262)")
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()


def _drop_manifest(chip_dir: Path) -> None:
    from quam_state_manager.core.history import HistoryManager
    try:
        (chip_dir / HistoryManager._MANIFEST_NAME).unlink()
    except FileNotFoundError:
        pass


def rebuild_leaf(instance: Path, chip_dir: Path) -> dict | None:
    """Recompute the chip's leaf change points from its snapshot dirs."""
    from quam_state_manager.core.history import (
        HistoryManager, _index_write_lock)
    idx = chip_dir / "index.sqlite"
    if not idx.exists():
        return None
    hm = HistoryManager(instance)
    snaps = hm._list_snapshots_in_dir(chip_dir)
    meta_by_ts = {m.timestamp: m for m in snaps}
    available = [m.timestamp for m in snaps
                 if (chip_dir / m.timestamp / "state.json").exists()]
    with _index_write_lock(idx):
        conn = sqlite3.connect(str(idx), isolation_level=None, timeout=10.0)
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                res = leaf_index.rebuild(
                    conn, timestamps=available,
                    load=hm._leaf_load_snapshot(chip_dir, meta_by_ts))
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()
    return res


def _apply_moves(chip_dir: Path, moves: list[dict]) -> None:
    pairs = []
    for m in moves:
        _move_dir(chip_dir, m["from"], m["to"])
        meta_p = chip_dir / m["to"] / "meta.json"
        cur = meta_p.read_bytes()
        orig = base64.b64decode(m["meta_b64"])
        meta = json.loads(orig.decode("utf-8"))
        new_bytes = _new_meta_bytes(meta, m["to"], m["from"])
        if _sha(cur) != _sha(new_bytes):
            _write_bytes_atomic(meta_p, new_bytes)
        pairs.append((m["from"], m["to"], json.loads(new_bytes.decode("utf-8"))))
    _relabel(chip_dir, pairs)
    _drop_manifest(chip_dir)


def _revert_moves(chip_dir: Path, moves: list[dict]) -> list[str]:
    """Undo *moves* (newest first). Returns the snapshots whose meta.json was
    edited after the re-key (their edit is kept, only the stamp goes back)."""
    edited: list[str] = []
    pairs = []
    for m in reversed(moves):
        orig = base64.b64decode(m["meta_b64"])
        if (chip_dir / m["to"]).exists() or not (chip_dir / m["from"]).exists():
            _move_dir(chip_dir, m["to"], m["from"])
        meta_p = chip_dir / m["from"] / "meta.json"
        cur = meta_p.read_bytes()
        if _sha(cur) == m["new_meta_sha"]:
            _write_bytes_atomic(meta_p, orig)
            os.utime(meta_p, ns=(m["meta_atime_ns"], m["meta_mtime_ns"]))
        elif _sha(cur) != m["meta_sha"]:
            # edited since (a label, a pin): keep the edit, restore the stamp
            data = json.loads(cur.decode("utf-8"))
            data["timestamp"] = m["from"]
            data.pop("rekeyed_from", None)
            _write_bytes_atomic(meta_p, json.dumps(data, indent=2).encode("utf-8"))
            edited.append(m["from"])
        pairs.append((m["to"], m["from"], json.loads(orig.decode("utf-8"))))
    _relabel(chip_dir, pairs)
    _drop_manifest(chip_dir)
    return edited


# --------------------------------------------------------------------------
# public entry points
# --------------------------------------------------------------------------

def _finish_pending_restamps(instance: Path, chip_dirs: list[Path]) -> None:
    from quam_state_manager.core.history import HistoryManager
    hm = None
    for d in chip_dirs:
        if (d / HistoryManager._RESTAMP_INTENT).exists():
            hm = hm or HistoryManager(instance)
            hm._finish_restamp(d)


def _resume(instance: Path, *, rebuild: bool) -> list[str]:
    """Finish every journal a killed process left mid-way."""
    done: list[str] = []
    jdir = instance / JOURNAL_DIR
    if not jdir.is_dir():
        return done
    for jp in sorted(jdir.glob("*.json")):
        j = _read_journal(jp)
        if j is None or j.get("state") not in ("applying", "reverting"):
            continue
        chip_dir = instance / "history" / j["chip"]
        moves = [m for r in j.get("rounds", []) for m in r.get("moves", [])]
        if j["state"] == "applying":
            _apply_moves(chip_dir, j["rounds"][-1]["moves"])
            j["state"] = "applied"
        else:
            _revert_moves(chip_dir, moves)
            j["state"] = "reverted"
            # a revert covers the whole instance: finishing one finishes it
            safe_io.atomic_write_json(instance / FLAG_NAME, {
                "status": "reverted", "chips": {j["chip"]: {"resumed": True}},
                "at": _now_iso()})
        j["resumed_at"] = _now_iso()
        safe_io.atomic_write_json(jp, j)
        if rebuild:
            rebuild_leaf(instance, chip_dir)
        done.append(j["chip"])
    return done


def migrate_history_rekey_v4(instance_path: str | Path, *, rebuild_leaf_index: bool = True,
                             force: bool = False, skip_if_peers: bool = True) -> dict[str, Any]:
    """Re-key every experiment snapshot to its run's instant (docs/262).

    Returns ``{"status": ..., "chips": {chip: summary}}``. Status:
    ``already_migrated`` / ``reverted`` (flag present, nothing pending),
    ``no_history``, ``deferred`` (another SM process is open on this instance,
    or another process holds the migration lock -- retried next start),
    ``migrated``. ``force`` re-plans even with the flag present (a chip whose
    last journal was reverted is re-applied only under ``force``).
    """
    inst = Path(instance_path)
    flag = inst / FLAG_NAME
    history_root = inst / "history"
    if not history_root.is_dir():
        if not flag.exists():
            safe_io.atomic_write_json(flag, {"status": "migrated", "chips": {},
                                             "at": _now_iso()})
        return {"status": "no_history"}
    jdir = inst / JOURNAL_DIR
    pending = jdir.is_dir() and any(
        (_read_journal(p) or {}).get("state") in ("applying", "reverting")
        for p in jdir.glob("*.json"))
    if flag.exists() and not force and not pending:
        try:
            st = json.loads(flag.read_text(encoding="utf-8")).get("status")
        except (OSError, ValueError, AttributeError):
            st = None
        return {"status": "reverted" if st == "reverted" else "already_migrated"}
    if skip_if_peers:
        from quam_state_manager.core import instances
        if instances.peers(inst):
            logger.info("history re-key v4 deferred: another SM process is "
                        "open on %s", inst)
            return {"status": "deferred", "reason": "peer process"}
    with _InstanceLock(history_root) as lock:
        if not lock.held:
            return {"status": "deferred", "reason": "locked"}
        resumed = _resume(inst, rebuild=rebuild_leaf_index)
        if flag.exists() and not force:
            return {"status": "resumed", "chips": resumed}
        chips = _chip_dirs(history_root)
        _finish_pending_restamps(inst, chips)
        report: dict[str, Any] = {}
        failed: list[str] = []
        for chip_dir in chips:
            jp = _journal_path(inst, chip_dir.name)
            j = _read_journal(jp)
            if j is not None and j.get("state") == "reverted" and not force:
                report[chip_dir.name] = {"skipped": "reverted"}
                continue
            try:
                plan = plan_chip(chip_dir)
            except RekeyError as exc:
                logger.warning("history re-key v4: %s", exc)
                failed.append(chip_dir.name)
                continue
            summary = {k: (len(v) if isinstance(v, list) else v)
                       for k, v in plan.items() if k != "chip"}
            report[chip_dir.name] = summary
            if not plan["moves"]:
                continue                     # nothing moves: write nothing here
            if j is None or j.get("state") == "reverted":
                if j is not None:
                    jp.rename(jp.with_name(f"{chip_dir.name}.reverted-"
                                           f"{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.json"))
                j = {"v": 4, "chip": chip_dir.name, "rounds": []}
            j["rounds"].append({"at": _now_iso(), "machine_offset": _machine_offset(),
                                **{k: plan[k] for k in ("moves", "unresolved",
                                                        "kept_assumed_local",
                                                        "collisions")}})
            j["state"] = "applying"
            jp.parent.mkdir(parents=True, exist_ok=True)
            safe_io.atomic_write_json(jp, j)          # BEFORE anything moves
            try:
                _apply_moves(chip_dir, plan["moves"])
            except (RekeyError, OSError, sqlite3.Error) as exc:
                logger.warning("history re-key v4 of %s stopped (resumes next "
                               "start): %s", chip_dir.name, exc, exc_info=True)
                failed.append(chip_dir.name)
                continue
            j["state"] = "applied"
            safe_io.atomic_write_json(jp, j)
            if rebuild_leaf_index:
                try:
                    rebuild_leaf(inst, chip_dir)
                except Exception:  # noqa: BLE001 -- dirty index heals on read
                    logger.warning("history re-key v4: leaf rebuild of %s "
                                   "failed; it is marked dirty and rebuilds "
                                   "on the next read", chip_dir.name, exc_info=True)
        out = {"status": "migrated" if not failed else "partial",
               "chips": report, "failed": failed, "at": _now_iso(),
               "machine_offset": _machine_offset()}
        if not failed:
            safe_io.atomic_write_json(flag, out)
        moved = sum(int(c.get("moves", 0)) for c in report.values()
                    if isinstance(c, dict))
        if moved:
            logger.info("history re-key v4: moved %d snapshots: %s", moved, report)
        return out


def revert_history_rekey_v4(instance_path: str | Path, *,
                            rebuild_leaf_index: bool = True) -> dict[str, Any]:
    """Put every snapshot the re-key moved back under its old key.

    Each ``meta.json`` nothing edited since gets its original bytes and mtime
    back; an edited one keeps the edit and only its stamp goes back (listed in
    ``edited``). The flag becomes ``reverted`` so the next start does not
    re-apply (``migrate_history_rekey_v4(force=True)`` does)."""
    inst = Path(instance_path)
    history_root = inst / "history"
    jdir = inst / JOURNAL_DIR
    report: dict[str, Any] = {}
    with _InstanceLock(history_root) as lock:
        if not lock.held:
            return {"status": "deferred", "reason": "locked"}
        for jp in sorted(jdir.glob("*.json")) if jdir.is_dir() else []:
            j = _read_journal(jp)
            if j is None or j.get("state") == "reverted" or ".reverted-" in jp.name:
                continue
            chip_dir = history_root / j["chip"]
            moves = [m for r in j.get("rounds", []) for m in r.get("moves", [])]
            j["state"] = "reverting"
            safe_io.atomic_write_json(jp, j)
            edited = _revert_moves(chip_dir, moves)
            j["state"] = "reverted"
            j["reverted_at"] = _now_iso()
            safe_io.atomic_write_json(jp, j)
            if rebuild_leaf_index:
                rebuild_leaf(inst, chip_dir)
            report[j["chip"]] = {"reverted": len(moves), "edited": edited}
        safe_io.atomic_write_json(inst / FLAG_NAME, {
            "status": "reverted", "chips": report, "at": _now_iso()})
    return {"status": "reverted", "chips": report}
