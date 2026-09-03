"""Cross-save undo journal (docs/107).

The change log dies on every ``/save`` (``saver.save`` clears it) -- which is
correct for the tray, but it also used to be the end of Ctrl+Z's reach.  This
module records the OUTGOING log at each save as *units* (one unit == one
user action == exactly what one ``undo_group`` press would have popped), so
the undo chain can keep walking past the save/apply boundary.

Covenant (user-stated, binding; AMENDED 2026-08-12 -- docs/117, and again
2026-08-16 -- docs/120 item 8): **a direct live write happens only on an
explicit Apply press OR inside a user-enabled Auto-Sync session** (default OFF,
always visible, auto-disarmed on conflict). The 2026-08-16 amendment extends
the same shape to the OTHER direction: that session may also replace the
working copy FROM live -- but only with "auto replace" ticked, which the user
defined as the consent. Unticked, a pull that would discard unapplied edits is
refused and the drift banner asks instead.
A journal step is unaffected: it never touches the live files -- it only
STAGES the inverse edits back into the change log (the review tray), under a
``jrn:<unit-id>`` group id.  With a session armed, that staged inverse is
flushed by the ordinary apply route, so undo still reaches the chip through
the ONE door.  The gid prefix is the
routing marker: /undo seeing a ``jrn:`` group on top walks DEEPER into the
journal instead of popping it; /redo seeing it on top un-stages it.

Pure module: no web imports.  Persistence is a per-chip sidecar
``instance/working_state/<key>.undo_journal.json`` (sibling FILE of the
working-copy dir -- the GC scan iterates ``is_dir()`` children and never sees
it), written via ``safe_io.atomic_write_json`` under a module lock (the
type_policy sidecar pattern).  All journal I/O is advisory bookkeeping: a
failed write must never fail the save that triggered it (callers wrap).
"""

from __future__ import annotations

import copy
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from . import safe_io
from . import working_copy
from .loader import ChangeEntry

logger = logging.getLogger(__name__)

#: gid prefix marking a change-log group as a STAGED JOURNAL STEP.  Chosen so
#: it can never collide with ordinary gids (``grp<seq>``); it survives
#: segmentation for free -- a re-save of staged journal steps records them as
#: ordinary units again (their gid run segments like any other).
GID_PREFIX = "jrn:"

#: Sidecar bound -- oldest units beyond this are trimmed at append time (and
#: ONLY at append time: capture always ends with cursor at the tip, so the
#: cursor can never be left pointing into a trimmed range by construction).
MAX_UNITS = 200

#: docs/160: the sidecar also carries the walk CURSOR (how many units are in
#: effect on the chip) once a live undo/redo has moved it -- a restart or a
#: second window then resumes the walk where it stands instead of at the tip.
#: A version-1 sidecar (no cursor) reads as "cursor at the tip", unchanged.
JOURNAL_VERSION = 2

_lock = threading.Lock()


# ----------------------------------------------------------------------
# Sidecar location
# ----------------------------------------------------------------------

def sidecar_path(instance_path: str | Path, live_folder: str | Path) -> Path:
    """``<instance>/working_state/<key>.undo_journal.json`` for a live folder."""
    key = working_copy.key_for(live_folder)
    return working_copy.working_state_root(instance_path) / f"{key}.undo_journal.json"


# ----------------------------------------------------------------------
# Segmentation (change log -> units)
# ----------------------------------------------------------------------

def segment_change_log(log: list[ChangeEntry]) -> list[list[ChangeEntry]]:
    """Split a change log into user-action units, oldest first.

    Contiguous same-gid runs form one unit; ``group_id is None`` is always a
    singleton.  This is provably what iterated :meth:`Modifier.undo_group`
    would pop (each pop takes the maximal TRAILING same-gid run, None ==
    singleton), so replaying units newest-to-oldest == pressing Ctrl+Z
    repeatedly -- the property ``tests/test_undo_journal.py`` pins.
    """
    units: list[list[ChangeEntry]] = []
    for entry in log:
        if (units
                and entry.group_id is not None
                and units[-1][-1].group_id == entry.group_id):
            units[-1].append(entry)
        else:
            units.append([entry])
    return units


def make_unit(entries: list[ChangeEntry], ts: float | None = None,
              meta: dict | None = None) -> dict:
    """Serialize one unit.  Values are deep-copied so the sidecar (and the
    RAM mirror) can never alias a live subtree that later mutates.

    docs/117: `meta` is an OPTIONAL, additive dict describing how the unit
    came to be (``{"src": "auto"}`` for an auto-apply flush).  It is what
    lets the applied-log show only the changes that actually reached the
    live chip, without a second store beside this one.  Nothing reads it
    except that log, `JOURNAL_VERSION` is unchanged, and a sidecar written
    by an older build simply has no meta.
    """
    unit = {
        "id": uuid.uuid4().hex[:12],
        "ts": time.time() if ts is None else ts,
        "entries": [
            {
                "path": e.dot_path,
                "old": copy.deepcopy(e.old_value),
                "new": copy.deepcopy(e.new_value),
                "source_file": e.source_file,
                "created": bool(e.created),
                "deleted": bool(e.deleted),
            }
            for e in entries
        ],
    }
    if meta:
        unit["meta"] = dict(meta)
    return unit


