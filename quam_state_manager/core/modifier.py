"""Edit QUAM state values with tracking, undo, and batch support.

Modifier wraps a QuamStore and provides safe, type-checked mutations that:
  - Record every change in the store's change_log
  - Invalidate the pointer resolver cache
  - Incrementally update the search index
  - Support single edits, batch edits, and undo

All mutations acquire the store's RLock for thread safety.
"""

from __future__ import annotations

import copy
import logging
import math
import re
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

from quam_state_manager.core.loader import ChangeEntry, QuamStore

logger = logging.getLogger(__name__)


class Modifier:
    """Applies tracked edits to a QuamStore."""

    def __init__(self, store: QuamStore) -> None:
        self.store = store

    # ------------------------------------------------------------------
    # Single edit
    # ------------------------------------------------------------------

    def set_value(self, dot_path: str, new_value: Any, *, _defer_hooks: bool = False,
                  coerce: bool = True, group_id: str | None = None,
                  enforce: bool = True) -> ChangeEntry:
        """Set a single value by dot-path in the merged (and source) dict.

        Steps:
          1. Navigate to parent dict + leaf key.
          2. Read old value.
          3. Type-check: when the store carries a ``type_policy`` and the
             key's expected type is KNOWN (env schema or user assignment),
             judge the value against it — a mismatch raises
             :class:`~core.type_policy.TypeMismatchError` (a ``TypeError``).
             Pointer strings and ``None`` always bypass. Otherwise fall back
             to the old-value coercion (type-check only, no range validation
             -- researchers know their values).
          4. Write into both the source dict (state or wiring) AND merged.
          5. Record a ChangeEntry.
          6. Unless ``_defer_hooks`` is True, clear the pointer cache and
             update the search index.

        If the field holds a ``#/`` pointer string, the pointer itself is
        updated -- we never chase to the resolved target.

        ``coerce=False`` skips the old-value coercion step. This is for the
        rare, deliberate type *change* — e.g. converting a literal
        ``downconverter_frequency`` float into a ``#/`` JSON pointer to its
        paired output's ``upconverter_frequency`` (a float→pointer-string
        change the coercer would otherwise reject).

        ``enforce=False`` skips the type-policy gate too. ONLY for the
        pull-replay (previously-accepted values replayed verbatim — blocking
        mid-replay would be data loss) and the Pulses literal mode (the
        explicit, audited type-CHANGE surface). ``enforce`` is orthogonal to
        ``coerce``; one ack never collapses two gates.
        """
        with self.store._lock:
            parent_merged, leaf_key = _navigate_to_parent(self.store.merged, dot_path)
            lk = _key_for(parent_merged, leaf_key, dot_path)
            old_value = parent_merged[lk]

            coerced = self._checked_value(dot_path, old_value, new_value,
                                          coerce=coerce, enforce=enforce)

            source_file = self.store.source_file_for(dot_path)
            source_dict = self.store.wiring if source_file == "wiring" else self.store.state
            _write_to_nested(source_dict, dot_path, coerced)

            parent_merged[lk] = coerced

            entry = ChangeEntry(
                dot_path=dot_path,
                old_value=old_value,
                new_value=coerced,
                source_file=source_file,
                group_id=group_id,
            )
            self.store.change_log.append(entry)
            self.store.mutation_seq += 1

            if not _defer_hooks:
                self.store._clear_pointer_cache()
                if self.store.search_index is not None:
                    self.store.search_index.update_entry(dot_path, coerced)

            logger.info("set_value %s: %r -> %r (%s)", dot_path, old_value, coerced, source_file)
            return entry

    def _checked_value(self, dot_path: str, old_value: Any, new_value: Any, *,
                       coerce: bool, enforce: bool) -> Any:
        """The type gate every single-value write passes through.

        Pointer strings and None ALWAYS pass verbatim. When an ENFORCED
        expected type is known (env schema / user assignment) and ``enforce``,
        the policy judges (raising on mismatch) and applies only the
        old-value numeric reconciliation. Otherwise: today's ``_type_coerce``
        iff ``coerce`` — byte-identical legacy behavior (empty-policy golden).
        """
        if isinstance(new_value, str) and new_value.startswith(("#/", "#./", "#../")):
            return new_value
        if new_value is None:
            return new_value
        policy = getattr(self.store, "type_policy", None)
        if policy is not None and enforce:
            try:
                expected = policy.expected_for(self.store.merged, dot_path,
                                               infer=False)
            except Exception:  # noqa: BLE001 — a policy bug must never brick edits
                logger.warning("type-policy resolution failed for %s", dot_path,
                               exc_info=True)
                expected = None
            if expected is not None and expected.enforced:
                return policy.check(expected, new_value, path=dot_path,
                                    old_value=old_value)
        return _type_coerce(old_value, new_value) if coerce else new_value

    # ------------------------------------------------------------------
    # Create (new key / subtree)
    # ------------------------------------------------------------------

    def create_subtree(self, dot_path: str, value: Any, *,
                       group_id: str | None = None,
                       enforce: bool = True) -> ChangeEntry:
        """Create a brand-new key (or subtree) at *dot_path*.

        The parent path must already exist; *dot_path* itself must NOT exist
        (else KeyError, to prevent silent overwrites).  *value* may be a
        scalar, list, or nested dict -- the whole subtree is inserted in
        one shot.

        When the store carries a ``type_policy``, every scalar leaf of the
        new subtree is checked against its expected type (embedded
        ``__class__`` dicts anchor, so a created pulse's fields are
        env-checked immediately); ``enforce=False`` skips (pull-replay).

        One :class:`ChangeEntry` is logged for the whole creation with
        ``created=True``; undoing it deletes the new key entirely (no orphan
        intermediate dicts).  Every leaf inside the new subtree is registered
        with the search index.
        """
        with self.store._lock:
            keys = dot_path.split(".")
            if not keys or not all(keys):
                raise ValueError(f"Invalid dot_path: {dot_path!r}")

            policy = getattr(self.store, "type_policy", None)
            if policy is not None and enforce:
                policy.check_subtree(self.store.merged, dot_path, value)

            leaf_key = keys[-1]
            parent_keys = keys[:-1]

            parent_merged = _navigate_to_dict(self.store.merged, parent_keys, dot_path)
            if leaf_key in parent_merged:
                raise KeyError(f"Cannot create {dot_path!r}: key already exists")

            source_file = self.store.source_file_for(dot_path)
            source_dict = self.store.wiring if source_file == "wiring" else self.store.state

            is_top_level = len(keys) == 1
            if is_top_level:
                parent_source = source_dict
            else:
                parent_source = _navigate_to_dict(source_dict, parent_keys, dot_path)
                if leaf_key in parent_source:  # defensive; should not trigger if merged check passed
                    raise KeyError(f"Cannot create {dot_path!r}: key already exists in source")

            # Deep-copy so caller mutations don't leak in.  For non-top-level
            # paths, merged and source share nested dicts, so a single write
            # is sufficient; for top-level keys we must write both.
            written = copy.deepcopy(value)
            parent_merged[leaf_key] = written
            if is_top_level:
                parent_source[leaf_key] = written

            entry = ChangeEntry(
                dot_path=dot_path,
                old_value=None,
                new_value=written,
                source_file=source_file,
                created=True,
                group_id=group_id,
            )
            self.store.change_log.append(entry)
            self.store.mutation_seq += 1

            self.store._clear_pointer_cache()
            if self.store.search_index is not None:
                for leaf_path, leaf_value in _enumerate_leaves(written, dot_path):
                    self.store.search_index.add_entry(leaf_path, leaf_value, source_file=source_file)

            logger.info("create_subtree %s (%s)", dot_path, source_file)
            return entry

    # ------------------------------------------------------------------
    # Delete (key / subtree)
    # ------------------------------------------------------------------

    def delete_subtree(self, dot_path: str, *,
                       group_id: str | None = None) -> ChangeEntry:
        """Delete the key (or whole subtree) at *dot_path*.

        The path must exist (else KeyError). The removed value is deep-copied
        into the :class:`ChangeEntry` (``deleted=True, new_value=None``) so
        undo/discard restore it exactly; every contained leaf is removed from
        the search index. Works for dict subtrees, scalars, and string-alias
        operations alike.
        """
        with self.store._lock:
            keys = dot_path.split(".")
            if not keys or not all(keys):
                raise ValueError(f"Invalid dot_path: {dot_path!r}")
            source_file = self.store.source_file_for(dot_path)

            removed = self._remove_at(dot_path, source_file)
            old_value = copy.deepcopy(removed)

            entry = ChangeEntry(
                dot_path=dot_path,
                old_value=old_value,
                new_value=None,
                source_file=source_file,
                deleted=True,
                group_id=group_id,
            )
            self.store.change_log.append(entry)
            self.store.mutation_seq += 1

            self.store._clear_pointer_cache()
            if self.store.search_index is not None:
                for leaf_path, _ in _enumerate_leaves(removed, dot_path):
                    self.store.search_index.remove_entry(leaf_path)

            logger.info("delete_subtree %s (%s)", dot_path, source_file)
            return entry

    # ------------------------------------------------------------------
    # Rename (atomic create-new + delete-old compose)
    # ------------------------------------------------------------------

    def new_group_id(self) -> str:
        """A fresh, unique group id for a multi-entry user action (batch, rename
        + its pointer retargets) so one Ctrl+Z undoes them atomically. Monotonic
        ``mutation_seq`` guarantees uniqueness across actions."""
        with self.store._lock:
            return f"grp{self.store.mutation_seq}"

    def rename_subtree(self, old_path: str, new_path: str,
                       new_value: Any | None = None,
                       group_id: str | None = None) -> list[ChangeEntry]:
        """Move the subtree at *old_path* to *new_path* atomically.

        Composed as ``create_subtree(new) + delete_subtree(old)`` under one
        lock hold — two change-log entries (each independently undoable and
        replayable; LIFO undo restores the old name first, then removes the
        new). The collision check on *new_path* happens before anything is
        destroyed; if the delete half fails, the created half is rolled back.

        *new_value* optionally substitutes the copied value (used by the
        pulse rename flow to rewrite internal self-pointers); default is a
        deep copy of the old value.
        """
        if old_path == new_path:
            raise ValueError("rename: old and new paths are identical")
        with self.store._lock:
            current = _navigate_to_parent(self.store.merged, old_path)
            if isinstance(current[0], list):
                raise ValueError(
                    f"rename of a list element is not supported ({old_path!r}) — "
                    "edit the whole array instead"
                )
            value = current[0][current[1]]
            payload = copy.deepcopy(value) if new_value is None else new_value

            # One group id so a single Ctrl+Z undoes the whole rename (and any
            # pointer retargets the caller stamps with the same id) atomically.
            gid = group_id or f"grp{self.store.mutation_seq}"
            create_entry = self.create_subtree(new_path, payload, group_id=gid)
            try:
                delete_entry = self.delete_subtree(old_path, group_id=gid)
            except Exception:
                self._rollback([create_entry])
                raise
            return [create_entry, delete_entry]

    # ------------------------------------------------------------------
    # Batch edit
    # ------------------------------------------------------------------

    def batch_set(self, updates: dict[str, Any]) -> list[ChangeEntry]:
        """Apply multiple edits atomically.

        All-or-nothing: if any single edit fails type-coercion or navigation,
        every preceding edit in this batch is rolled back.

        Cache clear and search index update happen ONCE after all edits
        succeed, not per-edit (the performance optimization noted in the plan).
        """
        with self.store._lock:
            entries: list[ChangeEntry] = []
            # Tag every edit in this batch with one group id so a single Ctrl+Z
            # undoes the whole batch atomically (LIFO within the group). A batch
            # of exactly one edit still groups, which is harmless (undo_group
            # falls back to popping the single trailing entry).
            gid = f"grp{self.store.mutation_seq}"
            try:
                for dot_path, new_value in updates.items():
                    entry = self.set_value(dot_path, new_value, _defer_hooks=True,
                                           group_id=gid)
                    entries.append(entry)
            except Exception:
                self._rollback(entries)
                raise

            self.store._clear_pointer_cache()
            if self.store.search_index is not None:
                for entry in entries:
                    self.store.search_index.update_entry(entry.dot_path, entry.new_value)

            return entries

    # ------------------------------------------------------------------
    # Undo
    # ------------------------------------------------------------------

    def undo(self) -> ChangeEntry | None:
        """Undo the most recent change by restoring its old_value (deleting
        for creations, re-inserting for deletions).

        Returns the reversed ChangeEntry, or None if the log is empty.

        Revert-then-pop (same order as :meth:`discard`): if the revert
        raises, the entry stays in the log so the change is neither lost
        nor half-applied.
        """
        with self.store._lock:
            if not self.store.change_log:
                return None

            last = self.store.change_log[-1]
            self._revert_entry(last)
            self.store.change_log.pop()
            logger.info(
                "undo %s%s",
                "(create) " if last.created else "(delete) " if last.deleted else "",
                last.dot_path,
            )
            return last

    def undo_group(self) -> list[ChangeEntry]:
        """Undo the last USER ACTION as one unit (for Ctrl+Z).

        If the last change_log entry belongs to a group (batch edit, rename),
        pop and revert every trailing entry sharing that ``group_id`` — LIFO,
        so a batch/rename undoes atomically. Otherwise pop the single last
        entry (identical to :meth:`undo`). Returns the reverted entries (most
        recent first), or ``[]`` if the log is empty.

        Revert-then-pop per entry, like :meth:`undo`: if a revert raises (e.g.
        the clobber guard), it propagates and the remaining entries stay in the
        log — the group is left partially reverted but the log still mirrors
        state (each popped entry was fully reverted before it was popped).
        """
        with self.store._lock:
            if not self.store.change_log:
                return []
            gid = self.store.change_log[-1].group_id
            reverted: list[ChangeEntry] = []
            if gid is None:
                last = self.store.change_log[-1]
                self._revert_entry(last)
                self.store.change_log.pop()
                reverted.append(last)
            else:
                while (self.store.change_log
                       and self.store.change_log[-1].group_id == gid):
                    entry = self.store.change_log[-1]
                    self._revert_entry(entry)
                    self.store.change_log.pop()
                    reverted.append(entry)
            logger.info("undo_group reverted %d entr%s", len(reverted),
                        "y" if len(reverted) == 1 else "ies")
            return reverted

    def discard(self, index: int, expect_path: str | None = None) -> ChangeEntry | None:
        """Discard a specific change by its index in the change log.

        Restores the old value (or deletes, if the entry was a creation) and
        removes the entry.  Returns the discarded ChangeEntry, or None if the
        index is out of range, OR if ``expect_path`` is given and does not
        match the ``dot_path`` of the entry currently at ``index``.

        The ``expect_path`` guard defends a STALE tray: the per-change ✕ posts
        an index frozen at render time, but another tab's discard/undo (or a
        batch undo_group) shifts every index below the removed entry — without
        the check a stale click would silently revert a DIFFERENT change than
        the one the user clicked.
        """
        with self.store._lock:
            if index < 0 or index >= len(self.store.change_log):
                return None

            entry = self.store.change_log[index]
            if expect_path is not None and entry.dot_path != expect_path:
                return None   # stale tray — the index no longer names this change
            self._revert_entry(entry)
            self.store.change_log.pop(index)
            logger.info("discard [%d] %s%s", index, "(create) " if entry.created else "", entry.dot_path)
            return entry

    # ------------------------------------------------------------------
    # Inspect
    # ------------------------------------------------------------------

    def get_change_log(self) -> list[ChangeEntry]:
        """Return a copy of the change log (most recent last)."""
        return list(self.store.change_log)

    @property
    def has_unsaved_changes(self) -> bool:
        return len(self.store.change_log) > 0

    # ------------------------------------------------------------------
    # Internal rollback
    # ------------------------------------------------------------------

    def _rollback(self, entries: list[ChangeEntry]) -> list[ChangeEntry]:
        """Undo a list of entries in reverse order (used by batch_set on failure).

        Returns the list of entries that could NOT be rolled back; they stay in
        ``change_log`` so the log continues to reflect actual state. The
        successfully rolled-back entries are removed.

        Failure handling is two-phase to keep the log consistent with state:
          1. Restore source dict and merged dict.
          2. Only on full success, remove the entry from change_log.
        If either restore step raises, both writes are reverted (best-effort)
        and the entry stays in change_log.
        """
        failures: list[ChangeEntry] = []
        for entry in reversed(entries):
            try:
                self._revert_entry(entry, _skip_cache_clear=True)
            except Exception:
                logger.exception("Rollback failed for %s -- entry kept in change_log", entry.dot_path)
                failures.append(entry)
                continue

            try:
                self.store.change_log.remove(entry)
            except ValueError:
                # Entry no longer in log (concurrent mutation) -- state is
                # already restored, so this is a soft failure.
                logger.warning("Rollback: entry %s already removed from change_log", entry.dot_path)

        self.store._clear_pointer_cache()
        return failures

    # ------------------------------------------------------------------
    # Shared revert path used by undo / discard / _rollback
    # ------------------------------------------------------------------

    def _remove_at(self, dot_path: str, source_file: str) -> Any:
        """Pop the key at *dot_path* from merged (and source where needed).

        Shared by :meth:`delete_subtree` and the created-entry revert. For
        nested paths merged and the source dict share the container objects
        (loader merge is a shallow top-level copy), so popping the merged
        parent already hit the source; only top-level keys need both pops.
        Raises KeyError when the path does not exist.
        """
        keys = dot_path.split(".")
        leaf_key = keys[-1]
        parent_keys = keys[:-1]
        parent_merged = _navigate_to_dict(self.store.merged, parent_keys, dot_path)
        if leaf_key not in parent_merged:
            raise KeyError(f"Cannot delete {dot_path!r}: key does not exist")
        removed = parent_merged.pop(leaf_key)

        source_dict = self.store.wiring if source_file == "wiring" else self.store.state
        if len(keys) == 1:
            source_dict.pop(leaf_key, None)
        else:
            try:
                parent_source = _navigate_to_dict(source_dict, parent_keys, dot_path)
                parent_source.pop(leaf_key, None)
            except KeyError:
                pass  # nested containers are shared with merged — already popped
        return removed

    def _revert_entry(self, entry: ChangeEntry, *, _skip_cache_clear: bool = False) -> None:
        """Reverse a single ChangeEntry in place.

        ``created=True``: delete the subtree from both merged and source
        dicts and drop every contained leaf from the search index.
        ``deleted=True``: re-insert a fresh deep copy of ``old_value`` and
        re-register its leaves (refuses to clobber if the key was re-created
        since the delete — e.g. delete X → create X → discard the delete).
        Mutations: restore ``old_value`` in both dicts and update the index.
        """
        if entry.created:
            try:
                removed = self._remove_at(entry.dot_path, entry.source_file)
            except KeyError:
                # The key is already gone. If a LATER pending delete of the
                # same key (or an ancestor) exists, silently succeeding here
                # would let discarding that delete resurrect the created
                # subtree with an empty log — untracked divergence. Refuse
                # (symmetric with the deleted-branch clobber guard).
                for other in self.store.change_log:
                    if other is entry or not other.deleted:
                        continue
                    if (other.dot_path == entry.dot_path
                            or entry.dot_path.startswith(other.dot_path + ".")):
                        raise KeyError(
                            f"Cannot discard creation of {entry.dot_path!r}:"
                            " a later delete of the same key is pending —"
                            " discard that delete first"
                        ) from None
                removed = None
            if self.store.search_index is not None and removed is not None:
                for leaf_path, _ in _enumerate_leaves(removed, entry.dot_path):
                    self.store.search_index.remove_entry(leaf_path)
        elif entry.deleted:
            keys = entry.dot_path.split(".")
            leaf_key = keys[-1]
            parent_keys = keys[:-1]
            parent_merged = _navigate_to_dict(self.store.merged, parent_keys, entry.dot_path)
            if leaf_key in parent_merged:
                raise KeyError(
                    f"Cannot restore deleted {entry.dot_path!r}: the key exists"
                    " again (re-created since the delete) — undo/discard that"
                    " change first"
                )
            # Fresh deep copy on every restore so repeated undo/redo cycles
            # can't alias the log entry to the live tree.
            restored = copy.deepcopy(entry.old_value)
            parent_merged[leaf_key] = restored
            if len(keys) == 1:
                source_dict = (self.store.wiring if entry.source_file == "wiring"
                               else self.store.state)
                source_dict[leaf_key] = restored

            if self.store.search_index is not None:
                for leaf_path, leaf_value in _enumerate_leaves(restored, entry.dot_path):
                    self.store.search_index.add_entry(
                        leaf_path, leaf_value, source_file=entry.source_file)
        else:
            parent_merged, leaf_key = _navigate_to_parent(self.store.merged, entry.dot_path)
            parent_merged[_key_for(parent_merged, leaf_key, entry.dot_path)] = entry.old_value

            source_dict = self.store.wiring if entry.source_file == "wiring" else self.store.state
            _write_to_nested(source_dict, entry.dot_path, entry.old_value)

            if self.store.search_index is not None:
                self.store.search_index.update_entry(entry.dot_path, entry.old_value)

        self.store.mutation_seq += 1
        if not _skip_cache_clear:
            self.store._clear_pointer_cache()


