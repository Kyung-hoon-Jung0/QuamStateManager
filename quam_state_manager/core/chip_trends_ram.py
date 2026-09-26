"""Chip Status > Trends served from RAM (ram_design.md §2a, package P1a).

The measured cost of a Trends request was never the per-path series query
(0.01-0.06 ms); it was per-request DERIVED work recomputed on every open and
every chip toggle: the snapshot provenance map (a ``SELECT DISTINCT`` over the
whole ``param_history`` table plus one ``Path.resolve`` per charted snapshot),
the leaf-family grouping (a ``GROUP BY`` over every indexed path, ~75 ms on a
180k-path index -- once for the badge row and again on every typeahead
keystroke), the metrics-with-data probe, the freshness gate's connection opens,
and the identity-ladder walk in front of each of them.

This module keeps all of that in one object per HISTORY TOKEN:

    token = (chip history dir,
             history_seq_for        -- the SET of snapshot dir names (docs/208 D3),
             _chip_dir_version      -- every in-process capture/ingest/prune bump,
             the snapshot-list object -- its identity changes exactly when the
                                       per-process list cache is dropped,
             the persistent read connection + its PRAGMA data_version
                                     -- any commit to index.sqlite by ANY other
                                        connection, in this process or another)

Validate on read (§1.1): every request computes the token first and a table is
served only for an equal token. A table is filled lazily and each part is
computed at most once; nothing in it is ever patched in place, so a part can
only be as old as the token it was served under. When the token moves, the
next request builds a new table -- the old one is never handed out as current.

The token is read BEFORE any part is computed, so a part may reflect a commit
that landed after its token was read; the next request then sees a different
``data_version`` and rebuilds. That is a redundant recompute, never a stale
serve.

``SM_RAM_VERIFY=1`` (tests): every part served from a table is ALSO recomputed
cold through the uncached path and compared; a difference raises
``StaleCacheError``.

The one implementation rule: a cached part is produced by the SAME function
the cold path calls (``HistoryManager.snapshot_provenance``,
``extract_property_history``, ``history.leaf_series_on``) -- the only
re-implementation is :class:`FamilyTable`, the in-RAM twin of
``leaf_index.path_families`` / ``matching_paths``, which is pinned to the SQL
row for row (tests/test_chip_trends_ram.py).
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable, Iterable

from quam_state_manager.core import ramcache
from quam_state_manager.core.loader import natural_key

logger = logging.getLogger(__name__)

__all__ = ["table", "token", "FamilyTable", "ChipTrendsTable", "stats"]


# ── identity wrapper ─────────────────────────────────────────────────────────

class _Ident:
    """Compares by IDENTITY and keeps its object alive, so an id can never be
    reused by a different object while a token holding it exists."""

    __slots__ = ("obj",)

    def __init__(self, obj: Any):
        self.obj = obj

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, _Ident) and other.obj is self.obj

    def __hash__(self) -> int:
        return id(self.obj)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"_Ident({type(self.obj).__name__}@{id(self.obj):x})"


# ── one persistent read connection per index file ────────────────────────────

class _IndexConn:
    """A long-lived connection used for reading only.

    ``PRAGMA data_version`` is only meaningful across calls on the SAME
    connection ("the integer values returned by two invocations of PRAGMA
    data_version from the same connection will be different if changes were
    committed to the database by any other connection in the interim" --
    sqlite.org/pragma.html#pragma_data_version), which is why the connection
    itself is part of the token: a reopened connection restarts the counter.

    Opened ``mode=rw`` (never creating the file -- a missing index is the cold
    path's business) with ``query_only``; every statement is run to
    completion under ``lock`` so no read transaction is left open.
    """

    def __init__(self, path: Path):
        self.path = str(path)
        uri = "file:" + Path(path).as_posix() + "?mode=rw"
        self.conn = sqlite3.connect(uri, uri=True, check_same_thread=False,
                                    isolation_level=None, timeout=10.0)
        self.conn.execute("PRAGMA query_only=1")
        self.lock = threading.Lock()
        # The token's PRAGMA data_version gets its OWN connection: every
        # request reads it first, and on the shared read connection it queued
        # behind whatever long read held the lock (a first family-table read
        # took the next request from 0.3 s to 1.2 s). Neither connection ever
        # writes, so "committed by any other connection" is every writer.
        self.vconn = sqlite3.connect(uri, uri=True, check_same_thread=False,
                                     isolation_level=None, timeout=10.0)
        self.vconn.execute("PRAGMA query_only=1")
        self.vlock = threading.Lock()

    def data_version(self) -> int:
        with self.vlock:
            return int(self.vconn.execute("PRAGMA data_version").fetchone()[0])

    def run(self, fn: Callable[[sqlite3.Connection], Any]) -> Any:
        with self.lock:
            return fn(self.conn)

    def run_detached(self, fn: Callable[[sqlite3.Connection], Any]) -> Any:
        """*fn* on a connection of its own, for a LONG read (the whole family
        table) that must not hold every other read of this chip behind it."""
        uri = "file:" + Path(self.path).as_posix() + "?mode=rw"
        c = sqlite3.connect(uri, uri=True, isolation_level=None, timeout=10.0)
        try:
            c.execute("PRAGMA query_only=1")
            return fn(c)
        finally:
            c.close()

    def close(self) -> None:
        for lk, c in ((self.lock, self.conn), (self.vlock, self.vconn)):
            with lk:
                try:
                    c.close()
                except sqlite3.Error:
                    pass


_CONN_LOCK = threading.Lock()
_CONNS: "OrderedDict[str, _IndexConn]" = OrderedDict()
_MAX_CONNS = 4


def _conn_for(hist_dir: Path) -> _IndexConn | None:
    idx = Path(hist_dir) / "index.sqlite"
    key = str(idx)
    with _CONN_LOCK:
        c = _CONNS.get(key)
        if c is not None:
            _CONNS.move_to_end(key)
            return c
    if not idx.exists():
        return None
    try:
        c = _IndexConn(idx)
    except sqlite3.Error:
        logger.debug("persistent trends connection unavailable", exc_info=True)
        return None
    evicted: list[_IndexConn] = []
    with _CONN_LOCK:
        cur = _CONNS.get(key)
        if cur is not None:              # another thread won the race
            evicted.append(c)
            c = cur
        else:
            _CONNS[key] = c
            while len(_CONNS) > _MAX_CONNS:
                evicted.append(_CONNS.popitem(last=False)[1])
    for e in evicted:
        e.close()
    return c


def close_all() -> None:
    """Close every persistent connection (tests, shutdown)."""
    with _CONN_LOCK:
        conns = list(_CONNS.values())
        _CONNS.clear()
    for c in conns:
        c.close()
    _MEMO.clear()


# ── the token ────────────────────────────────────────────────────────────────

def token(hm, quam_state_path: str | Path) -> tuple | None:
    """The history token for this chip, or None when it has no history dir.

    Cost on a warm request: one ``stat`` of the history dir (the names set is
    re-listed only when its mtime moved or sits inside the 2 s racy-clean
    window), dict lookups, and one ``PRAGMA data_version``. No
    ``Path.resolve``, no identity-ladder walk, no connection open.
    """
    seq = hm.history_seq_for(quam_state_path)
    hist_dir = hm.history_dir_cached(quam_state_path)
    if hist_dir is None or not seq:
        return None
    snaps = hm.snapshots_cached(quam_state_path)
    ver = hm._chip_dir_version.get(str(hist_dir), 0)
    ic = _conn_for(hist_dir)
    dv = ic.data_version() if ic is not None else None
    return (str(hist_dir), seq, ver, _Ident(snaps), _Ident(ic), dv)


# ── the in-RAM family table (twin of leaf_index.path_families) ───────────────

_ASCII_LOWER = {c: c + 32 for c in range(ord("A"), ord("Z") + 1)}


def _alower(s: str) -> str:
    """SQLite's LIKE folds case for ASCII letters only. For an ASCII string
    that is exactly ``str.lower()`` (the C fast path); only a string with a
    non-ASCII character needs the letter-by-letter table."""
    return s.lower() if s.isascii() else s.translate(_ASCII_LOWER)


def _like_regex(pattern: str, escape: str | None) -> "re.Pattern[str]":
    """A SQLite ``LIKE`` pattern as a regex: ``%`` any run, ``_`` one char,
    the escape char makes the next one literal, ASCII-only case folding."""
    out = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if escape is not None and ch == escape:
            i += 1
            if i >= len(pattern):
                # SQLite: an escape char with nothing after it matches nothing
                return re.compile(r"(?!)")
            out.append(re.escape(pattern[i]))
        elif ch == "%":
            out.append(".*")
        elif ch == "_":
            out.append(".")
        else:
            out.append(re.escape(ch))
        i += 1
    return re.compile("".join(out), re.IGNORECASE | re.ASCII | re.DOTALL)


class _Term:
    """One search term as ``path LIKE '%<escaped term>%' ESCAPE '\\'``."""

    __slots__ = ("sub", "rx")

    def __init__(self, term: str):
        if "\\" in term:
            # The SQL escapes only % and _, so a backslash in the term acts as
            # an escape itself -- match through the exact LIKE semantics.
            pat = "%" + term.replace("%", r"\%").replace("_", r"\_") + "%"
            self.sub = None
            self.rx = _like_regex(pat, "\\")
        else:
            self.sub = _alower(term)
            self.rx = None

    def hit(self, lowered: str, raw: str) -> bool:
        if self.sub is not None:
            return self.sub in lowered
        return self.rx.fullmatch(raw) is not None


class _Family:
    __slots__ = ("scope", "tail", "members", "n", "changes", "blob",
                 "scope_l", "tail_l", "_sk")

    def __init__(self, scope: str, tail: str):
        self.scope = scope
        self.tail = tail
        self.members: list[tuple[str, str, str, int, Any]] = []   # (lower, raw, ent, changes, path id)
        self._sk = None

    def finish(self) -> None:
        m = self.members
        if len(m) == 1:
            self.n, self.changes, self.blob = 1, m[0][3], m[0][0]
        else:
            self.n = len({x[2] for x in m})
            self.changes = sum(x[3] for x in m)
            self.blob = "\n".join(x[0] for x in m)
        self.scope_l = _alower(self.scope)
        self.tail_l = _alower(self.tail)

    def copy(self) -> "_Family":
        """A copy whose member LIST is its own (the tuples are immutable)."""
        f = _Family(self.scope, self.tail)
        f.members = list(self.members)
        f._sk = self._sk
        return f

    @property
    def sort_key(self):
        # natural_key over every tail was the single largest build cost
        # (~40k calls on a new token); only families that reach a result
        # are ever sorted, so compute it on first use.
        if self._sk is None:
            self._sk = natural_key(self.tail)
        return self._sk


class FamilyTable:
    """Every indexed path, split and grouped ONCE, queried in RAM with the
    same semantics as ``leaf_index.path_families`` (the pins compare the two
    row for row, order included)."""

    def __init__(self, rows: Iterable[tuple], roots: tuple[str, ...],
                 term: str | None = None):
        """*rows*: ``(path, changes)``, ``(path, changes, ascii-lowered path)``
        or ``(path, changes, lowered, path_id)`` -- only a table built from
        rows that carry their path ids can be derived (:meth:`derive`).
        *term*: the ASCII-lowered substring every row was pre-filtered by
        (None = every indexed path); a derived table applies the same filter
        to the rows it adds."""
        self.roots = tuple(roots)
        self.term = term
        splitters = []
        for r in self.roots:
            if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", str(r or "")):
                raise ValueError(f"not a path root: {r!r}")
            pre = _alower(r.split("_", 1)[0])       # the literal head before any `_` wildcard
            splitters.append((r, _like_regex(f"{r}.%.%", None), len(r) + 1, pre))
        self._splitters = splitters
        fams: dict[tuple[str, str], _Family] = {}
        n_paths = 0
        for row in rows:
            path, changes = row[0], row[1]
            low = row[2] if len(row) > 2 else _alower(path)
            pid = row[3] if len(row) > 3 else None
            n_paths += 1
            scope, ent, tail = self._split(path, low)
            f = fams.get((scope, tail))
            if f is None:
                f = fams[(scope, tail)] = _Family(scope, tail)
            f.members.append((low, path, ent, int(changes or 0), pid))
        for f in fams.values():
            f.finish()
        self._fams = list(fams.values())
        self._index = {(f.scope, f.tail): i for i, f in enumerate(self._fams)}
        # Parallel lists for the query's first pass: one list comprehension
        # per term over C-level `in` tests (the per-family Python loop cost
        # ~50 ms per keystroke over 22k families).
        self._blobs = [f.blob for f in self._fams]
        self.n_paths = n_paths
        self._qcache: "OrderedDict[tuple, list[dict]]" = OrderedDict()
        self._qlock = threading.Lock()

    def _split(self, path: str, low: str) -> tuple[str, str, str]:
        """``(scope, entity, tail)`` -- the SQL's ``_family_split_sql``."""
        for r, rx, off, pre in self._splitters:
            if not low.startswith(pre) or rx.fullmatch(path) is None:
                continue
            rest = path[off:]
            dot = rest.find(".")
            if dot >= 1:          # SQL: instr(rest, '.') > 1
                return r, rest[:dot], rest[dot + 1:]
        return "", "", path

    def derive(self, added: dict[int, tuple[str, int]],
               new_rows: list[tuple[str, int, str, int]]) -> "FamilyTable":
        """The table after an APPEND-ONLY index change, without re-reading or
        re-grouping every path (RAM P1a: the first Trends request after a
        capture re-read 180k paths -- 0.8 s alone, 16-22 s under the page's
        concurrent polls, because every fetched row re-takes the GIL).

        *added*: ``{path_id: (path, change points added)}`` for paths this
        table already holds (``path`` only locates the family). *new_rows*:
        ``(path, change points, lowered, path_id)`` for paths whose ids are
        above every id this table holds, in id order. Copy-on-write: this
        table is never modified (a request may still be reading it). The
        result is the table ``FamilyTable(all rows)`` would build -- pinned
        family by family. Raises ``LookupError`` when a changed path is not
        where it must be; the caller then builds from scratch.
        """
        new = object.__new__(FamilyTable)
        new.roots, new.term, new._splitters = self.roots, self.term, self._splitters
        fams = list(self._fams)
        index = self._index
        copied: dict[int, _Family] = {}

        def fam_for(scope: str, tail: str, create: bool) -> "_Family | None":
            nonlocal index
            i = index.get((scope, tail))
            if i is None:
                if not create:
                    return None
                if index is self._index:
                    index = dict(index)
                i = index[(scope, tail)] = len(fams)
                f = _Family(scope, tail)
                fams.append(f)
                copied[i] = f
                return f
            f = copied.get(i)
            if f is None:
                f = copied[i] = fams[i] = fams[i].copy()
            return f

        term = self.term
        for pid, (path, add) in added.items():
            low = _alower(path)
            if term is not None and term not in low:
                continue
            scope, _ent, tail = self._split(path, low)
            f = fam_for(scope, tail, False)
            if f is None:
                raise LookupError(f"path id {pid} has no family in this table")
            for k, m in enumerate(f.members):
                if m[4] == pid:
                    f.members[k] = (m[0], m[1], m[2], m[3] + int(add), pid)
                    break
            else:
                raise LookupError(f"path id {pid} is not in its family")
        n_new = 0
        for path, changes, low, pid in new_rows:
            if term is not None and term not in low:
                continue
            n_new += 1
            scope, ent, tail = self._split(path, low)
            fam_for(scope, tail, True).members.append((low, path, ent, int(changes or 0), pid))
        for f in copied.values():
            f.finish()
        new._fams = fams
        new._index = index
        if copied:
            blobs = list(self._blobs)
            for i in sorted(copied):
                if i < len(blobs):
                    blobs[i] = copied[i].blob
                else:
                    blobs.append(copied[i].blob)
            new._blobs = blobs
        else:
            new._blobs = self._blobs
        new.n_paths = self.n_paths + n_new
        new._qcache = OrderedDict()
        new._qlock = threading.Lock()
        return new

    def _signature(self) -> tuple:
        return (self.roots, self.n_paths,
                tuple((f.scope, f.tail, f.n, f.changes, f.blob) for f in self._fams))

    def __eq__(self, other: Any) -> bool:
        # Content equality, so the shadow check (SM_RAM_VERIFY) can compare a
        # served table with a cold rebuild -- identity would always differ.
        return isinstance(other, FamilyTable) and other._signature() == self._signature()

    __hash__ = object.__hash__

    def ram_bytes(self) -> int:
        # ~ 4 strings + a tuple per member, the blob, the sorted path list
        return 220 * self.n_paths + 96 * len(self._fams)

    def query(self, query: str = "", *, limit: int | None = None) -> list[dict]:
        key = (query or "", limit)
        with self._qlock:
            hit = self._qcache.get(key)
            if hit is not None:
                self._qcache.move_to_end(key)
                return [dict(r) for r in hit]
        out = self._query(query, limit)
        with self._qlock:
            self._qcache[key] = out
            while len(self._qcache) > 256:
                self._qcache.popitem(last=False)
        return [dict(r) for r in out]

    def _query(self, query: str, limit: int | None) -> list[dict]:
        from quam_state_manager.core.search_query import groups as _sq_groups

        grps = [[_Term(t) for t in g] for g in _sq_groups(query or "")]
        if (query or "").strip() and not grps:
            return []
        fams = self._fams
        if grps:
            # First pass: the families whose members could match at all --
            # for each group, the union over its terms of "the term occurs
            # somewhere in this family's member paths"; AND over groups. A
            # backslash term (LIKE-escape semantics) cannot be prefiltered by
            # a plain substring and keeps every family.
            cand: set[int] | None = None
            blobs = self._blobs
            for g in grps:
                hit: set[int] = set()
                for t in g:
                    if t.sub is None:
                        hit = set(range(len(fams)))
                        break
                    sub = t.sub
                    hit.update(i for i, b in enumerate(blobs) if sub in b)
                cand = hit if cand is None else (cand & hit)
                if not cand:
                    return []
            order = sorted(cand)
        else:
            order = range(len(fams))
        out = []
        for i in order:
            f = fams[i]
            if grps:
                whole = all(any(t.sub is not None and (t.sub in f.tail_l or t.sub in f.scope_l)
                                for t in g) for g in grps)
                if whole:
                    n, changes = f.n, f.changes     # every member contains every group
                else:
                    ents: set[str] = set()
                    changes = 0
                    hit_any = False
                    for low, raw, ent, ch, _pid in f.members:
                        if all(any(t.hit(low, raw) for t in g) for g in grps):
                            hit_any = True
                            ents.add(ent)
                            changes += ch
                    if not hit_any:
                        continue
                    n = len(ents)
            else:
                n, changes = f.n, f.changes
            out.append((f, n, changes))
        # Ties (equal change count AND equal natural key, e.g. one tail under
        # two scopes) fall back to (scope, tail) -- the SQL's GROUP BY order,
        # which its stable sort keeps -- so the answer never depends on the
        # order families were ADDED in (a derived table appends new ones).
        out.sort(key=lambda t: (-t[2], t[0].sort_key, t[0].scope, t[0].tail))
        if limit:
            out = out[:int(limit)]
        return [{"path": (f"{f.scope}.*.{f.tail}" if f.scope else f.tail),
                 "label": f.tail, "scope": f.scope, "n": int(n),
                 "changes": int(changes)} for f, n, changes in out]


class _Marks:
    """Where an index read stood: every change point up to ``snap_max`` and
    every path up to ``paths_max``, with additive fingerprints of both
    prefixes. A later read DERIVES from a base only if both fingerprints are
    unchanged -- i.e. nothing below the marks was deleted, moved to another
    path, or rewritten (a rebuild renumbers from scratch, a re-stamp deletes a
    snapshot's rows); otherwise it reads everything again.

    ``cp_sig`` = (rows, SUM(path_id), SUM((path_id mod M)^2)) over the change
    points with ``snap_id <= snap_max`` -- a fingerprint of the MULTISET of
    path ids, which is exactly what the per-path change counts are. A value
    rewrite in place (same path, same snapshot) does not move it, and does not
    change a count either. ``paths_sig`` = (rows, SUM(id)) over the paths with
    ``id <= paths_max``: a deleted or renumbered path moves it. A path's TEXT
    is not fingerprinted (reading 180k strings cost 55-240 ms per capture):
    ``leaf_paths`` is insert-only by construction -- ``_intern_paths`` is its
    only writer, ``path`` is UNIQUE, and nothing updates or deletes a row.
    """

    __slots__ = ("snap_max", "cp_sig", "paths_max", "paths_sig")

    def __init__(self, snap_max: int, cp_sig: tuple, paths_max: int, paths_sig: tuple):
        self.snap_max, self.cp_sig = int(snap_max), tuple(cp_sig)
        self.paths_max, self.paths_sig = int(paths_max), tuple(paths_sig)

    def __eq__(self, other: Any) -> bool:
        return (isinstance(other, _Marks) and other.snap_max == self.snap_max
                and other.cp_sig == self.cp_sig and other.paths_max == self.paths_max
                and other.paths_sig == self.paths_sig)

    def __hash__(self) -> int:
        return hash((self.snap_max, self.cp_sig, self.paths_max, self.paths_sig))


_SIG_MOD = 1048573
_CP_SIG = ("SELECT COUNT(*), COALESCE(SUM(path_id), 0), "
           f"COALESCE(SUM((path_id % {_SIG_MOD}) * (path_id % {_SIG_MOD})), 0) "
           "FROM leaf_cp WHERE snap_id > ? AND snap_id <= ?")
_PATHS_SIG = ("SELECT COUNT(*), COALESCE(SUM(id), 0) FROM leaf_paths "
              "WHERE id > ? AND id <= ?")
_NO_ID = -(1 << 62)


def _add(a: tuple, b: tuple) -> tuple:
    return tuple(x + y for x, y in zip(a, b))


def _heads(conn: sqlite3.Connection) -> tuple[int, int]:
    """(highest snapshot id seen in leaf_snaps OR leaf_cp, highest path id)."""
    snap_max = conn.execute(
        "SELECT MAX(m) FROM (SELECT MAX(id) AS m FROM leaf_snaps "
        "UNION ALL SELECT MAX(snap_id) FROM leaf_cp)").fetchone()[0]
    paths_max = conn.execute("SELECT MAX(id) FROM leaf_paths").fetchone()[0]
    return (_NO_ID if snap_max is None else int(snap_max),
            _NO_ID if paths_max is None else int(paths_max))


class _ReadTxn:
    """One WAL read transaction: every statement inside sees ONE commit, so
    the rows and the marks that describe them can never disagree."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def __enter__(self):
        self.conn.execute("BEGIN")
        return self.conn

    def __exit__(self, *exc):
        try:
            self.conn.execute("COMMIT")
        except sqlite3.Error:
            pass
        return False


def _family_rows_by_row(conn: sqlite3.Connection) -> list[tuple[str, int, str, int]]:
    counts = dict(conn.execute("SELECT path_id, COUNT(*) FROM leaf_cp GROUP BY path_id"))
    return [(path, counts.get(pid, 0), _alower(path), pid)
            for pid, path in conn.execute("SELECT id, path FROM leaf_paths ORDER BY id")]


def _family_rows_marked(conn: sqlite3.Connection) -> tuple[list[tuple[str, int, str, int]], _Marks]:
    """``(path, change points, ascii-lowered path, path id)`` for every
    indexed path, in id order -- the same numbers as ``LEFT JOIN leaf_cp ...
    GROUP BY p.id`` -- plus the marks they were read at.

    Read as a handful of ONE-ROW aggregates (``group_concat``) instead of one
    fetched row per path: sqlite3 releases the GIL around every row it
    steps, and with the page's other requests busy (live-diff, drift) each
    re-acquire waited its turn -- 180k rows took 16-22 s in the browser for
    what costs 0.8 s alone. A separator that also occurs inside a path shows
    up as a piece-count mismatch and falls back to the row-by-row read.
    """
    with _ReadTxn(conn):
        snap_max, paths_max = _heads(conn)
        cp_sig = conn.execute(_CP_SIG, (_NO_ID, snap_max)).fetchone()
        paths_sig = conn.execute(_PATHS_SIG, (_NO_ID, paths_max)).fetchone()
        marks = _Marks(snap_max, cp_sig, paths_max, paths_sig)
        ids_s, paths_s, n = conn.execute(
            "SELECT group_concat(id, ' ' ORDER BY id), "
            "       group_concat(path, char(30) ORDER BY id), COUNT(*) FROM leaf_paths").fetchone()
        cid_s, cn_s = conn.execute(
            "SELECT group_concat(path_id, ' '), group_concat(n, ' ') FROM "
            "(SELECT path_id, COUNT(*) AS n FROM leaf_cp GROUP BY path_id)").fetchone()
        if not n:
            return [], marks
        ids = list(map(int, ids_s.split(" ")))
        paths = paths_s.split("\x1e")
        if len(ids) != n or len(paths) != n:
            return _family_rows_by_row(conn), marks
        counts = dict(zip(map(int, cid_s.split(" ")), map(int, cn_s.split(" ")))) if cid_s else {}
    lows = paths_s.lower().split("\x1e") if paths_s.isascii() else [_alower(x) for x in paths]
    if len(lows) != n:
        lows = [_alower(x) for x in paths]
    return list(zip(paths, [counts.get(i, 0) for i in ids], lows, ids)), marks


def _family_rows(conn: sqlite3.Connection) -> list[tuple[str, int, str, int]]:
    """The rows alone (tests; the pins compare them with the SQL)."""
    return _family_rows_marked(conn)[0]


class _Delta:
    __slots__ = ("added", "new_rows", "marks")

    def __init__(self, added, new_rows, marks):
        self.added, self.new_rows, self.marks = added, new_rows, marks

    def __eq__(self, other: Any) -> bool:
        return (isinstance(other, _Delta) and other.added == self.added
                and other.new_rows == self.new_rows and other.marks == self.marks)

    __hash__ = None  # type: ignore[assignment]


# Past this many changed paths a derive is no cheaper than a full read.
_DELTA_MAX = 20_000


def _delta_read(conn: sqlite3.Connection, base: _Marks) -> _Delta | None:
    """What was APPENDED to the index since *base*, or None when anything
    below *base*'s marks changed (the caller then reads everything)."""
    with _ReadTxn(conn):
        if tuple(conn.execute(_CP_SIG, (_NO_ID, base.snap_max)).fetchone()) != base.cp_sig:
            return None
        if tuple(conn.execute(_PATHS_SIG, (_NO_ID, base.paths_max)).fetchone()) != base.paths_sig:
            return None
        snap_max, paths_max = _heads(conn)
        snap_max, paths_max = max(snap_max, base.snap_max), max(paths_max, base.paths_max)
        # INDEXED BY: left to itself the planner walks the primary key for
        # the DISTINCT/GROUP BY (20-85 ms on 180k rows for an EMPTY delta);
        # a missing index raises, and a failed delta is a full read.
        n_add = conn.execute(
            "SELECT COUNT(*) FROM (SELECT DISTINCT path_id FROM leaf_cp "
            "INDEXED BY idx_leaf_cp_snap WHERE snap_id > ?)", (base.snap_max,)).fetchone()[0]
        n_new = conn.execute("SELECT COUNT(*) FROM leaf_paths WHERE id > ?",
                             (base.paths_max,)).fetchone()[0]
        if n_add + n_new > _DELTA_MAX:
            return None
        added = {pid: (path, int(k)) for pid, path, k in conn.execute(
            "SELECT l.path_id, p.path, COUNT(*) FROM leaf_cp l INDEXED BY idx_leaf_cp_snap "
            "  JOIN leaf_paths p ON p.id = l.path_id "
            " WHERE l.snap_id > ? AND l.path_id <= ? GROUP BY l.path_id",
            (base.snap_max, base.paths_max))}
        new_rows = [(path, int(k), _alower(path), pid) for pid, path, k in conn.execute(
            "SELECT p.id, p.path, (SELECT COUNT(*) FROM leaf_cp l WHERE l.path_id = p.id) "
            "  FROM leaf_paths p WHERE p.id > ? ORDER BY p.id", (base.paths_max,))]
        cp_sig = _add(base.cp_sig, conn.execute(_CP_SIG, (base.snap_max, snap_max)).fetchone())
        paths_sig = _add(base.paths_sig,
                         conn.execute(_PATHS_SIG, (base.paths_max, paths_max)).fetchone())
    return _Delta(added, new_rows, _Marks(snap_max, cp_sig, paths_max, paths_sig))


class _Base:
    """What a table hands the table built for the NEXT token: its family
    tables and matching-path lists, each with the marks it is exact at."""

    __slots__ = ("fams", "matching", "depth")

    def __init__(self, fams, matching, depth):
        self.fams, self.matching, self.depth = fams, matching, depth


# How each family table / matching list was produced (tests, the bench).
COUNTS = {"full": 0, "derive": 0, "reuse": 0,
          "full_no_base": 0, "full_prefix_changed": 0, "full_refused": 0}


# A chain of derives is re-anchored on a full read this often (belt and
# braces: a derive is pinned equal to a full build, this bounds any drift).
_MAX_DEPTH = 64


def _unpack(res: Any) -> tuple[str, bool]:
    """*render* returns the html, or ``(html, keep)`` -- ``keep`` False for a
    response that read something outside the token (served, never kept)."""
    return res if isinstance(res, tuple) else (res, True)


# ── the per-token table ──────────────────────────────────────────────────────

def _verify_on() -> bool:
    return os.environ.get("SM_RAM_VERIFY", "") not in ("", "0")


class ChipTrendsTable:
    """Everything Trends derives from one history token. See module doc."""

    def __init__(self, hm, quam_state_path: str | Path, hist_dir: Path | None,
                 snaps: list, ic: _IndexConn | None):
        self.hm = hm
        self.path = Path(quam_state_path)
        self.hist_dir = hist_dir
        self.snaps = snaps
        self.ic = ic
        self._parts: dict[Any, Any] = {}
        self._series: dict[tuple[str, bool], list[tuple] | None] = {}
        self._lock = threading.RLock()
        self._fresh_at: float | None = None
        self._frags: "OrderedDict[Any, str]" = OrderedDict()
        self._inflight: dict[Any, threading.Event] = {}
        self.token: tuple | None = None
        self._base: _Base | None = None
        self._depth = 0

    # A fixed estimate: parts fill lazily after insertion, and the memo holds
    # at most a few tables (one per open chip).
    def ram_bytes(self) -> int:
        return 4_000_000 + 400 * len(self.snaps or ())

    def __eq__(self, other: Any) -> bool:
        # ramcache's shadow check compares a table with a freshly built one
        # for the SAME token: equal sources make equal tables. Part-level
        # values are verified separately (see part()).
        return (isinstance(other, ChipTrendsTable) and other.hist_dir == self.hist_dir
                and other.snaps is self.snaps and other.ic is self.ic)

    __hash__ = object.__hash__

    # -- generic part memo ---------------------------------------------------
    def part(self, key: Any, compute: Callable[[], Any],
             cold: Callable[[], Any] | None = None) -> Any:
        """*key*'s value, computed at most once per table. Parts compute
        CONCURRENTLY (one in-flight event per key, not one lock for the
        table): a request needing the family table no longer queues behind
        the pre-warm thread computing the provenance map. A waiter whose
        owner failed computes the part itself."""
        while True:
            with self._lock:
                if key in self._parts:
                    val = self._parts[key]
                    break
                ev = self._inflight.get(key)
                if ev is None:
                    ev = self._inflight[key] = threading.Event()
                    owner = True
                else:
                    owner = False
            if not owner:
                ev.wait()
                with self._lock:
                    if key in self._parts:
                        return self._parts[key]
                continue              # the owner raised: try (and own) it again
            try:
                val = compute()
                with self._lock:
                    self._parts[key] = val
            finally:
                with self._lock:
                    self._inflight.pop(key, None)
                ev.set()
            return val
        if _verify_on():
            chk = (cold or compute)()
            if chk != val:
                raise ramcache.StaleCacheError(
                    f"chip_trends: part {key!r} differs from a cold recompute")
        return val

    _MAX_FRAGMENTS = 8

    def fragment(self, key: Any, render: Callable[[], Any]) -> str:
        """A rendered response for *key*, rendered at most once per table.

        Bounded LRU (a toggle sequence visits many selections; each fragment
        can be a few hundred KB). Rendered OUTSIDE the table lock -- two
        concurrent first renders of one key both render, which is only
        redundant work. Shadow mode re-renders every hit and compares.
        """
        with self._lock:
            hit = self._frags.get(key)
            if hit is not None:
                self._frags.move_to_end(key)
        if hit is not None:
            if _verify_on() and _unpack(render())[0] != hit:
                raise ramcache.StaleCacheError(
                    "chip_trends: a memoized Trends fragment differs from a fresh render")
            return hit
        out, keep = _unpack(render())
        if not keep:
            return out
        with self._lock:
            self._frags[key] = out
            while len(self._frags) > self._MAX_FRAGMENTS:
                self._frags.popitem(last=False)
        return out

    # -- freshness: once per table, and only for a LEAF-tier read ------------
    _FRESH_RETRY_S = 60.0

    def _ensure_fresh(self) -> None:
        """The leaf freshness gate (docs/208: it only ever SCHEDULES a
        background repair). Run once per table -- i.e. once per history token
        -- and again at most every ``_FRESH_RETRY_S`` (the gate's own retry
        window for a repair that failed), instead of once per request; the
        curated-only render keeps never calling it
        (TestTheReadStaysOffTheIndexWriteLock)."""
        now = time.monotonic()
        with self._lock:
            if self._fresh_at is not None and now - self._fresh_at < self._FRESH_RETRY_S:
                return
            self._fresh_at = now
        try:
            self.hm._ensure_leaf_index_fresh(self.path)
        except Exception:  # noqa: BLE001 - a scheduling failure must not 500 a read
            logger.debug("leaf freshness gate failed", exc_info=True)

    # -- the parts -----------------------------------------------------------
    def snapshot_count(self) -> int:
        return len(self.snaps or ())

    def provenance_rows(self) -> list[dict]:
        return self.part(("prov",), lambda: self.hm.snapshot_provenance(self.path))

    def curated(self, props: tuple[str, ...], downsample: int | None,
                compress: str | None) -> list[dict]:
        def go():
            try:
                return self.hm.extract_property_history(
                    self.path, list(props), downsample=downsample, compress=compress)
            except Exception:  # noqa: BLE001
                logger.debug("curated trend read failed", exc_info=True)
                return None
        return self.part(("curated", tuple(props), downsample, compress), go)

    def _full(self) -> tuple[list, _Marks] | None:
        def go():
            try:
                run = getattr(self.ic, "run_detached", None) or self.ic.run
                return run(_family_rows_marked)
            except sqlite3.Error:
                logger.debug("family table read failed", exc_info=True)
                return None
        return self.part(("family_rows",), go)

    def _rows(self) -> list[tuple[str, int, str, int]] | None:
        full = self._full()
        return None if full is None else full[0]

    def _delta(self, marks: _Marks) -> _Delta | None:
        """What the index appended since *marks* (one read per table and
        base marks), or None: derive nothing, read everything."""
        if self._depth >= _MAX_DEPTH:
            return None

        def go():
            try:
                return self.ic.run(lambda c: _delta_read(c, marks))
            except sqlite3.Error:
                logger.debug("family delta read failed", exc_info=True)
                return None
        return self.part(("delta", marks), go)

    _BASE_KEYS = 16

    def export_base(self) -> _Base | None:
        """The parts the next token's table may derive from (never the
        table itself, so no chain of old tables is kept alive). A part this
        table never computed is handed on from ITS base, with the older
        marks it is exact at: the badge row asks only for the pair table on
        a new token, and the typeahead's full table must not be lost to that
        (it was: a full 180k-path read on the next token). At most
        ``_BASE_KEYS`` of each kind, own parts first."""
        with self._lock:
            fams = {k[1:]: v for k, v in self._parts.items()
                    if k[0] == "families_m" and v is not None}
            matching = {k[1]: v for k, v in self._parts.items()
                        if k[0] == "matching_m" and v is not None}
        inherited = self._base
        if inherited is not None:
            for k, v in inherited.fams.items():
                if len(fams) >= self._BASE_KEYS:
                    break
                fams.setdefault(k, v)
            for k, v in inherited.matching.items():
                if len(matching) >= self._BASE_KEYS:
                    break
                matching.setdefault(k, v)
        if not fams and not matching:
            return None
        return _Base(fams, matching, self._depth)

    def _families_marked(self, roots: tuple[str, ...], term: str | None):
        def go():
            base = self._base.fams.get((roots, term)) if self._base else None
            if base is None:
                COUNTS["full_no_base"] += 1
            else:
                old_ft, old_marks = base
                d = self._delta(old_marks)
                if d is None:
                    COUNTS["full_prefix_changed"] += 1
                else:
                    if not d.added and not d.new_rows:
                        COUNTS["reuse"] += 1
                        return old_ft, d.marks
                    try:
                        ft = old_ft.derive(d.added, d.new_rows)
                    except LookupError:
                        COUNTS["full_refused"] += 1
                        logger.debug("family derive refused; full read", exc_info=True)
                    else:
                        if _verify_on():
                            self._check_derived(ft, roots, term)
                        COUNTS["derive"] += 1
                        return ft, d.marks
            full = self._full()
            if full is None:
                return None
            r, marks = full
            if term is not None:
                r = [x for x in r if term in x[2]]
            COUNTS["full"] += 1
            return FamilyTable(r, roots, term), marks
        return self.part(("families_m", tuple(roots), term), go)

    def _check_derived(self, ft: "FamilyTable", roots, term) -> None:
        r, _m = self.ic.run(_family_rows_marked)
        if term is not None:
            r = [x for x in r if term in x[2]]
        if FamilyTable(r, roots, term) != ft:
            raise ramcache.StaleCacheError("chip_trends: a derived family table differs "
                                           "from a full build")

    def families(self, roots: tuple[str, ...], term: str | None = None) -> FamilyTable | None:
        """The family table over every indexed path -- or, with *term*, over
        only the paths that contain it (ASCII case-insensitive, the LIKE
        rule). A query is a filter applied BEFORE grouping in
        ``path_families``, so a table built from the pre-filtered rows answers
        any query that includes that term exactly.

        After a capture the table is DERIVED from the previous token's one
        (``FamilyTable.derive`` over ``_delta_read``) when the index only
        grew; anything else reads everything again."""
        if self.ic is None:
            return None
        t = None if term is None else _alower(term)
        got = self._families_marked(tuple(roots), t)
        return None if got is None else got[0]

    def leaf_families(self, query: str = "", *,
                      roots: tuple[str, ...] = ("qubits", "qubit_pairs"),
                      limit: int | None = None, fresh: bool = False) -> list[dict]:
        """Drop-in for ``HistoryManager.leaf_families`` (same rows, same order)."""
        from quam_state_manager.core.search_query import groups as _sq_groups

        if fresh:
            self._ensure_fresh()
        term = None
        if not fresh:
            # A fixed single-term query (the badge row's "qubit_pairs", a
            # typed-leaf check) gets its own pre-filtered table; the typeahead
            # (fresh) asks a new term per keystroke and uses the full one.
            g = _sq_groups(query or "")
            if len(g) == 1 and len(g[0]) == 1 and "\\" not in g[0][0]:
                term = g[0][0]
        ft = self.families(tuple(roots), term)
        if ft is None:
            return self.hm.leaf_families(self.path, query, roots=roots,
                                         limit=limit, fresh=False)
        out = ft.query(query, limit=limit)
        if _verify_on():
            cold = self.hm.leaf_families(self.path, query, roots=roots,
                                         limit=limit, fresh=False)
            if cold != out:
                raise ramcache.StaleCacheError("chip_trends: families differ from SQL")
        return out

    def leaf_matching_paths(self, pattern: str) -> list[str]:
        """Twin of ``leaf_index.matching_paths``: whole-segment match with
        ``*`` wildcards, in ``ORDER BY path`` order (BINARY collation over
        UTF-8 = code-point order = Python's str order)."""
        if self.ic is None:
            return self.hm.leaf_matching_paths(self.path, pattern)

        parts = pattern.split(".")
        n = len(parts)
        fixed = [(i, a) for i, a in enumerate(parts) if a != "*"]
        need = [a for _, a in fixed]      # a cheap substring pre-test before any split

        def hit(path: str) -> bool:
            if not all(a in path for a in need):
                return False
            ps = path.split(".")
            return len(ps) == n and all(ps[i] == a for i, a in fixed)

        def go():
            base = self._base.matching.get(pattern) if self._base else None
            if base is not None:
                old, old_marks = base
                d = self._delta(old_marks)
                if d is not None:
                    add = [row[0] for row in d.new_rows if hit(row[0])]
                    return (sorted(old + add) if add else old), d.marks
            full = self._full()
            if full is None:
                return None
            r, marks = full
            return sorted(path for path, _c, _l, _p in r if hit(path)), marks

        def unmarked():
            got = self.part(("matching_m", pattern), go)
            return None if got is None else got[0]
        out = unmarked()
        if out is None:
            return self.hm.leaf_matching_paths(self.path, pattern)
        if _verify_on() and out != self.hm.leaf_matching_paths(self.path, pattern):
            raise ramcache.StaleCacheError("chip_trends: matching paths differ from SQL")
        return list(out)

    def leaf_series_many(self, dot_paths: list[str], *,
                         hold_to_newest: bool = False) -> dict[str, list[tuple]]:
        """Drop-in for ``HistoryManager.leaf_field_series_many``."""
        from quam_state_manager.core.history import leaf_series_on

        self._ensure_fresh()
        if self.ic is None:
            return self.hm.leaf_field_series_many(self.path, dot_paths,
                                                  hold_to_newest=hold_to_newest)
        with self._lock:
            missing = [dp for dp in dot_paths
                       if (dp, hold_to_newest) not in self._series]
        if missing:
            try:
                got = self.ic.run(lambda c: leaf_series_on(
                    c, missing, hold_to_newest=hold_to_newest))
            except sqlite3.Error:
                logger.debug("leaf series read failed", exc_info=True)
                return self.hm.leaf_field_series_many(
                    self.path, dot_paths, hold_to_newest=hold_to_newest)
            with self._lock:
                for dp in missing:
                    self._series[(dp, hold_to_newest)] = got.get(dp)
        with self._lock:
            out = {dp: list(self._series[(dp, hold_to_newest)])
                   for dp in dot_paths
                   if self._series.get((dp, hold_to_newest))}
        if _verify_on():
            cold = self.hm.leaf_field_series_many(self.path, dot_paths,
                                                  hold_to_newest=hold_to_newest)
            if cold != out:
                raise ramcache.StaleCacheError("chip_trends: leaf series differ from cold")
        return out

    def leaf_series(self, dot_path: str) -> list[tuple] | None:
        """Drop-in for ``HistoryManager.leaf_field_series`` (no held point)."""
        got = self.leaf_series_many([dot_path], hold_to_newest=False)
        return got.get(dot_path) or None

    def index_updating(self) -> bool:
        """NOT cached: whether a background repair runs is a thread's liveness."""
        if self.hist_dir is None:
            return self.hm.leaf_index_updating(self.path)
        return self.hm.leaf_index_updating_dir(self.hist_dir)


WARM_FULL_TABLE = True


def _warm_full(t: "ChipTrendsTable") -> None:
    try:
        t.families(("qubits", "qubit_pairs"), None)
    except Exception:  # noqa: BLE001 - a warm-up must never surface
        logger.debug("trends family warm-up failed", exc_info=True)


_MEMO = ramcache.KeyedMemo("chip_trends", max_entries=4,
                           sizeof=lambda t: t.ram_bytes())


def table(hm, quam_state_path: str | Path) -> ChipTrendsTable:
    """The Trends table for this chip at its CURRENT history token.

    A chip with no history dir yet gets an uncached table (every part goes
    through the cold path), so the first capture is never hidden behind a
    table built before it existed.
    """
    tok = token(hm, quam_state_path)
    if tok is None:
        return ChipTrendsTable(hm, quam_state_path, None,
                               hm.list_snapshots(quam_state_path), None)
    def build(prev):
        # Another CONNECTION committed (data_version moved) while nothing in
        # this process bumped the chip version: a second SM process, or a
        # writer that bypassed the capture paths. HistoryManager's own caches
        # (the extract LRU, the snapshot list) are keyed on that version only,
        # so without a bump every part computed below would read THEIR stale
        # answer. Bump once; the token re-read below includes it.
        if (prev is not None and prev.token is not None
                and prev.token[2] == tok[2] and prev.token[5] != tok[5]):
            hm._bump_chip_version(hist_dir)
        # The curated read's own freshness steps WRITE to index.sqlite (the
        # one-time schema stamp, the change-point companion's incremental
        # rows), which moves data_version. Run them FIRST and re-read the
        # token, so the table is stored under a token that already includes
        # its own writes -- otherwise every new history token cost two
        # builds back to back (measured 4.1 s, then 2.3 s). Nothing computed
        # here is kept: every part is computed after the token it is served
        # under was read.
        from quam_state_manager.core.history import DEFAULT_TRACKED_PROPERTIES
        try:
            hm.extract_property_history(quam_state_path, list(DEFAULT_TRACKED_PROPERTIES),
                                        downsample=None, compress="changes")
        except Exception:  # noqa: BLE001 - priming only
            logger.debug("trends prime failed", exc_info=True)
        tok2 = token(hm, quam_state_path) or tok
        t = ChipTrendsTable(hm, quam_state_path, Path(tok2[0]), tok2[3].obj, tok2[4].obj)
        t.token = tok2
        if (prev is not None and prev.hist_dir is not None and t.hist_dir == prev.hist_dir
                and prev.ic is t.ic and t.ic is not None):
            # The SAME connection: its path ids are the same index's. A new
            # connection (index replaced, reopened) starts from scratch.
            t._base = prev.export_base()
            t._depth = (t._base.depth + 1) if t._base is not None else 0
        elif prev is None and t.ic is not None and WARM_FULL_TABLE:
            # The chip's first table in this process: build the typeahead's
            # whole-index family table OFF the request path (1.5 s of Python
            # on a 180k-path index), so the first keystroke finds it -- and
            # every later token derives it instead of re-reading.
            threading.Thread(target=_warm_full, args=(t,), daemon=True,
                             name="trends-family-warm").start()
        return ramcache.Keyed(t, tok2)
    # One slot per (chip history dir, HistoryManager): two managers over one
    # instance (tests; never two in one app) must not evict each other.
    hist_dir = Path(tok[0])
    return _MEMO.get((tok[0], id(hm)), tok, build, wait_s=30.0, incremental=True)


def is_open(hm, quam_state_path: str | Path) -> bool:
    """Whether a Trends table was built for this chip in this process -- the
    gate for background pre-warming (a chip nobody opened Trends for is not
    worth the CPU)."""
    mine = [s for s in _MEMO.slots() if isinstance(s, tuple) and s[1] == id(hm)]
    if not mine:                     # the common case: resolve nothing at all
        return False
    hist_dir = hm.history_dir_cached(quam_state_path)
    if hist_dir is None:
        return False
    return (str(hist_dir), id(hm)) in mine


def forget(hm) -> int:
    """Drop every table built for *hm* (tests: a cold oracle)."""
    return _MEMO.drop_where(lambda slot: isinstance(slot, tuple) and slot[1] == id(hm))


def stats() -> dict[str, Any]:
    return _MEMO.stats()