def units_from_log(log: list[ChangeEntry], meta: dict | None = None) -> list[dict]:
    """Segment + serialize an outgoing change log (oldest unit first)."""
    ts = time.time()
    return [make_unit(seg, ts=ts, meta=meta) for seg in segment_change_log(log)]


# ----------------------------------------------------------------------
# Inverse ops (what a journal step stages)
# ----------------------------------------------------------------------

def inverse_ops(unit: dict) -> list[tuple[str, str, Any, str]]:
    """The staging plan for one journal step: ``(op, path, value, source_file)``
    tuples, **entries in REVERSE order** -- a rename unit is
    create(new)+delete(old); its inverse must re-create the OLD subtree before
    deleting the new one, i.e. undo exactly mirrors :meth:`undo_group`'s LIFO.

    ops: ``set`` (stage old value) / ``delete`` (entry created a key -> stage
    its removal) / ``create`` (entry deleted a subtree -> stage its
    restoration).  Values are deep-copied -- the modifier will hold them.
    """
    ops: list[tuple[str, str, Any, str]] = []
    for e in reversed(unit.get("entries", [])):
        if e.get("created"):
            ops.append(("delete", e["path"], None, e.get("source_file", "state")))
        elif e.get("deleted"):
            ops.append(("create", e["path"], copy.deepcopy(e.get("old")),
                        e.get("source_file", "state")))
        else:
            ops.append(("set", e["path"], copy.deepcopy(e.get("old")),
                        e.get("source_file", "state")))
    return ops


def forward_ops(unit: dict) -> list[tuple[str, str, Any, str]]:
    """The REPLAY plan for one unit (docs/160: Ctrl+Shift+Z after a live undo
    re-applies the unit forward): ``(op, path, value, source_file)`` tuples in
    CHRONOLOGICAL order -- the exact mirror of :func:`inverse_ops`.  A rename
    unit is create(new)+delete(old) again, in that order.

    ops: ``create`` (entry created a key -> create it with its new value) /
    ``delete`` (entry deleted a subtree -> delete it again) / ``set`` (its
    new value).  Values are deep-copied.
    """
    ops: list[tuple[str, str, Any, str]] = []
    for e in unit.get("entries", []):
        if e.get("created"):
            ops.append(("create", e["path"], copy.deepcopy(e.get("new")),
                        e.get("source_file", "state")))
        elif e.get("deleted"):
            ops.append(("delete", e["path"], None, e.get("source_file", "state")))
        else:
            ops.append(("set", e["path"], copy.deepcopy(e.get("new")),
                        e.get("source_file", "state")))
    return ops


# ----------------------------------------------------------------------
# Sidecar I/O
# ----------------------------------------------------------------------

def _read_doc(p: Path) -> dict:
    if not p.exists():
        return {}
    try:
        doc = safe_io.read_json(p, attempts=1)
        return doc if isinstance(doc, dict) else {}
    except Exception:
        logger.warning("undo journal unreadable, starting empty: %s", p)
        return {}


def load(path: str | Path) -> list[dict]:
    """Load the units list (oldest first).  Missing/corrupt -> ``[]`` --
    the journal is advisory history and must never block activation."""
    units = _read_doc(Path(path)).get("units", [])
    return units if isinstance(units, list) else []


def load_state(path: str | Path) -> tuple[list[dict], int]:
    """``(units, cursor)`` -- the cursor is the persisted walk position when
    the sidecar carries one (docs/160), else the tip.  Always clamped into
    ``[0, len(units)]``: a cursor written against a longer list (another
    window trimmed it) can never point past the end."""
    doc = _read_doc(Path(path))
    units = doc.get("units", [])
    units = units if isinstance(units, list) else []
    cur = doc.get("cursor")
    if not isinstance(cur, int) or isinstance(cur, bool):
        cur = len(units)
    return units, max(0, min(cur, len(units)))


def save_cursor(path: str | Path, cursor: int) -> None:
    """Persist the walk cursor (docs/160) -- load-merge-write under the
    module lock so a concurrent append is never clobbered.  Advisory."""
    p = Path(path)
    with _lock:
        doc = _read_doc(p)
        units = doc.get("units", [])
        units = units if isinstance(units, list) else []
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            safe_io.atomic_write_json(p, {"version": JOURNAL_VERSION,
                                          "units": units,
                                          "cursor": max(0, min(int(cursor), len(units)))})
        except Exception:
            logger.warning("undo journal cursor write failed: %s", p, exc_info=True)