# ======================================================================
# Internal helpers
# ======================================================================


_INDEX_RE = re.compile(r"\d+")


def _list_index(key: str, dot_path: str) -> int:
    """Strict non-negative list index from a dot-path segment.

    Rejects ``-1``/``+3``/``0x2``-style segments that ``int()`` would accept —
    Python's negative indexing would otherwise silently edit the WRONG element
    (a stale bookmark or off-by-one livediff path writing the last matrix cell).
    """
    if not _INDEX_RE.fullmatch(key):
        raise KeyError(f"Cannot index list with {key!r} at {dot_path!r}")
    return int(key)


def _key_for(parent: Any, leaf_key: str, dot_path: str):
    """The concrete subscript for *parent*: int index for lists, key for dicts.

    Number-keyed DICTS (``ports.mw_outputs.con1.1.2``) keep the string key —
    only an actual JSON list gets an integer index.
    """
    if isinstance(parent, list):
        return _list_index(leaf_key, dot_path)
    return leaf_key


def _navigate_to_parent(root: dict, dot_path: str) -> tuple[dict, str]:
    """Walk a nested dict by dot-path, returning (parent_dict, leaf_key).

    Raises KeyError if the path doesn't exist.
    """
    keys = dot_path.split(".")
    current: Any = root
    for key in keys[:-1]:
        if isinstance(current, dict):
            if key not in current:
                raise KeyError(f"Key {key!r} not found while navigating {dot_path!r}")
            current = current[key]
        elif isinstance(current, list):
            try:
                current = current[_list_index(key, dot_path)]
            except IndexError as exc:
                raise KeyError(f"Cannot index list with {key!r} at {dot_path!r}") from exc
        else:
            raise KeyError(f"Cannot traverse into {type(current).__name__} at {key!r} in {dot_path!r}")

    leaf_key = keys[-1]
    if isinstance(current, dict):
        if leaf_key not in current:
            raise KeyError(f"Leaf key {leaf_key!r} not found at {dot_path!r}")
        return current, leaf_key
    elif isinstance(current, list):
        try:
            _ = current[_list_index(leaf_key, dot_path)]
        except IndexError as exc:
            raise KeyError(f"Cannot index list with {leaf_key!r} at {dot_path!r}") from exc
        return current, leaf_key  # type: ignore[return-value]
    else:
        raise KeyError(f"Parent at {dot_path!r} is {type(current).__name__}, not dict or list")


