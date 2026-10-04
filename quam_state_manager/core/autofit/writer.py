"""Autofit staged-write orchestrator + deterministic revert (docs/56 §2f, §7b-C).

The ONLY path by which the engine touches chip state. In-process equivalent of
``/field/edit-batch`` + ``/state/apply-to-live`` under the same locks:

    build_lock → modifier.batch_set (store._lock inside) → saver.save()
              → working_copy.apply_to_live  (autonomy full)

Revert doctrine (design-review amendments C + F5):
* only ``op == "replace"`` patches with a usable scalar ``old`` are
  auto-revertible; add/remove ops defer to the review queue;
* every revert is **compare-and-swap**: the current value must still equal the
  patch's ``value`` (float-tolerant) — anything else means a third party wrote
  since, and we defer instead of clobbering;
* restore writes use ``coerce=False`` (exact-typed restoration — the default
  coercion would cast an old string/pointer through the new value's type).

The writer never raises into the engine: every outcome is a result dict the
ledger can record verbatim.
"""
from __future__ import annotations

from pathlib import Path

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Callable

from quam_state_manager.core import edit_policy, working_copy
from quam_state_manager.core.autofit.synth import patch_path_to_dotted

logger = logging.getLogger(__name__)

_REL_TOL = 1e-9      # CAS float comparison


@dataclass
class ChipHandle:
    """Everything the writer needs about the loaded chip, resolved ONCE by the
    engine at plan start (never re-fetched from the live-active context — the
    same captured-ctx discipline State History's mutators use)."""
    store: Any                     # QuamStore
    modifier: Any                  # Modifier
    saver: Any                     # Saver (bound to the working copy)
    wc: Any                        # WorkingCopy
    build_lock: Any                # per-folder RLock
    live_path: str
    # engine-supplied refresh: pull live into store/wc (reconcile-by-path)
    reconcile: Callable[[], None] = lambda: None
    # docs/271: where this chip's SM writes are recorded (its history dir) and
    # under which plan. None = no chip ledger: the simulator's synthetic chip
    # (and test handles) -- every production ChipHandle names it explicitly.
    hub_dir: Any = None
    hub_plan: str | None = None
    # docs/271 review P1-1: the working pair's stat fingerprint right before
    # the writer's last save (``_save``) -- equal to the working copy's sync
    # point means the save's change log is the whole difference to live
    hub_fp0: Any = None
    # ...and right after it: the bytes the live write reads must be these
    hub_fp1: Any = None


@dataclass
class WriteOutcome:
    ok: bool
    action: str                    # "applied" | "staged" | "reverted" | "noop"
    group_id: str | None = None
    paths: list[dict] = field(default_factory=list)   # {path, old, new}
    error: str | None = None
    conflicts: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"ok": self.ok, "action": self.action, "group_id": self.group_id,
                "paths": self.paths, "error": self.error,
                "conflicts": self.conflicts}


def _save(chip: ChipHandle) -> None:
    """``chip.saver.save()``, remembering what the working files were before
    it (docs/271 review P1-1: the content check of the next live write)."""
    try:
        chip.hub_fp0 = working_copy.working_fingerprint(chip.wc)
    except Exception:  # noqa: BLE001 -- unknown: the write records the whole difference
        chip.hub_fp0 = None
    chip.hub_fp1 = None
    chip.saver.save()
    try:
        chip.hub_fp1 = working_copy.working_fingerprint(chip.wc)
    except Exception:  # noqa: BLE001 -- unknown: no bytes check
        chip.hub_fp1 = None


