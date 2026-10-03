"""Durable pending work, separate from saved undo history (docs/265).

Only attached web working copies participate. Individual log/row mutations
and completed batches write one fsync'd, atomically replaced sidecar under
the store lock. The working
pair and live pair are never written by recovery. Failed recovery preserves
the checkpoint for inspection, and restores no part of it.
"""
from __future__ import annotations

import copy
import json
import os
import uuid
from dataclasses import asdict
from contextlib import contextmanager
from pathlib import Path

from . import safe_io, working_copy
from .loader import ChangeEntry, merge_state_wiring

_FLAGS = ("working_dirty", "staged_base", "pending_reapply", "pending_reapply_orig")


def sidecar_path(wc) -> Path:
    return wc.working_folder.parent / f"{wc.key}.pending_tray.json"


class PendingLog(list):
    """A list with one checkpoint hook for all existing log mutation sites."""
    def __init__(self, entries, checkpoint):
        super().__init__(entries)
        self.checkpoint = checkpoint
        self._defer = 0
        self._needs_checkpoint = False
        self._bind()

    def _bind(self):
        for entry in self:
            object.__setattr__(entry, "_pending_changed", self._changed)

    def _changed(self):
        if self._defer:
            self._needs_checkpoint = True
            return
        self._bind()
        self.checkpoint()

    def append(self, entry):
        super().append(entry)
        self._changed()

    def extend(self, entries):
        super().extend(entries)
        self._changed()

    def insert(self, index, entry):
        super().insert(index, entry)
        self._changed()

    def pop(self, index=-1):
        entry = super().pop(index)
        self._changed()
        return entry

    def remove(self, entry):
        super().remove(entry)
        self._changed()

    def clear(self):
        super().clear()
        self._changed()

    def __setitem__(self, index, value):
        super().__setitem__(index, value)
        self._changed()

    def __delitem__(self, index):
        super().__delitem__(index)
        self._changed()

    def __iadd__(self, entries):
        self.extend(entries)
        return self

    def reverse(self):
        super().reverse()
        self._changed()

    def sort(self, *args, **kwargs):
        super().sort(*args, **kwargs)
        self._changed()


@contextmanager
def batch(store):
    """Checkpoint a complete batch, including rollback and actor stamps.

    Individual edits still checkpoint immediately. The lock keeps unrelated
    requests from joining this batch or observing its incomplete document.
    """
    with store._lock:
        log = store.change_log
        if not isinstance(log, PendingLog):
            yield
            return
        log._defer += 1
        try:
            yield
        finally:
            log._defer -= 1
            if not log._defer and log._needs_checkpoint:
                log._needs_checkpoint = False
                log._changed()


def checkpoint(ctx):
    store, wc = ctx["store"], ctx["working_copy"]
    path = sidecar_path(wc)
    with store._lock:
        if ctx.get("_pending_tray_retired"):
            return
        if not store.change_log:
            path.unlink(missing_ok=True)
            return
        state, wiring = safe_io.read_state_wiring(wc.working_folder)
        doc = {
            "version": 1, "live_folder": str(wc.live_folder),
            "disk_hash": working_copy.content_hash(state, wiring),
            "synced_live_hash": wc.synced_live_hash,
            "state": store.state, "wiring": store.wiring,
            "entries": [asdict(e) for e in store.change_log],
            "mutation_seq": store.mutation_seq,
            "flags": {k: ctx[k] for k in _FLAGS if k in ctx},
        }
        safe_io.atomic_write_json(path, doc)