def sidecar_mtime(path: str | Path) -> float | None:
    """The sidecar's mtime, or None -- what :func:`routes._journal_sync`
    compares to notice another window's write (docs/160 C)."""
    try:
        return Path(path).stat().st_mtime
    except OSError:
        return None


def mark_unit(path: str | Path, unit_id: str, patch: dict) -> list[dict]:
    """Merge `patch` into one unit's ``meta`` (load-merge-write under the
    module lock, atomic write).  Returns the full post-write list.

    docs/117: used to stamp ``reverted_by`` on an applied-log row, so a
    reverted row can render struck-through with its X disabled instead of
    inviting a second revert of the same change.  Advisory exactly like
    :func:`append_units` -- a failed write must never break the caller.
    """
    p = Path(path)
    with _lock:
        units, cursor = load_state(p)   # docs/160: the persisted cursor survives a mark
        hit = False
        for u in units:
            if u.get("id") == unit_id:
                meta = dict(u.get("meta") or {})
                meta.update(patch)
                u["meta"] = meta
                hit = True
                break
        if not hit:
            return units
        try:
            safe_io.atomic_write_json(p, {"version": JOURNAL_VERSION,
                                          "units": units, "cursor": cursor})
        except Exception:
            logger.warning("undo journal mark failed: %s", p, exc_info=True)
        return units


def insert_units(path: str | Path, new_units: list[dict], *, before_tail: int) -> list[dict]:
    """Insert ``new_units`` below the newest ``before_tail`` units (docs/160 B,
    review F1): a wholesale base recorded in the same apply as the edits made
    on top of it must sit BELOW those edits so the walk undoes the edits
    first. Cursor at the tip. Advisory, atomic, under the module lock.

    Like :func:`append_units`, an insert TRUNCATES at the persisted cursor
    first (code-review round 2, F9): a new action after a live undo discards
    the redo branch. Writing the cursor at the tip over an un-truncated list
    resurrected units the chip no longer holds -- the applied log reported
    them as in effect and offered a ✕ that then 409s."""
    p = Path(path)
    with _lock:
        units, cursor = load_state(p)
        if 0 <= cursor < len(units):
            units = units[:cursor]
            before_tail = min(int(before_tail), len(units))
        cut = max(0, len(units) - max(0, int(before_tail)))
        units = units[:cut] + list(new_units) + units[cut:]
        if len(units) > MAX_UNITS:
            units = units[-MAX_UNITS:]
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            safe_io.atomic_write_json(p, {"version": JOURNAL_VERSION,
                                          "units": units, "cursor": len(units)})
        except Exception:
            logger.warning("undo journal insert failed: %s", p, exc_info=True)
        return units


def append_units(path: str | Path, new_units: list[dict], *,
                 truncate_at_cursor: bool = True) -> list[dict]:
    """Append units to the sidecar (load-merge-write under the module lock),
    trim to :data:`MAX_UNITS` keeping the newest, atomic write.  Returns the
    full post-append list (the caller's RAM mirror).  The cursor is written
    at the new tip: a save is a new action, and after it every unit in the
    file is in effect.

    ``truncate_at_cursor`` (docs/160): when the sidecar's own persisted cursor
    sits below the tip, the units past it were undone ON THE CHIP (only a
    live undo/redo ever writes that cursor); a new action after such an undo
    discards that redo branch -- the editor rule -- so the journal stays a
    straight line of what is in effect.  A staged-only walk never moves the
    persisted cursor, so the docs/107 stage-only mode keeps its file whole,
    byte-for-byte (a re-save of staged steps still appends them as units).

    Cross-process note (docs/80 stance): two windows on one chip race this
    file load-merge-write; the module lock serializes same-process, the
    atomic write keeps the file always-parsable, and residual
    last-writer-wins is TOLERATED -- the journal is advisory history, the
    apply/conflict machinery is the correctness layer.
    """
    if not new_units:
        return load(path)
    p = Path(path)
    with _lock:
        units, cursor = load_state(p)
        if truncate_at_cursor and 0 <= cursor < len(units):
            units = units[:cursor]
        units.extend(new_units)
        if len(units) > MAX_UNITS:
            units = units[-MAX_UNITS:]
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            safe_io.atomic_write_json(p, {"version": JOURNAL_VERSION,
                                          "units": units, "cursor": len(units)})
        except Exception:
            # Advisory: never let journal persistence break the save path.
            logger.warning("undo journal write failed: %s", p, exc_info=True)
        return units
