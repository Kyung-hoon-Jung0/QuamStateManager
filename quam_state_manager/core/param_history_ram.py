"""RAM P8 -- Param History and the value-history drawer served from RAM
(ram_design.md §4 P8, §1.2 ``hist_token``).

What an answer computed from a chip's history store is valid for:

``hist_token(hm, quam_state_path)`` =
    (history dir,
     the manager's in-process ``_chip_dir_version`` for it (every capture,
       ingest, restamp and prune bumps it),
     ``PRAGMA data_version`` of ONE persistent read-only connection to the
       chip's ``index.sqlite`` -- it moves on every commit made by ANY other
       connection, this process's writers and a second SM process alike,
     the index file's identity (ino, ctime): a rebuilt/replaced file is a new
       database, and the persistent connection is reopened on it,
     the snapshot list's identity (count + newest timestamp): the manifest /
       meta.json side, which is not in SQLite)

SQLite's documentation of ``PRAGMA data_version``: "The integer values
returned by two invocations of "PRAGMA data_version" from the same connection
will be different if changes were committed to the database by any other
connection in the interim." and "The behavior of "PRAGMA data_version" is the
same for all database connections, including database connections in separate
processes and shared cache database connections." [doc: sqlite.org/pragma.html
#pragma_data_version, both quoted verbatim, fetched 2026-09-26]. The
persistent connection never writes a row (it only checkpoints the WAL, which
is not a commit), so every commit is "another connection's".

Nothing here keeps a value correct by being told about events: a reader
computes the token and a mismatch is a miss (design §1.1).
"""
from __future__ import annotations

import logging
import os
import sqlite3
import itertools
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CONN_LOCK = threading.Lock()
# index path -> (file identity, connection, per-connection lock), LRU order.
# Bounded: each entry is an OPEN file handle on a chip's index.sqlite, and on
# Windows an open handle keeps that file (and its dir) from being removed --
# only the few chips somebody is looking at hold one.
_CONNS: "OrderedDict[str, tuple]" = OrderedDict()
_CONNS_MAX = 4
# data_version values are only comparable within ONE connection, so every
# token carries the connection's generation: a reopened connection (file
# replaced, or evicted and opened again) can never reproduce an old token.
_CONN_GEN = itertools.count(1)


def _file_identity(p: Path) -> tuple | None:
    try:
        st = os.stat(p)
    except OSError:
        return None
    return (st.st_ino, st.st_ctime_ns, st.st_dev)


def _truncate_wal(conn: sqlite3.Connection, index_path: Path) -> bool:
    """Give back the WAL a writer left behind once it has committed. True
    when there is nothing left to give back; False when the checkpoint came
    back busy (or failed) and must be tried again later.

    A WAL database whose LAST connection closes is checkpointed and its -wal
    removed; this persistent connection means a writer's close is never the
    last one, so without this -wal stayed at its high-water size for the life
    of the process and Param History's 'MB on disk' counted it (verifier D1:
    a 6,000-row write left 6,204,752 B). ``wal_checkpoint(TRUNCATE)`` backfills
    and truncates it to 0 B. ``timeout=0`` on the connection: a writer that is
    busy right now makes this return busy at once, never wait in a request.

    SQLite's own contract for the result row: "The first column is usually
    0 but will be 1 if a RESTART or FULL or TRUNCATE checkpoint was blocked
    from completing, for example because another thread or process was
    actively using the database." [doc: sqlite.org/pragma.html
    #pragma_wal_checkpoint, quoted verbatim, fetched 2026-09-27]. A blocked one used to be
    recorded as done, so the WAL stayed at its peak until the NEXT commit
    (final QA, fix 3): the caller now keeps the old ``seen`` value and
    re-arms a retry (``_retry_later``).

    An EMPTY -wal is not checkpointed at all: every TRUNCATE restarts the
    log, and a restart moves every OTHER connection's ``data_version``
    (measured on this machine, even with nothing to backfill) -- so a
    truncate of nothing would still invalidate every token read on another
    connection, and two SM processes watching one chip would each see the
    other's no-op as a commit and answer it with one of their own, forever.
    A checkpoint is not a commit, so it does not move THIS connection's
    data_version (measured). Best effort: every error is swallowed. [derived]"""
    try:
        if os.stat(str(index_path) + "-wal").st_size == 0:
            return True
    except OSError:
        return True                      # no -wal: nothing is pinned
    try:
        row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    except sqlite3.Error:
        return False
    return not row or row[0] == 0


