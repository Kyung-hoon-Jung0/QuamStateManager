"""Throwaway chip copies for a lab-side check.

:func:`make_chip_copies` reads the store's in-memory state and wiring
documents (unsaved edits included) and its ``mutation_seq`` in ONE hold of the
store lock, releases the lock, and writes two temporary folders:

- ``before``: the documents as they are;
- ``after``: the same documents plus a list of creates ``[(dot_path, value)]``,
  applied by the modifier's own ``create_subtree`` on a detached store built
  from the copied documents (:func:`apply_creates`), so "after" is exactly
  what the real write produces, and a create the real write would refuse
  raises :class:`CreateError` naming the path.

Nothing in the store changes: no change-log entry, no undo step, no working
copy or live file is touched. :func:`store_at_seq` answers, under the lock,
whether the store is still at the sequence value the copies were read at.
:func:`write_chip_folder` is the one folder writer (the add / remove env check
uses it too).

Known limit: creates carry no key position, so the key ORDER in "after" may
differ from a later write that inserts at an index; the content is equal.
"""

from __future__ import annotations

import copy
import json
import marshal
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class ChipCopies:
    before: Path
    after: Path
    seq: int
    root: Path
    root_class: str | None = None
    #: the store the copies were read from (identity, never compared by value)
    store: Any = field(default=None, compare=False, repr=False)


class CreateError(ValueError):
    """A create the real write would refuse (missing or non-dict parent, an
    existing target, a value the type policy rejects). ``path`` names it."""

    def __init__(self, path: str, reason: str):
        super().__init__(f"{path}: {reason}")
        self.path = path
        self.reason = reason


def write_chip_folder(folder: Path, state: dict, wiring: dict) -> None:
    """Write ``state.json`` and ``wiring.json`` into *folder*."""
    folder = Path(folder)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")


def apply_creates(state: dict, wiring: dict, creates, *,
                  type_policy=None) -> tuple[dict, dict]:
    """*state* / *wiring* (modified in place and returned) with *creates*
    applied by ``Modifier.create_subtree`` on a detached store built from
    them: the code the real write runs. The detached store is never
    registered, saved or reloaded."""
    from quam_state_manager.core.loader import QuamStore
    from quam_state_manager.core.modifier import Modifier
    detached = QuamStore.from_dicts(state, wiring)
    detached.type_policy = type_policy
    mod = Modifier(detached)
    for path, value in creates:
        try:
            mod.create_subtree(path, value)
        except Exception as exc:  # noqa: BLE001 -- reported as a failed check
            raise CreateError(path, f"{type(exc).__name__}: {exc}") from None
    return detached.state, detached.wiring


def make_chip_copies(store, creates: Iterable[tuple[str, Any]] = (), *,
                     prefix: str = "sm_chipcopy_") -> ChipCopies:
    """Two temporary chip folders and the store's sequence value at the read.

    The lock is held only for the read. A create the real write would refuse
    raises :class:`CreateError` before any folder exists; a failed write
    removes everything it made and re-raises."""
    creates = [(str(p), v) for p, v in creates]
    with store._lock:
        seq = store.mutation_seq
        policy = getattr(store, "type_policy", None)
        root_class = (getattr(store, "merged", None) or {}).get("__class__")
        try:
            blob = marshal.dumps((store.state, store.wiring))
            raw = None
        except (ValueError, TypeError):
            blob = None
            raw = copy.deepcopy((store.state, store.wiring))

    def docs():
        return marshal.loads(blob) if blob is not None else copy.deepcopy(raw)

    state, wiring = docs()
    a_state, a_wiring = apply_creates(*docs(), creates, type_policy=policy)
    root = Path(tempfile.mkdtemp(prefix=prefix))
    try:
        before = root / "before"
        after = root / "after"
        before.mkdir()
        after.mkdir()
        write_chip_folder(before, state, wiring)
        write_chip_folder(after, a_state, a_wiring)
    except BaseException:
        shutil.rmtree(str(root.resolve()), ignore_errors=True)
        raise
    return ChipCopies(before=before, after=after, seq=seq, root=root,
                      root_class=root_class if isinstance(root_class, str) else None,
                      store=store)


REMOVE_TRIES = 5
REMOVE_PAUSE_S = 0.2


def remove_chip_copies(copies: ChipCopies | None) -> str | None:
    """Remove both folders (and their common parent). On Windows a handle
    of a stopped child may still be open for a moment, so the removal is
    retried; returns the path left behind when it never succeeds (else
    None), so the caller can show it and retry."""
    if copies is None:
        return None
    target = Path(copies.root).resolve()
    for n in range(REMOVE_TRIES):
        try:
            shutil.rmtree(str(target))
            return None
        except FileNotFoundError:
            return None
        except OSError:
            if n + 1 < REMOVE_TRIES:
                time.sleep(REMOVE_PAUSE_S)
    return str(target) if target.exists() else None


def store_at_seq(store, copies) -> bool:
    """True when *store* is the store the copies were read from and is
    still at their sequence value (read under the store lock). *copies* is
    a :class:`ChipCopies` (or a bare sequence value, which checks the
    counter only)."""
    if isinstance(copies, ChipCopies):
        if copies.store is not None and copies.store is not store:
            return False
        seq = copies.seq
    else:
        seq = copies
    with store._lock:
        return store.mutation_seq == seq