def _pending(chip: ChipHandle, src: str, mine: list) -> Any:
    """docs/271: the record of one autofit live write. When the working files
    were exactly the sync point's before the save (``hub_fp0``), the entries
    are what that save wrote (``Saver.last_cleared``); otherwise the working
    copy carries content the log does not name (a review-autonomy save, a
    person's /save) and the entries are the whole-chip difference between the
    chip right before the write and the bytes written (review P1-1). The
    writer's own entries are stamped ``by="autofit"`` (they carry the
    modifier's default actor)."""
    from quam_state_manager.core import hub, hub_entries
    if chip.hub_dir is None:
        return hub.unrecorded("autofit handle without a chip ledger (simulator / test chip)")
    own = {id(e) for e in mine or ()}
    cleared = list(getattr(chip.saver, "last_cleared", None) or [])
    by_path = {}
    ents = []
    for e in cleared:
        ent = hub_entries.entry_of(e)
        if id(e) in own:
            ent["by"] = "autofit"
        if ent.get("by"):
            by_path[ent["path"]] = ent["by"]
        ents.append(ent)
    fp0 = chip.hub_fp0
    content_ok = fp0 is not None and getattr(chip.wc, "synced_working_fp", None) == fp0
    holder: dict = {}
    if not content_ok:
        store = chip.store

        def ents(post_state=None):  # noqa: F811 -- the whole-chip difference
            from quam_state_manager.core import doc_cache, hub_rules
            try:
                pair = doc_cache.read_pair(Path(chip.wc.live_folder), mode="shared")
                before = hub_rules.merged(pair.state, pair.wiring)
            except Exception:  # noqa: BLE001 -- nothing readable to replace
                before = {}
            with store._lock:
                if store.change_log and post_state is not None:
                    holder["aft"] = hub._parse_pair(*post_state)
                aft = holder.get("aft") or store.merged
                return hub.wholesale_entries(before, aft, by_path=by_path,
                                             file_of=store.source_file_for)
    return hub.Pending(chip.hub_dir, "autofit", "autofit", src, entries=ents,
                       plan_id=chip.hub_plan,
                       fragments=hub.store_fragments(chip.store, holder=holder),
                       expect_fp=chip.hub_fp1)


def _values_equal(a, b) -> bool:
    # docs/117: ONE comparator, shared with the applied-log revert. Kept as a
    # module-level name because the doctrine block above and several call
    # sites refer to it.
    return edit_policy.cas_equal(a, b)


def _current_value(chip: ChipHandle, dotted: str):
    node: Any = chip.store.state
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            raise KeyError(dotted)
        node = node[part]
    return node


def apply_rows(chip: ChipHandle, rows: list[dict], *, apply_live: bool,
               label: str) -> WriteOutcome:
    """Stage forward-decision rows (``[{path, value, ...}]``) and, in full
    autonomy, promote to live. All-or-nothing via ``batch_set``."""
    if not rows:
        return WriteOutcome(ok=True, action="noop")
    updates = {r["path"]: r["value"] for r in rows}
    with chip.build_lock:
        try:
            entries = chip.modifier.batch_set(updates)
        except Exception as exc:  # noqa: BLE001 — coercion/navigation failure
            return WriteOutcome(ok=False, action="staged",
                                error=f"stage failed: {exc}")
        gid = entries[0].group_id if entries else None
        paths = [{"path": e.dot_path, "old": e.old_value, "new": e.new_value}
                 for e in entries]
        try:
            _save(chip)
        except Exception as exc:  # noqa: BLE001
            # best-effort in-memory rollback: restore old values exactly
            try:
                for e in reversed(entries):
                    chip.modifier.set_value(e.dot_path, e.old_value,
                                            coerce=False)
            except Exception:  # noqa: BLE001
                logger.exception("rollback after failed save also failed")
            return WriteOutcome(ok=False, action="staged", group_id=gid,
                                paths=paths, error=f"save failed: {exc}")
        if not apply_live:
            return WriteOutcome(ok=True, action="staged", group_id=gid,
                                paths=paths)

        restaged: list = []

        def _restage() -> str | None:
            restaged[:] = chip.modifier.batch_set(updates)
            return None

        err = _apply_live_with_one_retry(chip, _restage, src=f"autofit_apply:{label}",
                                         mine=lambda: restaged or entries)
        if err:
            return WriteOutcome(ok=False, action="staged", group_id=gid,
                                paths=paths, error=err)
        return WriteOutcome(ok=True, action="applied", group_id=gid,
                            paths=paths)