# ── retry-later for a checkpoint that came back busy ──────────────────────
# One pending timer per index path at most. The retry runs on the SAME pooled
# connection as every token read (``data_version``), so it can never move a
# token it did not also account for; it never opens a connection (a path
# whose connection was closed or evicted meanwhile is dropped -- its close
# gave the WAL back, or the next open re-arms). Backoff doubles from
# ``_RETRY_FIRST_S`` to ``_RETRY_MAX_S``; after ``_RETRY_MAX_N`` attempts it
# stops until the next read, which re-arms it (``seen`` still differs).
_RETRY_LOCK = threading.Lock()
_RETRY_TIMERS: "dict[str, threading.Timer]" = {}
_RETRY_N: "dict[str, int]" = {}
_RETRY_FIRST_S = 0.25
_RETRY_MAX_S = 5.0
_RETRY_MAX_N = 60


def _retry_later(key: str) -> None:
    with _RETRY_LOCK:
        if key in _RETRY_TIMERS:
            return
        n = _RETRY_N.get(key, 0)
        if n >= _RETRY_MAX_N:
            return
        _RETRY_N[key] = n + 1
        t = threading.Timer(min(_RETRY_FIRST_S * (2 ** n), _RETRY_MAX_S), _retry, (key,))
        t.daemon = True
        t.name = "phr-wal-retry"
        _RETRY_TIMERS[key] = t
    t.start()


def _retry(key: str) -> None:
    with _RETRY_LOCK:
        _RETRY_TIMERS.pop(key, None)
    with _CONN_LOCK:
        present = key in _CONNS
    if not present:
        return
    try:
        data_version(Path(key))          # re-arms itself while still busy
    except Exception:  # noqa: BLE001 - a background give-back never raises
        logger.debug("wal retry failed", exc_info=True)


def _retry_done(key: str) -> None:
    with _RETRY_LOCK:
        _RETRY_N.pop(key, None)


def data_version(index_path: Path) -> tuple:
    """``(file identity, connection generation, PRAGMA data_version)`` of *index_path*, or a
    constant "absent" marker. Opens (or reopens, when the file was replaced)
    the one persistent read-only connection for that path."""
    key = str(index_path)
    ident = _file_identity(index_path)
    if ident is None:
        return ("absent",)
    with _CONN_LOCK:
        ent = _CONNS.get(key)
        if ent is not None and ent[0] != ident:
            try:
                ent[1].close()
            except sqlite3.Error:
                pass
            ent = None
        if ent is None:
            conn = None
            # mode=rw (never creates) so the connection can CHECKPOINT: a
            # read-only one cannot backfill the WAL (measured: "disk I/O
            # error"). It still never writes a row. mode=ro is the fallback
            # for an index this process may not write.
            for mode in ("rw", "ro"):
                try:
                    conn = sqlite3.connect(f"file:{index_path.as_posix()}?mode={mode}",
                                           uri=True, check_same_thread=False,
                                           isolation_level=None, timeout=0)
                    break
                except sqlite3.Error:
                    conn = None
            if conn is None:
                return ("unreadable", ident)
            ent = (ident, conn, threading.Lock(), next(_CONN_GEN), [None])
            _CONNS[key] = ent
            while len(_CONNS) > _CONNS_MAX:
                _k, (_i, old_conn, old_lk, _g, _s) = _CONNS.popitem(last=False)
                with old_lk:
                    try:
                        old_conn.close()
                    except sqlite3.Error:
                        pass
        else:
            _CONNS.move_to_end(key)
    ident0, conn, lk, gen, seen = ent
    with lk:
        try:
            dv = conn.execute("PRAGMA data_version").fetchone()[0]
            given_back = True
            if seen[0] != dv:
                given_back = _truncate_wal(conn, index_path)
                if given_back:
                    seen[0] = dv         # busy: keep the old value -- the next read retries
        except sqlite3.Error:
            with _CONN_LOCK:
                if _CONNS.get(key) is ent:
                    _CONNS.pop(key, None)
            try:
                conn.close()
            except sqlite3.Error:
                pass
            return ("unreadable", ident)
    if given_back:
        _retry_done(key)
    else:
        _retry_later(key)                # and so does a timer, if no read comes
    return (ident0, gen, dv)


def settle_wal(index_path: Path) -> None:
    """Before a caller measures the history dir on disk: let the persistent
    connection (only if one is open in this pool or in ``chip_trends_ram``'s
    -- with no reader open anywhere there is no pin to undo) see any commit
    made since it last looked, which truncates the WAL that commit left
    (``_truncate_wal``)."""
    with _CONN_LOCK:
        open_ = str(index_path) in _CONNS
    if not open_:
        # w7 integration: chip_trends_ram keeps its own persistent readers on
        # the same file (Chip Status Trends), and an open reader from EITHER
        # pool is a pin. Its readers never checkpoint, so the give-back is
        # done here, through this pool's connection (opened for it, bounded).
        try:
            from quam_state_manager.core import chip_trends_ram
            open_ = chip_trends_ram.has_conn(index_path)
        except Exception:  # noqa: BLE001 - a measurement helper never raises
            open_ = False
    if open_:
        data_version(index_path)


