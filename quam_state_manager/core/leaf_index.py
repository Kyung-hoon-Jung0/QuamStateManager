"""Every numeric parameter's history, stored as CHANGE POINTS (docs/83).

Param History has always tracked a curated list — T1, T2ramsey, f_01, eleven
properties in all (``history.DEFAULT_TRACKED_PROPERTIES``). Everything else was
answerable only by ``field_history``'s fallback tier, which re-parses snapshot
``state.json`` files newest-first and gives up after 150 of them. Measured on a
real 264-snapshot chip: **555 ms and truncated** for one untracked leaf, versus
2.6 ms and complete for a tracked one.

The obvious fix — index every numeric leaf of every snapshot — does not work.
Measured on real chips: 8,000-odd numeric leaves × 111 snapshots = 887k rows,
and at the ~235 bytes/row the existing dense index actually costs on disk that
is 200 MB for one chip (2.2M rows / 500 MB for another).

What makes it work is one measured fact about calibration data:

    between consecutive snapshots of a real chip, the number of numeric
    leaves that CHANGE has a median of 2 to 4 — out of 8,000.

A calibration run rewrites almost nothing. So this module stores only the
transitions, and the whole-chip history becomes SMALLER than the eleven-property
index it sits beside:

    chip          snapshots   numeric paths   change points   file    (old index)
    KRISS               111           8,721           9,410   1.45 MB      6.1 MB
    17Q                 264           9,018          12,374   1.59 MB     13.4 MB
    Novera_1Q         1,154           1,675          10,104   0.61 MB     26.9 MB

    one path's full history: 0.01-0.06 ms      full rebuild: 0.9-2.7 s

Design
------

**Three tables, in the chip's existing ``index.sqlite``.** ``snaps`` and
``paths`` hold the repeated strings once each; ``leaf_cp`` is then three
integers wide. The file is already per-chip, WAL, and routed through the chip
identity ladder — a second file would have to re-derive all of that.

**Its own version marker.** ``PRAGMA user_version`` belongs to the older
``param_history`` schema and drives its upgrade path; this module keeps
``leaf_meta`` instead so the two evolve independently and an upgrade here can
never trigger a pair-row re-ingest there.

**Ordering is not repaired incrementally — it is rebuilt.** A change point is
defined against the previous snapshot, so a snapshot arriving out of order
(backfill importing an older run after a newer one) invalidates the neighbours.
Rather than an incremental repair algorithm, such an arrival is *not written*
and the index is marked dirty; the next read rebuilds. A rebuild costs 0.9-2.7 s
on real chips, which is what makes the simple answer the right one.

**A rebuild MERGES — it never deletes what disk no longer has.** Snapshots are
pruned; their rows must survive, exactly as the old index's do (its docstring
promises "survives snapshot pruning"). So a rebuild recomputes only timestamps
whose snapshot dir still exists and replays the retained rows in timestamp
order, which both keeps them and rebuilds the running "previous value". Without
that replay the oldest SURVIVING snapshot would report all 8,000 of its leaves
as having just changed.

**Pointers are followed.** QUAM avoids duplicating values by storing pointers,
so on a real chip 1,000-2,300 leaves per snapshot are pointer strings and
570-1,190 of those resolve to numbers — on a 1Q chip as many parameters again
as the direct ones, and they are exactly the fields a user clicks
(``xy.operations.x180.amplitude``). They are resolved at ingest (3-7 ms per
snapshot) and stored as their number, which is also what the scan tier returns,
so the two agree. A pointer that resolves to nothing numeric (self-reference,
dangling, non-numeric target) is recorded as ``KIND_PTR`` with no value, and a
path that ever hit that state is handed back to the scan, which can at least
show the raw pointer string.

Not indexed, deliberately: booleans (``True`` is not a parameter) and strings.
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Any, Iterable

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# Row kinds. A change point records what a leaf BECAME.
KIND_NUM = 0        # a number stored right here (``value`` is it)
KIND_PTR = 1        # a pointer that did NOT resolve to a number — value NULL
KIND_GONE = 2       # the leaf was a parameter and is now absent
KIND_OTHER = 3      # became a non-numeric value (string / bool / null)
KIND_PTR_NUM = 4    # a pointer whose target resolved to ``value``

# Walk guard. No real chip comes close (largest measured: 8,721 numeric leaves);
# this only stops a pathological state.json from pinning memory.
WALK_CAP = 200_000
# Rows one snapshot may contribute. The first snapshot of a chip legitimately
# emits every leaf; after that only a wholesale replacement (a regenerate)
# approaches four figures (largest measured single-snapshot churn: 2,716).
SNAP_ROW_CAP = 50_000

_POINTER_PREFIXES = ("#/", "#../", "#./")


# ──────────────────────────────────────────────────────────────────────────
# Schema
# ──────────────────────────────────────────────────────────────────────────

# Statements are executed one at a time, never via ``executescript``: in
# autocommit mode that helper issues an implicit COMMIT first, which would end
# a transaction the CALLER opened around a rebuild (measured: it silently broke
# a BEGIN IMMEDIATE and left the rebuild fsyncing once per statement — 9.6 s
# instead of 1.4 s on a 111-snapshot chip).
_SCHEMA = """
CREATE TABLE IF NOT EXISTS leaf_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS leaf_snaps (
    id         INTEGER PRIMARY KEY,
    ts         TEXT NOT NULL UNIQUE,
    trigger    TEXT,
    run_id     INTEGER,
    experiment TEXT,
    folder     TEXT
);
CREATE TABLE IF NOT EXISTS leaf_paths (
    id   INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS leaf_cp (
    path_id INTEGER NOT NULL,
    snap_id INTEGER NOT NULL,
    value   REAL,
    kind    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (path_id, snap_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_leaf_cp_snap ON leaf_cp (snap_id, path_id);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Idempotent CREATEs + version stamp. Safe to call on every open, and
    safe INSIDE a caller's transaction (see the note above ``_SCHEMA``)."""
    for stmt in _SCHEMA.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    if _meta_get(conn, "version") is None:
        _meta_set(conn, "version", str(SCHEMA_VERSION))


def _meta_get(conn: sqlite3.Connection, key: str) -> str | None:
    try:
        row = conn.execute("SELECT value FROM leaf_meta WHERE key=?", (key,)).fetchone()
    except sqlite3.Error:
        return None
    return row[0] if row else None


def _meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO leaf_meta (key, value) VALUES (?, ?)",
                 (key, value))


def is_dirty(conn: sqlite3.Connection) -> bool:
    return _meta_get(conn, "dirty") == "1"


def mark_dirty(conn: sqlite3.Connection, reason: str = "") -> None:
    _meta_set(conn, "dirty", "1")
    if reason:
        _meta_set(conn, "dirty_reason", reason)


def clear_dirty(conn: sqlite3.Connection) -> None:
    _meta_set(conn, "dirty", "0")


# ──────────────────────────────────────────────────────────────────────────
# The walk
# ──────────────────────────────────────────────────────────────────────────


def is_pointer(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(_POINTER_PREFIXES)


def merged_doc(state: Any, wiring: Any = None) -> dict:
    """The document a dot path is resolved against.

    Mirrors ``HistoryManager._scan_field_series`` exactly: state first, wiring
    only for top-level keys state does not already have, so a dot path means
    the same thing in every tier.
    """
    out = dict(state) if isinstance(state, dict) else {}
    if isinstance(wiring, dict):
        for k, v in wiring.items():
            out.setdefault(k, v)
    return out


def numeric_leaves(state: Any, wiring: Any = None, *,
                   cap: int = WALK_CAP,
                   resolve_pointers: bool = True
                   ) -> tuple[dict[str, float], dict[str, int], bool]:
    """``(numbers, kinds, truncated)`` over the merged state+wiring view.

    ``numbers`` maps dot-path → float. ``kinds`` carries the row kind for every
    path worth recording: ``KIND_NUM`` is implied for a plain number and left
    out, ``KIND_PTR_NUM`` marks a value reached through a pointer, and
    ``KIND_PTR`` marks a pointer that resolves to nothing numeric (those have
    no entry in ``numbers``).

    **Pointers are followed.** On real chips 1,000-2,300 leaves per snapshot are
    pointers and 570-1,190 of them resolve to numbers — on a 1Q chip that is as
    many parameters again as the direct ones, and they include exactly the
    fields a user clicks (``xy.operations.x180.amplitude``). Resolving them
    costs 3-7 ms per snapshot, and it is what ``_scan_field_series`` already
    does, so both tiers answer with the same number.

    Booleans are excluded — ``True`` is not a parameter, and Python would
    happily arithmetic it into 1.0.
    """
    numbers: dict[str, float] = {}
    kinds: dict[str, int] = {}
    pointers: dict[str, str] = {}
    truncated = False

    doc = merged_doc(state, wiring)
    stack: list[tuple[str, Any]] = [(str(k), v) for k, v in reversed(list(doc.items()))]
    seen = 0
    while stack:
        prefix, node = stack.pop()
        if isinstance(node, dict):
            for k, v in node.items():
                stack.append((f"{prefix}.{k}" if prefix else str(k), v))
            continue
        if isinstance(node, list):
            for i, v in enumerate(node):
                stack.append((f"{prefix}.{i}", v))
            continue
        seen += 1
        if seen > cap:
            truncated = True
            break
        if isinstance(node, bool):
            continue
        if isinstance(node, (int, float)):
            numbers[prefix] = float(node)
        elif is_pointer(node):
            pointers[prefix] = node

    if pointers and resolve_pointers:
        from quam_state_manager.core.pointer_resolver import (
            is_self_ref, resolve_pointer,
        )
        for path, raw in pointers.items():
            resolved: Any = None
            if not is_self_ref(raw):
                try:
                    resolved = resolve_pointer(doc, raw, tuple(path.split(".")))
                except Exception:       # noqa: BLE001 — a dangling pointer is data
                    resolved = None
            if isinstance(resolved, (int, float)) and not isinstance(resolved, bool):
                numbers[path] = float(resolved)
                kinds[path] = KIND_PTR_NUM
            else:
                kinds[path] = KIND_PTR
    elif pointers:
        for path in pointers:
            kinds[path] = KIND_PTR
    return numbers, kinds, truncated


# ──────────────────────────────────────────────────────────────────────────
# Reading the current state of the index
# ──────────────────────────────────────────────────────────────────────────


def _path_ids(conn: sqlite3.Connection) -> dict[str, int]:
    return {p: i for i, p in conn.execute("SELECT id, path FROM leaf_paths")}


def _intern_paths(conn: sqlite3.Connection, cache: dict[str, int],
                  paths: Iterable[str]) -> None:
    # dict.fromkeys, not a set: callers pass ``list(numbers) + list(kinds)`` and
    # a pointer-resolved leaf appears in BOTH. Without the dedup the second
    # occurrence claimed a fresh id, the INSERT was IGNOREd on the UNIQUE path,
    # and the cache then pointed at an id that does not exist.
    new = list(dict.fromkeys(p for p in paths if p not in cache))
    if not new:
        return
    nxt = (conn.execute("SELECT COALESCE(MAX(id), -1) FROM leaf_paths").fetchone()[0]) + 1
    rows = []
    for p in new:
        cache[p] = nxt
        rows.append((nxt, p))
        nxt += 1
    conn.executemany("INSERT OR IGNORE INTO leaf_paths (id, path) VALUES (?, ?)", rows)


def _latest_values(conn: sqlite3.Connection) -> dict[int, tuple[float | None, int]]:
    """``{path_id: (value, kind)}`` as of the newest recorded change point —
    i.e. the chip's current value for every path this index knows."""
    return {r[0]: (r[1], r[2]) for r in conn.execute("""
        SELECT l.path_id, l.value, l.kind
          FROM leaf_cp l
          JOIN (SELECT path_id, MAX(snap_id) AS m FROM leaf_cp GROUP BY path_id) t
            ON t.path_id = l.path_id AND t.m = l.snap_id
    """)}


def _max_ts(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT MAX(ts) FROM leaf_snaps").fetchone()
    return row[0] if row and row[0] else None


def _next_snap_id(conn: sqlite3.Connection) -> int:
    return (conn.execute("SELECT COALESCE(MAX(id), -1) FROM leaf_snaps").fetchone()[0]) + 1


# ──────────────────────────────────────────────────────────────────────────
# Writing
# ──────────────────────────────────────────────────────────────────────────


def _diff_rows(prev: dict[int, tuple[float | None, int]],
               path_ids: dict[str, int],
               numbers: dict[str, float],
               kinds: dict[str, int],
               snap_id: int) -> list[tuple]:
    """The change points this snapshot contributes, against ``prev``."""
    rows: list[tuple] = []
    present: set[int] = set()
    for path, val in numbers.items():
        pid = path_ids[path]
        present.add(pid)
        kind = kinds.get(path, KIND_NUM)
        was = prev.get(pid)
        if was is not None and was[1] == kind and was[0] == val:
            continue
        rows.append((pid, snap_id, val, kind))
    for path, kind in kinds.items():
        if path in numbers:
            continue
        pid = path_ids[path]
        present.add(pid)
        was = prev.get(pid)
        if was is not None and was[1] == kind:
            continue
        rows.append((pid, snap_id, None, kind))
    # Leaves that were something and are now nothing. A disappearing parameter
    # is a change a history must be able to show.
    for pid, (_v, kind) in prev.items():
        if pid in present or kind == KIND_GONE:
            continue
        rows.append((pid, snap_id, None, KIND_GONE))
    return rows


def ingest_snapshot(conn: sqlite3.Connection, *, ts: str, trigger: str | None,
                    run_id: int | None, experiment: str | None,
                    folder: str | None, state: Any, wiring: Any = None) -> dict:
    """Append one snapshot's change points. Returns a small status dict.

    ``{"status": "appended"|"known"|"dirty"|"skipped", "rows": n}``.

    Out-of-order arrival marks the index dirty and writes NOTHING: a change
    point computed against the wrong neighbour is a wrong answer, and the
    rebuild that repairs it is cheap. ``known`` = this timestamp is already in,
    which is the normal no-op for a re-ingest.
    """
    ensure_schema(conn)
    row = conn.execute("SELECT id FROM leaf_snaps WHERE ts=?", (ts,)).fetchone()
    if row is not None:
        return {"status": "known", "rows": 0}

    newest = _max_ts(conn)
    if newest is not None and ts < newest:
        mark_dirty(conn, f"out-of-order {ts} < {newest}")
        return {"status": "dirty", "rows": 0}

    numbers, kinds, truncated = numeric_leaves(state, wiring)
    path_cache = _path_ids(conn)
    _intern_paths(conn, path_cache, list(numbers) + list(kinds))
    prev = _latest_values(conn)
    snap_id = _next_snap_id(conn)
    rows = _diff_rows(prev, path_cache, numbers, kinds, snap_id)
    if len(rows) > SNAP_ROW_CAP:
        rows = rows[:SNAP_ROW_CAP]
        truncated = True
    conn.execute(
        "INSERT INTO leaf_snaps (id, ts, trigger, run_id, experiment, folder) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (snap_id, ts, trigger, run_id, experiment, folder))
    if rows:
        conn.executemany(
            "INSERT OR REPLACE INTO leaf_cp (path_id, snap_id, value, kind) "
            "VALUES (?, ?, ?, ?)", rows)
    if truncated:
        _meta_set(conn, "truncated", "1")
    return {"status": "appended", "rows": len(rows)}


def rebuild(conn: sqlite3.Connection, *, timestamps: Iterable[str],
            load: Any) -> dict:
    """Recompute change points for ``timestamps``; keep every other row.

    ``load(ts)`` returns ``(meta_dict, state, wiring)`` or None. It is called
    ONE timestamp at a time, in order — a chip with 1,154 snapshots would be
    several GB if they were materialised together.

    Rows for timestamps NOT in ``timestamps`` are kept verbatim: that is what
    lets a pruned snapshot's history survive a rebuild. Replaying them in
    timestamp order also rebuilds the running "previous value", so the oldest
    SURVIVING snapshot reports only what it really changed rather than all
    8,000 leaves it happens to contain.
    """
    ensure_schema(conn)
    supplied_ts = sorted(set(timestamps))
    if not supplied_ts:
        clear_dirty(conn)
        return {"snapshots": 0, "rows": 0, "kept": 0}

    known = {ts: sid for sid, ts in conn.execute("SELECT id, ts FROM leaf_snaps")}
    retained = set(known) - set(supplied_ts)

    # Carry the already-recorded rows across verbatim. Replaying them in
    # timestamp order rebuilds the running "previous value" exactly as it stood,
    # so the oldest SURVIVING snapshot reports only what it really changed —
    # not all 8,000 leaves it happens to contain. Kept for EVERY known
    # timestamp, not just the retained ones, so a snapshot that turns out to be
    # unreadable mid-rebuild keeps the history it already had.
    old_rows_by_ts: dict[str, list[tuple]] = {}
    if known:
        id_to_ts = {sid: ts for ts, sid in known.items()}
        for pid, sid, val, kind in conn.execute(
                "SELECT path_id, snap_id, value, kind FROM leaf_cp"):
            ts = id_to_ts.get(sid)
            if ts is not None:
                old_rows_by_ts.setdefault(ts, []).append((pid, val, kind))
    retained_meta = {r[0]: tuple(r[1:]) for r in conn.execute(
        "SELECT ts, trigger, run_id, experiment, folder FROM leaf_snaps")}

    # Renumber from scratch: snap_id must stay monotonic in ts — every query
    # here (latest value, previous value, feed order) depends on that.
    merged: list[tuple[str, bool]] = (
        [(ts, False) for ts in retained] + [(ts, True) for ts in supplied_ts])
    merged.sort(key=lambda t: t[0])
    prev: dict[int, tuple[float | None, int]] = {}

    conn.execute("DELETE FROM leaf_cp")
    conn.execute("DELETE FROM leaf_snaps")
    path_cache = _path_ids(conn)

    n_rows = 0
    truncated = False
    n_loaded = 0
    for sid, (ts, is_new) in enumerate(merged):
        payload = load(ts) if is_new else None
        if payload is None:                     # retained, or unreadable now
            meta = retained_meta.get(ts) or (None, None, None, None)
            conn.execute(
                "INSERT INTO leaf_snaps (id, ts, trigger, run_id, experiment, folder) "
                "VALUES (?, ?, ?, ?, ?, ?)", (sid, ts, *meta))
            rows = [(pid, sid, val, kind) for pid, val, kind in old_rows_by_ts.get(ts, [])]
            for pid, _s, val, kind in rows:
                prev[pid] = (val, kind)
        else:
            meta, state, wiring = payload
            n_loaded += 1
            numbers, kinds, tr = numeric_leaves(state, wiring)
            truncated = truncated or tr
            _intern_paths(conn, path_cache, list(numbers) + list(kinds))
            rows = _diff_rows(prev, path_cache, numbers, kinds, sid)
            if len(rows) > SNAP_ROW_CAP:
                rows = rows[:SNAP_ROW_CAP]
                truncated = True
            conn.execute(
                "INSERT INTO leaf_snaps (id, ts, trigger, run_id, experiment, folder) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (sid, ts, meta.get("trigger"), meta.get("run_id"),
                 meta.get("experiment"), meta.get("folder")))
            for pid, _s, val, kind in rows:
                prev[pid] = (val, kind)
        if rows:
            conn.executemany(
                "INSERT OR REPLACE INTO leaf_cp (path_id, snap_id, value, kind) "
                "VALUES (?, ?, ?, ?)", rows)
            n_rows += len(rows)
    _meta_set(conn, "truncated", "1" if truncated else "0")
    clear_dirty(conn)
    return {"snapshots": len(merged), "rows": n_rows, "kept": len(retained),
            "loaded": n_loaded}


# ──────────────────────────────────────────────────────────────────────────
# Queries
# ──────────────────────────────────────────────────────────────────────────


def path_needs_scan(conn: sqlite3.Connection, path: str) -> bool:
    """True when this index must decline the path.

    Only ``KIND_PTR`` qualifies: a pointer that resolved to nothing numeric
    (self-reference, dangling, or a non-numeric target). Those are the cases
    where the resolving scan can still show something honest — the raw pointer
    string — and this index cannot. A pointer that DID resolve is stored as its
    number (``KIND_PTR_NUM``) and answered from here like any other parameter.
    """
    row = conn.execute(
        "SELECT 1 FROM leaf_cp l JOIN leaf_paths p ON p.id=l.path_id "
        "WHERE p.path=? AND l.kind=? LIMIT 1", (path, KIND_PTR)).fetchone()
    return row is not None


def series(conn: sqlite3.Connection, path: str) -> list[tuple]:
    """``(ts, value, trigger, run_id, experiment, folder)`` oldest-first —
    exactly the tuple ``HistoryManager.field_history`` collapses. Rows that are
    not a number (pointer / gone / other) carry ``None``, which is what the
    caller already renders for a missing value."""
    return [tuple(r) for r in conn.execute(
        "SELECT s.ts, l.value, s.trigger, s.run_id, s.experiment, s.folder "
        "  FROM leaf_cp l "
        "  JOIN leaf_paths p ON p.id = l.path_id "
        "  JOIN leaf_snaps s ON s.id = l.snap_id "
        " WHERE p.path = ? ORDER BY s.ts", (path,))]


def snapshot_count(conn: sqlite3.Connection) -> int:
    try:
        return conn.execute("SELECT COUNT(*) FROM leaf_snaps").fetchone()[0]
    except sqlite3.Error:
        return 0


def snapshot_timestamps(conn: sqlite3.Connection) -> set[str]:
    """Every timestamp this index holds a row for — ingested or carried.

    ~500 short strings on a big chip; the freshness gate reads it only when
    the count check says the index is behind, never in the steady state.
    """
    try:
        return {r[0] for r in conn.execute("SELECT ts FROM leaf_snaps")}
    except sqlite3.Error:
        return set()


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    """Public read of a ``leaf_meta`` key. The freshness gate's retrigger
    memo lives here so deleting the index file clears it with the index."""
    return _meta_get(conn, key)


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Public write of a ``leaf_meta`` key (autocommit-safe — the manager
    opens its connections with ``isolation_level=None``)."""
    _meta_set(conn, key, value)


def stats(conn: sqlite3.Connection) -> dict:
    try:
        return {
            "snapshots": conn.execute("SELECT COUNT(*) FROM leaf_snaps").fetchone()[0],
            "paths": conn.execute("SELECT COUNT(*) FROM leaf_paths").fetchone()[0],
            "rows": conn.execute("SELECT COUNT(*) FROM leaf_cp").fetchone()[0],
            "dirty": is_dirty(conn),
            "truncated": _meta_get(conn, "truncated") == "1",
            "version": _meta_get(conn, "version"),
        }
    except sqlite3.Error:
        return {"snapshots": 0, "paths": 0, "rows": 0, "dirty": False,
                "truncated": False, "version": None}


def changes_by_snapshot(conn: sqlite3.Connection, *, limit_snaps: int = 20,
                        rows_per_snap: int = 25, prefix: str | None = None,
                        before_ts: str | None = None,
                        at_ts: str | None = None) -> list[dict]:
    """The feed, paged by SNAPSHOT rather than by row.

    A change point never happens alone: a run writes a handful of parameters at
    once, and a regenerate rewrites thousands (2,716 measured on a real chip).
    Paging by row would spend a whole page on one such snapshot and hide every
    other; paging by snapshot always shows the last N *events*, each with its
    true count and the first ``rows_per_snap`` of its rows. ``at_ts`` opens one
    snapshot in full.
    """
    where = [f"l.kind IN ({KIND_NUM}, {KIND_PTR_NUM})"]
    params: list[Any] = []
    if prefix:
        where.append("p.path LIKE ? ESCAPE '\\'")
        params.append(prefix.replace("%", r"\%").replace("_", r"\_") + "%")
    if at_ts:
        where.append("s.ts = ?")
        params.append(at_ts)
    elif before_ts:
        where.append("s.ts < ?")
        params.append(before_ts)
    cond = " AND ".join(where)

    snaps = conn.execute(
        "SELECT s.id, s.ts, s.trigger, s.run_id, s.experiment, s.folder, "
        "       COUNT(*) AS n "
        "  FROM leaf_cp l "
        "  JOIN leaf_paths p ON p.id = l.path_id "
        "  JOIN leaf_snaps s ON s.id = l.snap_id "
        f" WHERE {cond} "
        " GROUP BY s.id ORDER BY s.id DESC LIMIT ?",
        params + [int(limit_snaps)]).fetchall()

    out: list[dict] = []
    for sid, ts, trigger, run_id, experiment, folder, n in snaps:
        rows = conn.execute(
            "SELECT p.path, l.value, l.path_id "
            "  FROM leaf_cp l JOIN leaf_paths p ON p.id = l.path_id "
            f" WHERE l.snap_id = ? AND l.kind IN ({KIND_NUM}, {KIND_PTR_NUM})"
            + (" AND p.path LIKE ? ESCAPE '\\'" if prefix else "")
            + " ORDER BY p.path LIMIT ?",
            ([sid] + ([prefix.replace("%", r"\%").replace("_", r"\_") + "%"]
                      if prefix else []) + [int(rows_per_snap)])).fetchall()
        items = []
        for path, value, pid in rows:
            prev = conn.execute(
                "SELECT value, kind FROM leaf_cp WHERE path_id=? AND snap_id<? "
                "ORDER BY snap_id DESC LIMIT 1", (pid, sid)).fetchone()
            items.append({
                "path": path, "value": value,
                "previous": (prev[0] if prev and prev[1] in (KIND_NUM, KIND_PTR_NUM)
                             else None),
                "is_first": prev is None,
            })
        out.append({
            "timestamp": ts, "trigger": trigger, "run_id": run_id,
            "experiment": experiment, "experiment_folder_path": folder,
            "total": n, "shown": len(items), "rows": items,
        })
    return out


def recent_changes(conn: sqlite3.Connection, *, limit: int = 200,
                   prefix: str | None = None,
                   before_ts: str | None = None) -> list[dict]:
    """Newest change points first — the "what changed" feed.

    Each row carries the value it became AND the value it came from, so the
    caller can render a delta without a second query.
    """
    where = [f"l.kind IN ({KIND_NUM}, {KIND_PTR_NUM})"]
    params: list[Any] = []
    if prefix:
        where.append("p.path LIKE ?")
        params.append(prefix.replace("%", r"\%") + "%")
    if before_ts:
        where.append("s.ts < ?")
        params.append(before_ts)
    params.append(int(limit))
    rows = conn.execute(
        "SELECT p.path, s.ts, l.value, s.trigger, s.run_id, s.experiment, "
        "       s.folder, l.path_id, l.snap_id "
        "  FROM leaf_cp l "
        "  JOIN leaf_paths p ON p.id = l.path_id "
        "  JOIN leaf_snaps s ON s.id = l.snap_id "
        f" WHERE {' AND '.join(where)} "
        " ORDER BY s.id DESC, p.path ASC LIMIT ?", params).fetchall()
    out: list[dict] = []
    for path, ts, value, trigger, run_id, experiment, folder, pid, sid in rows:
        prev = conn.execute(
            "SELECT value, kind FROM leaf_cp WHERE path_id=? AND snap_id<? "
            "ORDER BY snap_id DESC LIMIT 1", (pid, sid)).fetchone()
        out.append({
            "path": path, "timestamp": ts, "value": value,
            "previous": prev[0] if prev and prev[1] in (KIND_NUM, KIND_PTR_NUM) else None,
            "is_first": prev is None,
            "trigger": trigger, "run_id": run_id,
            "experiment": experiment, "experiment_folder_path": folder,
        })
    return out


def search_paths(conn: sqlite3.Connection, query: str, *,
                 limit: int = 50) -> list[dict]:
    """Substring match over indexed paths, with each path's change count —
    the typeahead behind "which parameter do you mean?".

    Shared grammar (docs/96): space = AND, a standalone ``|`` = OR — the WHERE
    clause is (AND over groups) of (OR over ``LIKE`` terms). The old code put
    the WHOLE trimmed query into one ``LIKE``, which made a multi-word query
    structurally unable to hit: 0 of 2,158 indexed paths on a real chip
    contain a space. A single-word query builds the identical single-LIKE
    clause it always did.
    """
    from quam_state_manager.core.search_query import groups as _sq_groups

    grps = _sq_groups(query or "")
    if not grps:
        return []

    def _like(term: str) -> str:
        return "%" + term.replace("%", r"\%").replace("_", r"\_") + "%"

    clauses: list[str] = []
    params: list[str] = []
    for g in grps:
        clauses.append("(" + " OR ".join([r"p.path LIKE ? ESCAPE '\'"] * len(g)) + ")")
        params.extend(_like(t) for t in g)
    rows = conn.execute(
        "SELECT p.path, COUNT(l.snap_id) AS n "
        "  FROM leaf_paths p LEFT JOIN leaf_cp l ON l.path_id = p.id "
        " WHERE " + " AND ".join(clauses) +
        " GROUP BY p.id ORDER BY n DESC, p.path ASC LIMIT ?",
        (*params, int(limit))).fetchall()
    return [{"path": r[0], "changes": r[1]} for r in rows]
