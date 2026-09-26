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
persistent connection never writes, so every commit is "another
connection's".

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
_CONNS: "OrderedDict[str, tuple[tuple, sqlite3.Connection, threading.Lock]]" = OrderedDict()
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
            try:
                conn = sqlite3.connect(f"file:{index_path.as_posix()}?mode=ro", uri=True,
                                       check_same_thread=False, isolation_level=None)
            except sqlite3.Error:
                return ("unreadable", ident)
            ent = (ident, conn, threading.Lock(), next(_CONN_GEN))
            _CONNS[key] = ent
            while len(_CONNS) > _CONNS_MAX:
                _k, (_i, old_conn, old_lk, _g) = _CONNS.popitem(last=False)
                with old_lk:
                    try:
                        old_conn.close()
                    except sqlite3.Error:
                        pass
        else:
            _CONNS.move_to_end(key)
    ident0, conn, lk, gen = ent
    with lk:
        try:
            dv = conn.execute("PRAGMA data_version").fetchone()[0]
        except sqlite3.Error:
            with _CONN_LOCK:
                if _CONNS.get(key) is ent:
                    _CONNS.pop(key, None)
            try:
                conn.close()
            except sqlite3.Error:
                pass
            return ("unreadable", ident)
    return (ident0, gen, dv)


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
    with _CONN_LOCK:
        ents = list(_CONNS.values())
        _CONNS.clear()
    for _i, conn, _l, _g in ents:
        try:
            conn.close()
        except sqlite3.Error:
            pass