def hist_token(hm: Any, quam_state_path: Path | str) -> tuple:
    """The token every P8 cache is validated against (module docstring)."""
    path = Path(quam_state_path)
    hist_dir = hm._history_dir(path)
    key = str(hist_dir)
    with hm._lock:
        ver = hm._chip_dir_version.get(key, 0)
    snaps = hm.list_snapshots(path)            # the manager's own cached list
    snap_sig = (len(snaps), snaps[0].timestamp if snaps else None,
                snaps[-1].timestamp if snaps else None)
    return (key, ver, data_version(hist_dir / "index.sqlite"), snap_sig)


def close_all() -> None:
    """Tests and shutdown: drop every persistent connection."""
    with _RETRY_LOCK:
        timers = list(_RETRY_TIMERS.values())
        _RETRY_TIMERS.clear()
        _RETRY_N.clear()
    for t in timers:
        t.cancel()
    with _CONN_LOCK:
        ents = list(_CONNS.values())
        _CONNS.clear()
    for _i, conn, _l, _g, _s in ents:
        try:
            conn.close()
        except sqlite3.Error:
            pass


# ── the parameter typeahead (/param-history/param-search) ─────────────────
# One ranked path list per chip history, validated on read by hist_token:
# a capture/ingest/prune (any commit to index.sqlite) is a miss and a full
# rebuild -- never a patched list.
_PATH_RANK_MEMO: Any = None
#: per history dir, the natural order of every path the last rank saw -- a
#: pure function of the path strings, so it can never be stale, only
#: incomplete (a new path), which PathRank detects and recomputes
_NAT_ORDER: "dict[str, dict[str, int]]" = {}
_PATH_RANK_LOCK = threading.Lock()


def _path_rank_memo() -> Any:
    global _PATH_RANK_MEMO
    with _PATH_RANK_LOCK:
        if _PATH_RANK_MEMO is None:
            from quam_state_manager.core import ramcache
            _PATH_RANK_MEMO = ramcache.KeyedMemo(
                "param_history.path_rank", max_entries=2,
                max_bytes=96 * 1024 * 1024, sizeof=lambda v: v.nbytes)
        return _PATH_RANK_MEMO


def leaf_search(hm: Any, quam_state_path: Path | str, query: str, *,
                limit: int = 50) -> list[dict]:
    """``hm.leaf_search(path, query, limit=limit)`` answered from RAM.

    The token is read BEFORE the rank is built, so a commit landing during
    the build leaves an entry whose token is already old: the next read
    misses and rebuilds (a value is never newer-labelled than its data).
    Any failure of the accelerator answers through the manager's own SQL."""
    from quam_state_manager.core import leaf_index, ramcache
    path = Path(quam_state_path)

    def compute():
        conn = hm._open_index(path)
        try:
            rank = leaf_index.PathRank.from_conn(
                conn, _NAT_ORDER.get(str(hm._history_dir(path))))
        finally:
            conn.close()
        with _PATH_RANK_LOCK:
            _NAT_ORDER[str(hm._history_dir(path))] = rank.nat_order
            while len(_NAT_ORDER) > 2:
                _NAT_ORDER.pop(next(iter(_NAT_ORDER)))
        return rank

    try:
        token = hist_token(hm, path)
        rank = _path_rank_memo().get(("rank", token[0]), token, compute,
                                     wait_s=30.0)
        return rank.search(query, limit=limit)
    except ramcache.Warming:
        return hm.leaf_search(path, query, limit=limit)
    except Exception:             # noqa: BLE001 -- the memo is an accelerator
        logger.debug("path-rank memo bypassed", exc_info=True)
        return hm.leaf_search(path, query, limit=limit)


_WARMING: set = set()


def warm_path_rank(hm: Any, quam_state_path: Path | str) -> None:
    """Build the typeahead's rank off the request (the Changes page, which
    carries the typeahead, calls this on render), so the first keystroke
    after a capture is not the one that pays the ~1 s rebuild on a 30-qubit
    chip. A current entry makes this a token read and a memo hit."""
    key = str(Path(quam_state_path))
    with _PATH_RANK_LOCK:
        if key in _WARMING:
            return
        _WARMING.add(key)

    def run():
        try:
            leaf_search(hm, quam_state_path, "", limit=1)   # "" builds, answers []
        finally:
            with _PATH_RANK_LOCK:
                _WARMING.discard(key)

    threading.Thread(target=run, name="path-rank-warm", daemon=True).start()