def _navigate_to_dict(root: dict, keys: list[str], full_dot_path: str) -> dict:
    """Walk *root* by a sequence of keys, returning the dict at the end.

    Used by :meth:`Modifier.create_subtree` (and revert) to find the parent
    container of a key being created or deleted.  Unlike
    :func:`_navigate_to_parent`, *keys* can be empty -- meaning *root* itself
    is the target dict.

    Raises KeyError if any intermediate key is missing or the path traverses
    a non-dict node.
    """
    current: Any = root
    for key in keys:
        if not isinstance(current, dict):
            raise KeyError(
                f"Cannot traverse {type(current).__name__} at {key!r} in {full_dot_path!r}"
            )
        if key not in current:
            raise KeyError(f"Parent key {key!r} not found while creating {full_dot_path!r}")
        current = current[key]
    if not isinstance(current, dict):
        raise KeyError(
            f"Parent of {full_dot_path!r} is {type(current).__name__}, not dict"
        )
    return current


def _enumerate_leaves(value: Any, root_path: str) -> Iterator[tuple[str, Any]]:
    """Yield ``(dot_path, leaf_value)`` for every terminal value inside *value*.

    Used to register newly-created leaves with the search index (and to
    remove them on undo).  Lists are treated as opaque leaves -- we do not
    recurse into list elements.
    """
    if not isinstance(value, dict):
        yield root_path, value
        return
    if not value:  # empty dict still counts as a leaf-shaped node
        yield root_path, value
        return
    for key, sub in value.items():
        child_path = f"{root_path}.{key}"
        if isinstance(sub, dict) and sub:
            yield from _enumerate_leaves(sub, child_path)
        else:
            yield child_path, sub


