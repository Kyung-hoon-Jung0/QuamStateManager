"""Cross-save undo journal (docs/107).

The change log dies on every ``/save`` (``saver.save`` clears it) -- which is
correct for the tray, but it also used to be the end of Ctrl+Z's reach.  This
module records the OUTGOING log at each save as *units* (one unit == one
user action == exactly what one ``undo_group`` press would have popped), so
the undo chain can keep walking past the save/apply boundary.

Covenant (user-stated, binding): **any direct live write requires >= 1
explicit press of Apply-to-live.**  A journal step therefore never touches
the live files -- it only STAGES the inverse edits back into the change log
(the review tray), under a ``jrn:<unit-id>`` group id.  The gid prefix is the
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

JOURNAL_VERSION = 1

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


def make_unit(entries: list[ChangeEntry], ts: float | None = None) -> dict:
    """Serialize one unit.  Values are deep-copied so the sidecar (and the
    RAM mirror) can never alias a live subtree that later mutates."""
    return {
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


def units_from_log(log: list[ChangeEntry]) -> list[dict]:
    """Segment + serialize an outgoing change log (oldest unit first)."""
    ts = time.time()
    return [make_unit(seg, ts=ts) for seg in segment_change_log(log)]


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


# ----------------------------------------------------------------------
# Sidecar I/O
# ----------------------------------------------------------------------

def load(path: str | Path) -> list[dict]:
    """Load the units list (oldest first).  Missing/corrupt -> ``[]`` --
    the journal is advisory history and must never block activation."""
    p = Path(path)
    if not p.exists():
        return []
    try:
        doc = safe_io.read_json(p, attempts=1)
        units = doc.get("units", [])
        return units if isinstance(units, list) else []
    except Exception:
        logger.warning("undo journal unreadable, starting empty: %s", p)
        return []


def append_units(path: str | Path, new_units: list[dict]) -> list[dict]:
    """Append units to the sidecar (load-merge-write under the module lock),
    trim to :data:`MAX_UNITS` keeping the newest, atomic write.  Returns the
    full post-append list (the caller's RAM mirror).

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
        units = load(p)
        units.extend(new_units)
        if len(units) > MAX_UNITS:
            units = units[-MAX_UNITS:]
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            safe_io.atomic_write_json(p, {"version": JOURNAL_VERSION,
                                          "units": units})
        except Exception:
            # Advisory: never let journal persistence break the save path.
            logger.warning("undo journal write failed: %s", p, exc_info=True)
        return units