def _restore(ctx):
    store, wc = ctx["store"], ctx["working_copy"]
    path = sidecar_path(wc)
    paths = []
    try:
        if not path.exists():
            return
        doc = safe_io.read_json(path, attempts=1)
        raw = doc["entries"]
        if not isinstance(raw, list) or not raw:
            raise ValueError("the recovery rows are invalid")
        paths = [e["dot_path"] for e in raw
                 if isinstance(e, dict) and isinstance(e.get("dot_path"), str)]
        if (type(doc["version"]) is not int or doc["version"] != 1
                or not working_copy._same_live(doc["live_folder"], wc.live_folder)):
            raise ValueError("the recovery checkpoint is invalid")
        rows = [ChangeEntry(**e) for e in raw]
        for row in rows:
            if (not isinstance(row.dot_path, str) or not row.dot_path
                    or row.source_file not in ("state", "wiring")
                    or type(row.created) is not bool or type(row.deleted) is not bool
                    or not isinstance(row.actor, str)
                    or (row.group_id is not None and not isinstance(row.group_id, str))):
                raise ValueError("the recovery row metadata is invalid")
        flags = doc["flags"]
        if (not isinstance(flags, dict)
                or not {"working_dirty", "pending_reapply"}.issubset(flags)):
            raise ValueError("the recovery flags are invalid")
        for key in ("working_dirty", "staged_base"):
            if key in flags and type(flags[key]) is not bool:
                raise ValueError("the recovery dirty flags are invalid")
        for key in ("pending_reapply", "pending_reapply_orig"):
            if key in flags and flags[key] is not None and not isinstance(flags[key], dict):
                raise ValueError("the recovery stash is invalid")
        # JSON turns the replay map's (operation, value[, group]) tuples into
        # lists. Sync's _untag recognizes tuples, so decode the tags again.
        flags = copy.deepcopy(flags)
        if flags.get("pending_reapply"):
            flags["pending_reapply"] = {
                key: tuple(value) if (isinstance(value, list) and len(value) in (2, 3)
                                     and value[0] in ("set", "literal", "create", "delete", "replace"))
                else value for key, value in flags["pending_reapply"].items()}
        if type(doc["mutation_seq"]) is not int or doc["mutation_seq"] < 0:
            raise ValueError("the recovery mutation sequence is invalid")
        if not isinstance(doc["state"], dict) or not isinstance(doc["wiring"], dict):
            raise ValueError("the staged documents are invalid")
        disk_hash = working_copy.content_hash(store.state, store.wiring)
        staged_hash = working_copy.content_hash(doc["state"], doc["wiring"])
        # A save interrupted after installing the pair but before clearing
        # the log is also recoverable: those files equal the entire snapshot.
        if disk_hash not in (doc["disk_hash"], staged_hash):
            raise ValueError("the working-copy files changed")
        live = working_copy.read_live(wc)
        if working_copy.content_hash(*live) != doc["synced_live_hash"]:
            raise ValueError("the live chip changed or its old values cannot be verified")
        if doc["synced_live_hash"] != wc.synced_live_hash:
            raise ValueError("the working-copy sync point changed")
        seq = max(doc["mutation_seq"], len(rows), store.mutation_seq) + 1
        # Prepare everything that can fail before publishing recovered data.
        state, wiring = copy.deepcopy(doc["state"]), copy.deepcopy(doc["wiring"])
        merged = merge_state_wiring(state, wiring)
        wiring_json = json.dumps(wiring)
    except Exception as exc:
        retained = path.with_name(f"{path.stem}.unrecovered-{uuid.uuid4().hex[:8]}.json")
        try:
            os.replace(path, retained)
            retention = f"Recovery file retained at {retained}."
        except OSError as retain_exc:
            retention = (f"Recovery file left at {path}; could not rename it: "
                         f"{retain_exc}.")
        ctx["_tray_recovery_notice"] = (
            f"Staged edits could not be recovered: {exc}. No rows were restored. "
            f"Paths: {', '.join(paths) or 'unreadable checkpoint'}. "
            f"Live folder: {wc.live_folder}. Working copy: {wc.working_folder}. "
            f"{retention}")
        return
    store.state, store.wiring, store.merged = state, wiring, merged
    store._clear_pointer_cache()
    store.mutation_seq = seq
    store.file_digest = None
    store.change_log = rows
    for key in _FLAGS:
        if key in flags:
            ctx[key] = flags[key]
    ctx["wiring_json"] = wiring_json


def attach(ctx):
    """Restore once, then bind every future mutation to this chip's sidecar."""
    if ctx.get("origin") != "live":
        return
    store = ctx["store"]
    with store._lock:
        _restore(ctx)
        store.change_log = PendingLog(store.change_log, lambda: checkpoint(ctx))
