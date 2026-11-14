"""Throwaway chip copies for a lab-side check.

:func:`make_chip_copies` reads the store's in-memory state and wiring
documents (unsaved edits included) and its ``mutation_seq`` in ONE hold of the
store lock, releases the lock, and writes two temporary folders:

- ``before``: the documents as they are;
- ``after``: the same documents plus a list of creates ``[(dot_path, value)]``,
  each landing in state.json or wiring.json by the store's own rule
  (``QuamStore.source_file_for``, the split the modifier uses).

Nothing in the store changes: no change-log entry, no undo step, no working
copy or live file is touched. :func:`store_at_seq` answers, under the lock,
whether the store is still at the sequence value the copies were read at.
:func:`write_chip_folder` is the one folder writer (the add / remove env check
uses it too).
"""

from __future__ import annotations

import copy
import json
import marshal
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class ChipCopies:
    before: Path
    after: Path
    seq: int
    root: Path


def write_chip_folder(folder: Path, state: dict, wiring: dict) -> None:
    """Write ``state.json`` and ``wiring.json`` into *folder*."""
    folder = Path(folder)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")


def _set_path(doc: dict, dot_path: str, value: Any) -> None:
    segs = dot_path.split(".")
    node = doc
    for seg in segs[:-1]:
        nxt = node.get(seg) if isinstance(node, dict) else None
        if not isinstance(nxt, dict):
            nxt = {}
            node[seg] = nxt
        node = nxt
    node[segs[-1]] = copy.deepcopy(value)


def make_chip_copies(store, creates: Iterable[tuple[str, Any]] = (), *,
                     prefix: str = "sm_chipcopy_") -> ChipCopies:
    """Two temporary chip folders and the store's sequence value at the read.

    The lock is held only for the read; a failed write removes everything it
    made and re-raises."""
    creates = [(str(p), v) for p, v in creates]
    with store._lock:
        seq = store.mutation_seq
        targets = [store.source_file_for(p) for p, _v in creates]
        try:
            blob = marshal.dumps((store.state, store.wiring))
            raw = None
        except (ValueError, TypeError):
            blob = None
            raw = copy.deepcopy((store.state, store.wiring))
    state, wiring = marshal.loads(blob) if blob is not None else raw
    root = Path(tempfile.mkdtemp(prefix=prefix))
    try:
        before = root / "before"
        after = root / "after"
        before.mkdir()
        after.mkdir()
        write_chip_folder(before, state, wiring)
        for (path, value), where in zip(creates, targets):
            _set_path(wiring if where == "wiring" else state, path, value)
        write_chip_folder(after, state, wiring)
    except BaseException:
        shutil.rmtree(str(root.resolve()), ignore_errors=True)
        raise
    return ChipCopies(before=before, after=after, seq=seq, root=root)


def remove_chip_copies(copies: ChipCopies | None) -> None:
    """Remove both folders (and their common parent)."""
    if copies is None:
        return
    shutil.rmtree(str(Path(copies.root).resolve()), ignore_errors=True)


def store_at_seq(store, seq: int) -> bool:
    """True when the store is still at *seq* (read under the store lock)."""
    with store._lock:
        return store.mutation_seq == seq