def _apply_live_with_one_retry(chip: ChipHandle,
                               restage: Callable[[], str | None], *, src: str = "autofit",
                               mine: Callable[[], list] = lambda: []) -> str | None:
    """apply_to_live with the amendment-§8 policy: ONE pull + re-stage retry
    on StaleLiveError, then give up (defer). Returns an error string or None.

    The retry is a genuine re-stage (audit E1): our own save() moved the
    working files off the recorded sync point, so a ctx-level reconcile would
    only latch ``live_diverged`` and re-raise. Instead: force-sync the working
    copy FROM live (adopting the out-of-band write), reload the store, replay
    our edits via *restage* (each caller re-applies its own rows — reverts
    re-verify CAS against the fresh content), save, apply. All under the
    build lock the caller already holds."""
    try:
        working_copy.apply_to_live(chip.wc, record=_pending(chip, src, mine()))
        return None
    except working_copy.StaleLiveError:
        logger.info("apply_to_live stale — one pull + re-stage retry")
    except Exception as exc:  # noqa: BLE001
        return f"apply_to_live failed: {exc}"
    try:
        working_copy.sync_from_live(chip.wc)
        chip.store.reload()
        err = restage()
        if err:
            return f"re-stage after pull refused: {err}"
        _save(chip)
        working_copy.apply_to_live(chip.wc, record=_pending(chip, src, mine()))
        return None
    except Exception as exc:  # noqa: BLE001
        return f"apply_to_live failed after pull+re-stage: {exc}"


def revert_patches(chip: ChipHandle, patches: list[dict], *, apply_live: bool,
                   label: str) -> WriteOutcome:
    """Deterministically undo a node's own state writes (the reject path).

    Restores each replace-patch's ``old`` value — CAS-guarded, exact-typed.
    Partial reverts are allowed across patches (each patch stands alone), but
    every conflict/skip is reported.
    """
    if not patches:
        return WriteOutcome(ok=True, action="noop")
    revertible: list[tuple[str, Any, Any]] = []      # (dotted, old, expect_new)
    conflicts: list[dict] = []
    for p in patches:
        dotted = patch_path_to_dotted(p.get("path", ""))
        op = p.get("op", "replace")
        old = p.get("old")
        if op != "replace" or old is None or isinstance(old, (dict, list)):
            conflicts.append({"path": dotted, "reason":
                              f"non-revertible patch (op={op}, old={type(old).__name__})"})
            continue
        try:
            cur = _current_value(chip, dotted)
        except KeyError:
            conflicts.append({"path": dotted, "reason": "path vanished"})
            continue
        if not _values_equal(cur, p.get("value")):
            conflicts.append({"path": dotted, "reason":
                              "value changed since the node wrote it (CAS) — "
                              f"current={cur!r}, patch={p.get('value')!r}"})
            continue
        revertible.append((dotted, old, p.get("value")))

    if not revertible:
        return WriteOutcome(ok=False, action="reverted",
                            error="nothing revertible", conflicts=conflicts)

    with chip.build_lock:
        entries = []
        gid = f"afrev{chip.store.mutation_seq}"
        try:
            with chip.store._lock:
                # re-CAS under the lock (the pre-check above was advisory)
                for dotted, old, expect in revertible:
                    cur = _current_value(chip, dotted)
                    if not _values_equal(cur, expect):
                        raise _CasConflict(dotted, cur, expect)
                for dotted, old, _ in revertible:
                    e = chip.modifier.set_value(dotted, old, coerce=False,
                                                _defer_hooks=True,
                                                group_id=gid)
                    entries.append(e)
                chip.store._clear_pointer_cache()
                if chip.store.search_index is not None:
                    for e in entries:
                        chip.store.search_index.update_entry(e.dot_path,
                                                             e.new_value)
        except _CasConflict as cc:
            conflicts.append({"path": cc.path, "reason":
                              f"CAS lost under lock (current={cc.cur!r})"})
            return WriteOutcome(ok=False, action="reverted",
                                error="CAS conflict", conflicts=conflicts)
        except Exception as exc:  # noqa: BLE001
            try:
                for e in reversed(entries):
                    chip.modifier.set_value(e.dot_path, e.old_value, coerce=False)
            except Exception:  # noqa: BLE001
                logger.exception("revert rollback failed")
            return WriteOutcome(ok=False, action="reverted",
                                error=f"revert failed: {exc}",
                                conflicts=conflicts)
        paths = [{"path": e.dot_path, "old": e.old_value, "new": e.new_value}
                 for e in entries]
        try:
            _save(chip)
        except Exception as exc:  # noqa: BLE001
            return WriteOutcome(ok=False, action="reverted", group_id=gid,
                                paths=paths, error=f"save failed: {exc}",
                                conflicts=conflicts)
        if apply_live:
            restaged: list = []

            def _restage() -> str | None:
                # after the pull the store holds the freshest live content —
                # a revert must re-win its CAS there or refuse (never clobber)
                with chip.store._lock:
                    for dotted, old, expect in revertible:
                        cur = _current_value(chip, dotted)
                        if not _values_equal(cur, expect):
                            return (f"CAS lost after pull at {dotted} "
                                    f"(current={cur!r})")
                    for dotted, old, _ in revertible:
                        restaged.append(chip.modifier.set_value(
                            dotted, old, coerce=False, _defer_hooks=True, group_id=gid))
                    chip.store._clear_pointer_cache()
                return None

            err = _apply_live_with_one_retry(chip, _restage, src=f"autofit_revert:{label}",
                                             mine=lambda: restaged or entries)
            if err:
                return WriteOutcome(ok=False, action="reverted", group_id=gid,
                                    paths=paths, error=err, conflicts=conflicts)
        return WriteOutcome(ok=True, action="reverted", group_id=gid,
                            paths=paths, conflicts=conflicts)