def _write_to_nested(root: dict, dot_path: str, value: Any) -> None:
    """Write a value into a nested dict by dot-path, creating no new keys.

    The path must already exist (same constraint as _navigate_to_parent).
    For wiring paths like ``"wiring.qubits.qA1.xy.opx_output"``, the
    top-level key in wiring.json is ``"wiring"`` so the full path works.
    """
    keys = dot_path.split(".")
    current: Any = root
    for key in keys[:-1]:
        if isinstance(current, dict):
            current = current[key]
        elif isinstance(current, list):
            current = current[_list_index(key, dot_path)]
        else:
            return

    leaf = keys[-1]
    if isinstance(current, dict):
        current[leaf] = value
    elif isinstance(current, list):
        current[_list_index(leaf, dot_path)] = value


def _type_coerce(old_value: Any, new_value: Any) -> Any:
    """Coerce new_value to match old_value's type.

    Rules:
      - If new is a QUAM ``#/`` / ``#./`` / ``#../`` pointer string, accept it as-is
        for ANY field — a pointer resolves to the field's type on read, so a
        deliberate literal→pointer change (e.g. a float ``downconverter_frequency``
        relinked to its paired ``upconverter_frequency``) must never be cast/rejected.
        This is what lets such an edit survive a change-log replay (pull & re-apply).
      - If old is None, accept anything as-is.
      - If old is float, cast to float.
      - If old is int (but not bool), cast to int.
      - If old is bool, accept true/false, yes/no, on/off, 1/0 (case-insensitive);
        an unrecognized string raises (never silently True).
      - If old is str, cast to str.
      - If old is list/dict, accept list/dict (no deep coercion).
      - Otherwise, return new_value as-is.

    Raises TypeError if the cast fails (e.g. "abc" to float).
    """
    if isinstance(new_value, str) and new_value.startswith(("#/", "#./", "#../")):
        return new_value   # a JSON pointer is valid for any field — never coerce it

    if old_value is None:
        return new_value

    if isinstance(old_value, bool):
        if isinstance(new_value, bool):
            return new_value
        if isinstance(new_value, str):
            low = new_value.strip().lower()
            if low in ("true", "t", "yes", "y", "on", "1"):
                return True
            if low in ("false", "f", "no", "n", "off", "0"):
                return False
            # Reject an unrecognized string rather than silently returning
            # bool("anything") == True (a typo'd "flase" must not flip to True).
            raise TypeError(
                f"Cannot coerce {new_value!r} to bool — use true/false, yes/no, "
                f"on/off, or 1/0 (old value was {old_value!r})"
            )
        return bool(new_value)

    if isinstance(old_value, int) and not isinstance(old_value, bool):
        try:
            as_float = float(new_value)
        except (ValueError, TypeError) as exc:
            raise TypeError(f"Cannot coerce {new_value!r} to int (old value was {old_value!r})") from exc
        if not math.isfinite(as_float):
            # json.dump would emit a literal Infinity/NaN token — invalid strict
            # JSON that breaks every non-Python consumer of state.json.
            raise TypeError(f"Non-finite value {new_value!r} cannot be stored in state.json")
        # A non-integral edit to an int field must NOT silently truncate
        # (e.g. amplitude stored as int 1, edited to 0.3 → int(0.3)==0, a
        # silent data-loss + a manufactured working-copy divergence). Keep
        # the fractional value as a float; only collapse to int when the
        # value is exactly integral (real QUAM data has int↔float drift and
        # trusts researcher input — see the Type Coercion philosophy).
        return int(as_float) if as_float.is_integer() else as_float

    if isinstance(old_value, float):
        try:
            as_float = float(new_value)
        except (ValueError, TypeError) as exc:
            raise TypeError(f"Cannot coerce {new_value!r} to float (old value was {old_value!r})") from exc
        if not math.isfinite(as_float):
            raise TypeError(f"Non-finite value {new_value!r} cannot be stored in state.json")
        return as_float

    if isinstance(old_value, str):
        return str(new_value)

    if isinstance(old_value, list):
        if not isinstance(new_value, list):
            raise TypeError(f"Expected list, got {type(new_value).__name__} (old value was list)")
        return new_value

    if isinstance(old_value, dict):
        if not isinstance(new_value, dict):
            raise TypeError(f"Expected dict, got {type(new_value).__name__} (old value was dict)")
        return new_value

    return new_value
