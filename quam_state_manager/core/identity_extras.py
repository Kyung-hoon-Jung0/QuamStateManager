"""The chip's identity extras survive loading an older version of itself.

``extras.chip_name`` names the chip (the history ladder files every snapshot
by it, docs/20 v2) and ``extras.data_folder`` pairs it with its experiment
data (SM registers it as a workspace root on every activation). Neither is a
calibration value. A version saved before the chip was named, or before its
data moved, carries the old value, and loading that version wholesale
silently re-pointed the chip: the sync panel offered to write the old data
folder back, and the project asked whether to scope Datasets to it
(docs/301 F6, F26).

The doors that load a whole version of the SAME chip keep the current values
and name what they kept. Two doors deliberately do not: an explicit
cross-chip apply ("anyway"), which takes the other chip's state as it is and
names the identity change, and Revert last apply, which must restore the
pre-apply files byte for byte.
"""
from __future__ import annotations

import copy
from typing import Any

IDENTITY_KEYS = ("chip_name", "data_folder")
_NOUN = {"chip_name": "chip name", "data_folder": "data folder"}


def _present(v: Any) -> bool:
    return v not in (None, "", [], {})


def keep_identity(incoming: Any, current: Any) -> tuple[Any, list[dict]]:
    """``incoming`` with the identity extras of ``current`` (a copy when any
    is carried), plus one ``{"key", "version", "kept"}`` per value carried.

    Only a value ``current`` actually holds is carried; a key ``current``
    lacks leaves ``incoming`` as it is. Nothing else in ``extras`` moves."""
    if not isinstance(incoming, dict) or not isinstance(current, dict):
        return incoming, []
    cur_x = current.get("extras")
    if not isinstance(cur_x, dict):
        return incoming, []
    inc_x = incoming.get("extras") if isinstance(incoming.get("extras"), dict) else None
    kept: list[dict] = []
    new_x = dict(inc_x) if inc_x is not None else {}
    for key in IDENTITY_KEYS:
        value = cur_x.get(key)
        if not _present(value):
            continue
        had = inc_x.get(key) if inc_x is not None else None
        if had == value:
            continue
        kept.append({"key": key, "version": copy.deepcopy(had), "kept": copy.deepcopy(value)})
        new_x[key] = copy.deepcopy(value)
    if not kept:
        return incoming, []
    out = dict(incoming)
    out["extras"] = new_x
    return out, kept


def kept_note(kept: list[dict], source: str = "version") -> str:
    """One sentence naming what was kept, or ``""``; *source* names what
    was loaded ("version", "run")."""
    if not kept:
        return ""
    parts = []
    for k in kept:
        had = k["version"]
        shown = ("none" if not _present(had)
                 else ", ".join(str(x) for x in had) if isinstance(had, list) else str(had))
        parts.append(f"{_NOUN.get(k['key'], k['key'])} (the {source} had {shown})")
    return (" Kept this chip's own " + " and ".join(parts)
            + " — identity, not calibration.")
