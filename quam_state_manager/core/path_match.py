"""Decide whether the quam_state Qualibrate is writing == the one SM has open.

The ``/workbench`` co-display nudges SM when Qualibrate writes the live state,
then syncs. But the watch resolves QUALIBRATE's active-project ``state_path``
while sync pulls SM's loaded folder — if those differ, a Qualibrate "fit"
produces a SILENT NO-OP (nudge fires on Qualibrate's path, sync pulls SM's
unchanged path). This module is the verdict that gates the nudge and drives the
workbench path indicator.

Users are single-OS (Windows→Windows or macOS→macOS, never mixed — WSL is only
the dev harness), so a **same-namespace path compare** is enough. Chip
fingerprints (:func:`history.fingerprint_of`) are used ONLY to tell "same chip,
different folder" (a per-experiment copy) apart from a genuine mismatch.

Pure: reads the state files for fingerprinting; no Flask.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from quam_state_manager.core import history

# Verdict states.
LINKED = "linked"                                    # same physical folder
LINKED_DIFFERENT_FOLDER = "linked-different-folder"  # same chip, different folder (e.g. an experiment copy)
MISMATCH = "mismatch"                                # a different chip
QB_UNRESOLVED = "qb-unresolved"                       # Qualibrate's path could not be resolved
SM_EMPTY = "sm-empty"                                 # no chip is loaded in SM
INDETERMINATE = "indeterminate"                       # folders differ and a fingerprint was unreadable


def same_folder(a: str | Path, b: str | Path) -> bool:
    """True when two paths point at the same physical folder (same OS).

    ``os.path.samefile`` is the ground truth (immune to case-fold, symlinks and
    trailing slashes) but raises when either side is missing; fall back to a
    ``normcase``-resolved string compare (and, if resolve fails, a raw normcase
    compare).
    """
    pa, pb = Path(a), Path(b)
    try:
        if os.path.samefile(pa, pb):
            return True
    except OSError:
        pass
    try:
        return os.path.normcase(str(pa.resolve())) == os.path.normcase(str(pb.resolve()))
    except OSError:
        return os.path.normcase(str(pa)) == os.path.normcase(str(pb))


def verdict(qb_path: str | Path | None, sm_path: str | Path | None,
            *, qb_reason: str | None = None) -> dict[str, Any]:
    """Compare Qualibrate's live state dir against SM's loaded dir.

    Returns ``{"state": <one of the module constants>, ...}``:
      - qb None        → ``qb-unresolved`` (+ ``reason``)
      - sm None        → ``sm-empty``
      - same folder    → ``linked``
      - same chip,
        diff folder    → ``linked-different-folder``  (fingerprints align)
      - different chip → ``mismatch``
      - can't tell     → ``indeterminate``            (a fingerprint was None)
    """
    if not qb_path:
        return {"state": QB_UNRESOLVED,
                "reason": qb_reason or "could not resolve Qualibrate's state path from config"}
    if not sm_path:
        return {"state": SM_EMPTY}
    if same_folder(qb_path, sm_path):
        return {"state": LINKED}
    # Different folders — is it the same chip (a per-experiment copy)?
    alignment = history.align(history.fingerprint_of(qb_path), history.fingerprint_of(sm_path))
    if alignment == history.ALIGN_ALIGNED:
        return {"state": LINKED_DIFFERENT_FOLDER}
    if alignment in (history.ALIGN_DIFFERENT_CHIP, history.ALIGN_RENAMED):
        return {"state": MISMATCH}
    return {"state": INDETERMINATE}  # ALIGN_UNKNOWN — a fingerprint was unreadable


# Convenience for callers/UI: which verdicts mean "the sync/nudge loop will work".
LINKED_STATES = frozenset({LINKED, LINKED_DIFFERENT_FOLDER})


def is_linked(state: str) -> bool:
    return state in LINKED_STATES


# Folder basenames that are a generic state container, not a chip name.
_GENERIC_STATE_DIRS = frozenset({"quam_state", "quam_states", "quam-state", "state"})


def chip_label(path: str | Path) -> str:
    """A short human chip name for the /workbench bar.

    Uses the folder's OWN name (e.g. ``quam_states/LabA`` → ``LabA``) unless
    that name is a generic state-container (``quam_state``/``state``/…), in which
    case it falls back to :func:`history.chip_name_for` (which derives the chip
    from the ``<chip>/quam_state`` and per-experiment layouts).
    """
    p = Path(path)
    if p.name.lower() in _GENERIC_STATE_DIRS:
        return history.chip_name_for(p)
    return p.name