def restore_values(chip: ChipHandle, rows: list[dict], *, apply_live: bool,
                   label: str) -> WriteOutcome:
    """review-autonomy plan-end restore: force-write pre-plan values back
    (docs/56 §7b-A). No CAS — values legitimately evolved through multiple
    steps and the engine is the sole master while the mutator lock holds;
    exact-typed (coerce=False) like reverts. Every write is logged old→new."""
    if not rows:
        return WriteOutcome(ok=True, action="noop")
    with chip.build_lock:
        entries = []
        gid = f"afrestore{chip.store.mutation_seq}"
        try:
            with chip.store._lock:
                for r in rows:
                    e = chip.modifier.set_value(r["path"], r["value"],
                                                coerce=False,
                                                _defer_hooks=True,
                                                group_id=gid)
                    entries.append(e)
                chip.store._clear_pointer_cache()
                if chip.store.search_index is not None:
                    for e in entries:
                        chip.store.search_index.update_entry(e.dot_path,
                                                             e.new_value)
        except Exception as exc:  # noqa: BLE001
            try:
                for e in reversed(entries):
                    chip.modifier.set_value(e.dot_path, e.old_value,
                                            coerce=False)
            except Exception:  # noqa: BLE001
                logger.exception("restore rollback failed")
            return WriteOutcome(ok=False, action="restored",
                                error=f"restore failed: {exc}")
        paths = [{"path": e.dot_path, "old": e.old_value, "new": e.new_value}
                 for e in entries]
        try:
            _save(chip)
        except Exception as exc:  # noqa: BLE001
            return WriteOutcome(ok=False, action="restored", group_id=gid,
                                paths=paths, error=f"save failed: {exc}")
        if apply_live:
            restaged: list = []

            def _restage() -> str | None:
                with chip.store._lock:
                    for r in rows:
                        restaged.append(chip.modifier.set_value(
                            r["path"], r["value"], coerce=False, _defer_hooks=True,
                            group_id=gid))
                    chip.store._clear_pointer_cache()
                return None

            err = _apply_live_with_one_retry(chip, _restage, src=f"autofit_restore:{label}",
                                             mine=lambda: restaged or entries)
            if err:
                return WriteOutcome(ok=False, action="restored", group_id=gid,
                                    paths=paths, error=err)
        return WriteOutcome(ok=True, action="restored", group_id=gid,
                            paths=paths)


class RealWriter:
    """The engine's Writer protocol over a real ChipHandle (docs/56 §2f)."""

    def __init__(self, chip: ChipHandle, *, apply_live: bool = True):
        self.chip = chip
        self.apply_live = apply_live

    def current_value_of(self, dotted: str):
        return _current_value(self.chip, dotted)

    def merged_view(self) -> dict | None:
        """State + wiring in one root — power coupling resolves the feedline
        port through the wiring pointer chain, which `state` alone can't see."""
        return getattr(self.chip.store, "merged", None)

    def apply_rows(self, rows, *, label: str) -> dict:
        return apply_rows(self.chip, rows, apply_live=self.apply_live,
                          label=label).as_dict()

    def revert_patches(self, patches, *, label: str) -> dict:
        return revert_patches(self.chip, patches, apply_live=self.apply_live,
                              label=label).as_dict()

    def restore_values(self, rows, *, label: str) -> dict:
        return restore_values(self.chip, rows, apply_live=self.apply_live,
                              label=label).as_dict()


class _CasConflict(Exception):
    def __init__(self, path, cur, expect):
        super().__init__(path)
        self.path, self.cur, self.expect = path, cur, expect
