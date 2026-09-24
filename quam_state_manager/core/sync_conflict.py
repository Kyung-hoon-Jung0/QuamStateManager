"""Which fields are actually in conflict — the question Auto-Sync needs answered.

Auto-Sync used to decide with one whole-file question: *does the working copy
differ from the sync point at all?* If it did, and the user had not ticked
"…and replace my unapplied edits", the pull was refused and a banner went up.

So an experiment writing ``q2.T1`` while the user was editing ``q1.f_01``
raised exactly the same prompt as an experiment overwriting the very field the
user was typing into. The two cases are not alike, the prompt could not tell
them apart, and a user who sees the same box for both learns to dismiss it.

The question that matters is narrower: **of the paths the live chip changed,
which has the user also changed?** Everything else can be pulled without
asking — which is what arming Auto-Sync meant in the first place.

The separation needs the sync-point value, and it is already in hand: a
``ChangeEntry`` carries ``old_value``, the value the path held before the user
touched it. So for a path the user edited, *they* changed it too exactly when
the live value differs from that original. No extra file read, no third
snapshot.

    S = the sync point   W = the working copy (S + my edits)   L = live (S + theirs)

    diff(W, L) says only that W and L disagree — it cannot say whose edit
    caused the disagreement. old_value supplies S for the paths that matter.

**Honesty rule.** Dirt this module cannot enumerate makes the whole verdict
``unaccounted``, and Auto-Sync then asks exactly as it did before. Silence is
only ever granted over edits that were counted. A wrong "no conflict" destroys
work with no prompt and no Ctrl+Z, so the failure direction is not symmetric.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping


def covers(ancestor: str, path: str) -> bool:
    """Does *ancestor* cover *path* — the same leaf, or a subtree above it?

    A created/deleted subtree is recorded at its ROOT (``qubits.q1``), so it
    has to collide with anything beneath it (``qubits.q1.T1``).
    """
    return ancestor == path or path.startswith(ancestor + ".")


@dataclass(frozen=True)
class Verdict:
    """What Auto-Sync is allowed to do without asking."""

    conflicts: tuple[str, ...] = ()
    """Paths the user changed AND the live chip changed. Only these need a
    human. Sorted, so a message reads the same twice running."""

    external: tuple[str, ...] = ()
    """Paths the live chip changed that the user did not touch — the part a
    pull can adopt silently."""

    mine: tuple[str, ...] = ()
    """Paths the user changed, conflicting or not."""

    unaccounted: str | None = None
    """Why ``mine`` may be incomplete, in the user's words, or None when the
    set is exact. Any value here forbids a silent pull."""

    @property
    def may_pull_silently(self) -> bool:
        """The whole point: nothing collides and nothing is unaccounted for."""
        return not self.conflicts and self.unaccounted is None

    @property
    def must_ask(self) -> bool:
        return not self.may_pull_silently


def originals_from_change_log(change_log: Iterable[Any]) -> dict[str, Any]:
    """Path → the value it held BEFORE the user's first edit of it.

    The EARLIEST entry per path wins: a user who edited a field three times
    still started from one original, and it is that original the live chip
    must be compared against. A created/deleted subtree is keyed at its own
    root — ``covers`` handles the reach.
    """
    out: dict[str, Any] = {}
    for entry in change_log or ():
        path = getattr(entry, "dot_path", None)
        if not path:
            continue
        if path not in out:                      # first edit wins
            out[path] = getattr(entry, "old_value", None)
    return out


def _subtree_paths(change_log: Iterable[Any]) -> set[str]:
    """Paths recorded as a whole created/deleted subtree, not a single leaf."""
    out: set[str] = set()
    for entry in change_log or ():
        path = getattr(entry, "dot_path", None)
        if path and (getattr(entry, "created", False) or getattr(entry, "deleted", False)):
            out.add(path)
    return out


def classify(
    *,
    live_by_path: Mapping[str, Any],
    change_log: Iterable[Any] = (),
    dom_paths: Iterable[str] = (),
    reapply_paths: Iterable[str] = (),
    working_dirty: bool = False,
    reapply_originals: Mapping[str, Any] | None = None,
) -> Verdict:
    """Decide what Auto-Sync may do.

    ``live_by_path`` is the live value at every path where the working copy and
    the live chip currently disagree — i.e. ``Differ().diff(working, live)``
    reduced to ``{dot_path: new_value}``. A path absent from it is a path the
    two sides already agree on, which can never be a conflict.

    ``dom_paths`` are grid cells typed but not yet committed. The server cannot
    see them and the working copy does not hold them, so a DOM path that
    appears in ``live_by_path`` is one the live chip moved under the user's
    fingers — a conflict by construction.

    ``working_dirty`` (saved-but-unapplied) and ``reapply_paths`` (a stash
    mid-merge) are dirt whose originals this module does not have, so they make
    the verdict unaccounted rather than silently trusted.

    ``reapply_originals`` (QA liveedit-r2-05 review) is the value each stashed
    leaf held before the user's first edit, recorded when it was stashed. A
    stash path that has one is judged exactly like a change-log edit -- a
    conflict only when the live chip moved away from it -- and that original
    wins over a later change-log one (a save cleared the log in between, so
    the log's is the user's own saved value). A stash path without one keeps
    the old any-difference rule. ``unaccounted`` is unchanged: an automatic
    pull over a stash still asks.
    """
    originals = originals_from_change_log(change_log)
    subtrees = _subtree_paths(change_log)
    dom = {str(p) for p in dom_paths if p}
    stash = {str(p) for p in reapply_paths if p}
    stash_orig = {p: v for p, v in (reapply_originals or {}).items() if p in stash}
    log_had_originals = bool(originals)
    originals.update(stash_orig)

    mine = set(originals) | dom | stash
    conflicts: set[str] = set()

    for path, orig in originals.items():
        if path in subtrees:
            # A whole subtree the user created or deleted: any live change at
            # or under it collides, and there is no single value to compare.
            if any(covers(path, lp) or covers(lp, path) for lp in live_by_path):
                conflicts.add(path)
            continue
        if path in live_by_path and live_by_path[path] != orig:
            # The live chip moved away from what this edit started at, so the
            # other writer touched this same field. (Equal means only the user
            # moved it — the pull must keep that edit, not ask about it.)
            conflicts.add(path)

    for path in dom:
        # Not in the working copy, so a disagreement here is entirely theirs.
        if path in live_by_path:
            conflicts.add(path)

    for path in stash:
        if path in stash_orig:
            continue                             # judged against its original above
        if path in live_by_path:
            conflicts.add(path)

    external = {p for p in live_by_path if not any(
        covers(m, p) or covers(p, m) for m in mine)}

    unaccounted = None
    if working_dirty and not log_had_originals:
        # Saved-but-unapplied: the save journalled and cleared the change log,
        # so the originals are gone from memory. Refuse to guess.
        unaccounted = ("there are saved edits whose original values SM no "
                       "longer holds in memory")
    elif stash:
        unaccounted = "a pull is already part-way through re-applying your edits"

    return Verdict(
        conflicts=tuple(sorted(conflicts)),
        external=tuple(sorted(external)),
        mine=tuple(sorted(mine)),
        unaccounted=unaccounted,
    )
