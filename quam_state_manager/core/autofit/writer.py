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

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Callable

from quam_state_manager.core import working_copy
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


def _values_equal(a, b) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) \
            and not isinstance(a, bool) and not isinstance(b, bool):
        return math.isclose(float(a), float(b), rel_tol=_REL_TOL, abs_tol=0.0)
    return a == b


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
            chip.saver.save()
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

        def _restage() -> str | None:
            chip.modifier.batch_set(updates)
            return None

        err = _apply_live_with_one_retry(chip, _restage)
        if err:
            return WriteOutcome(ok=False, action="staged", group_id=gid,
                                paths=paths, error=err)
        return WriteOutcome(ok=True, action="applied", group_id=gid,
                            paths=paths)


def _apply_live_with_one_retry(chip: ChipHandle,
                               restage: Callable[[], str | None]) -> str | None:
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
        working_copy.apply_to_live(chip.wc)
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
        chip.saver.save()
        working_copy.apply_to_live(chip.wc)
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
            chip.saver.save()
        except Exception as exc:  # noqa: BLE001
            return WriteOutcome(ok=False, action="reverted", group_id=gid,
                                paths=paths, error=f"save failed: {exc}",
                                conflicts=conflicts)
        if apply_live:
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
                        chip.modifier.set_value(dotted, old, coerce=False,
                                                _defer_hooks=True,
                                                group_id=gid)
                    chip.store._clear_pointer_cache()
                return None

            err = _apply_live_with_one_retry(chip, _restage)
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
            chip.saver.save()
        except Exception as exc:  # noqa: BLE001
            return WriteOutcome(ok=False, action="restored", group_id=gid,
                                paths=paths, error=f"save failed: {exc}")
        if apply_live:
            def _restage() -> str | None:
                with chip.store._lock:
                    for r in rows:
                        chip.modifier.set_value(r["path"], r["value"],
                                                coerce=False,
                                                _defer_hooks=True,
                                                group_id=gid)
                    chip.store._clear_pointer_cache()
                return None

            err = _apply_live_with_one_retry(chip, _restage)
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
