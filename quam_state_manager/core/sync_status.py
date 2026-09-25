"""One answer to "where does this window stand against the live chip?" (sync-ux 2026-09-25).

The sync UI used to answer that question in up to ten places at once -- a
status pill, a tray bar, a drift banner, Chip Status' own banner, a conflict
tray, toasts -- each from a different subset of the facts, so the same state
looked different depending on the page, the window, and whether the user had
pressed F5. The user decided (2026-09-25) on ONE status control and ONE panel.
This module is the one place that turns the facts into that control's state,
so every renderer and every window reads the same verdict.

Two kinds of facts, kept apart on purpose:

* LIVE facts need a read of the live state files: has the live chip moved away
  from the sync point, which of its changes collide with the user's own edits,
  and is the live file readable at all. They are computed on the poll
  (``/state/drift``), never on render (docs/28), and cached on the context
  against the live mtimes + sync point they were read at.
* LOCAL facts are free: how many unapplied edits, whether a saved/staged
  working copy exists, whether this is a read-only archive. They are read at
  render time, so a fresh edit shows at once even though the live facts are a
  poll old.

``state`` is one of :data:`STATES`; the precedence is the order of that tuple
(an archive is an archive before anything else, an unreadable live file makes
every live-side claim unknowable, a same-field collision is the one question
only the user can answer, a refused apply is the most recent thing the user did,
and it outranks a plain two-sided change).
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Iterable, Mapping

STATES = (
    "archive",     # a dataset run's frozen quam_state -- read-only
    "unreadable",  # the live state files cannot be parsed (torn / crashed save)
    "collide",     # live changed a field the user also edited
    "refused",     # the user's own Apply wrote nothing: the live chip had moved
    "both",        # live changed + the user has unapplied edits, no overlap
    "live",        # live changed, nothing of the user's is pending
    "staged",      # a whole document (stage / Load State / saved draft) not on live
    "mine",        # unapplied edits, live unchanged
    "synced",
)

STALE_CAP = 300
"""How many stale cells the poll ships. A chip that moved more than this is
announced by count; marking 8,000 cells would help nobody read them."""


def fmt_value(v: Any) -> str:
    """The short text a stale cell's "live now …" line shows.

    Floats keep their full repr (the grid shows the stored value verbatim, so a
    rounded "live now" would read as a different number); everything else is
    its JSON text, so a string is quoted exactly as the grid quotes it."""
    if isinstance(v, bool) or v is None:
        return json.dumps(v)
    if isinstance(v, float):
        return repr(v) if math.isfinite(v) else str(v)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, str):
        return v
    try:
        s = json.dumps(v, sort_keys=True)
    except (TypeError, ValueError):
        s = str(v)
    return s if len(s) <= 80 else s[:77] + "..."


def derive_state(*, archive: bool, unreadable: bool, refused: bool,
                 live_moved: bool, conflicts: int, unapplied: int,
                 working_dirty: bool) -> str:
    """The control's state from the facts. Pure; see the module docstring for
    the precedence."""
    if archive:
        return "archive"
    if unreadable:
        return "unreadable"
    if live_moved and conflicts:
        return "collide"
    if refused and (unapplied or working_dirty):
        return "refused"
    if live_moved:
        if unapplied or working_dirty:
            return "both"
        return "live"
    if unapplied:
        return "mine"
    if working_dirty:
        return "staged"
    return "synced"


def live_facts(*, entries: Iterable[Any], moved: bool | None,
               verdict: Any = None) -> dict:
    """The cached LIVE facts from one read: ``Differ().diff(working, live)``
    entries, whether the live content moved from the sync point, and the
    ``sync_conflict.classify`` verdict over the same entries.

    ``moved is False`` means nothing outside SM wrote since the last sync, so
    every difference is SM-side (the user's edits, a staged document) and none
    is a live change -- the same rule as ``_live_diff_attribution``.
    """
    entries = list(entries or ())
    live_by_path = {e.dot_path: e.new_value for e in entries}
    here_by_path = {e.dot_path: e.old_value for e in entries}
    if moved is False or verdict is None:
        conflicts: list[str] = []
        external: list[str] = []
    else:
        conflicts = list(getattr(verdict, "conflicts", ()) or ())
        external = list(getattr(verdict, "external", ()) or ())
    stale: dict[str, str] = {}
    for p in external + conflicts:
        if len(stale) >= STALE_CAP:
            break
        if p in live_by_path:
            stale[p] = fmt_value(live_by_path[p])
    rows = []
    for p in conflicts:
        rows.append({"path": p, "here": fmt_value(here_by_path.get(p)),
                     "live": fmt_value(live_by_path.get(p))})
    return {
        "moved": bool(moved) if moved is not None else None,
        "conflicts": conflicts,
        "conflict_rows": rows,
        "external": external,
        "live_n": len(external) + len(conflicts),
        "stale": stale,
    }


def view(*, facts: Mapping[str, Any] | None, flag_moved: bool,
         flag_count: int | None, archive: bool, unreadable: str | None,
         flag_conflicts: Iterable[str] = (),
         refused: Mapping[str, Any] | None, change_paths: Iterable[str],
         unapplied: int, working_dirty: bool) -> dict:
    """What the control renders NOW: cached live facts (may be one poll old)
    combined with the local facts read at render time.

    ``facts`` is None when no poll has judged this sync point yet; the context's
    ``live_diverged`` flag (``flag_moved``) and its free count stand in, so a
    chip that was already diverged at activation still says so on first paint.
    A cached collision is kept only while the user still holds an edit at that
    path -- an edit discarded since the read cannot still collide."""
    paths = set(change_paths or ())
    if facts is not None:
        moved = bool(facts.get("moved"))
        conflicts = [p for p in facts.get("conflicts") or () if p in paths]
        live_n = int(facts.get("live_n") or 0)
        rows = [r for r in facts.get("conflict_rows") or () if r.get("path") in paths]
        stale = dict(facts.get("stale") or {})
    else:
        # no poll has judged this sync point yet: the flag, its free count, and
        # the fields an Auto-Sync pull or a collision check already named
        moved = bool(flag_moved)
        conflicts = [p for p in (flag_conflicts or ()) if p in paths] if moved else []
        rows, stale = [], {}
        live_n = max(int(flag_count or 0), len(conflicts))
    state = derive_state(archive=archive, unreadable=bool(unreadable),
                         refused=bool(refused), live_moved=moved,
                         conflicts=len(conflicts), unapplied=unapplied,
                         working_dirty=working_dirty)
    out = {
        "state": state,
        "unapplied": int(unapplied),
        "working_dirty": bool(working_dirty),
        "live_moved": moved,
        "live_n": live_n,
        "conflicts": conflicts,
        "conflict_rows": rows,
        "stale": stale if state not in ("archive", "unreadable") else {},
        "unreadable": unreadable or None,
        "refused_at": (refused or {}).get("at") if refused else None,
    }
    out["sig"] = signature(out)
    return out


def signature(v: Mapping[str, Any]) -> str:
    """A short digest of what the control SHOWS. A window compares it with the
    one its control was rendered at and re-renders only when it moved -- so a
    lowered state (another window took live, SE-05) repaints exactly like a
    raised one, and nothing repaints for a poll that changed nothing."""
    key = json.dumps([v.get("state"), v.get("unapplied"), v.get("working_dirty"),
                      v.get("live_n"), sorted(v.get("conflicts") or ()),
                      v.get("unreadable"), v.get("refused_at")],
                     sort_keys=True, default=str)
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
