"""Autofit plan engine — the run→gate→audit→decide→write state machine
(docs/56 §2e, amended per §7b A/B).

One engine instance per SM instance path; ``is_active`` feeds the UI mutator
lock (the same guard that locks edits while the Scheduler runs). The engine
thread drives a *backend* (sim or the scheduler chassis) one step at a time:

    for step in plan:
        run the node on the still-alive targets (retry loop with per-family
        deterministic adaptations) → gate verdicts (G1..G5) → LLM audit on
        suspects (judge-only) → per-target decision:
            pass/accept → keep the node's own write (or stage+apply the
                          family rows when the node didn't write)
            fail/reject → REVERT the node's patches (CAS, exact-typed)
                          → retry with adapted params while budget lasts
                          → else defer to the review queue
        hard-criticality failures halt that TARGET's chain only.

Autonomy (§7b-A): ``full`` and ``review`` execute identically (the chain needs
each step's values on the chip); ``review`` restores every first-touched path
to its pre-plan value at plan end. Every event lands in an append-only JSONL
ledger; the report renders exclusively from it.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from quam_state_manager.core import safe_io
from quam_state_manager.core.autofit import families as fam_mod
from quam_state_manager.core.autofit import gates as gates_mod
from quam_state_manager.core.autofit import (consistency, notify, power_rows,
                                             stoploss, verification)
from quam_state_manager.core.autofit import auditor as auditor_mod
from quam_state_manager.core.autofit.auditor import Auditor, build_bundle
from quam_state_manager.core.autofit.plan import Plan, Step
from quam_state_manager.core.autofit.synth import patch_path_to_dotted

logger = logging.getLogger(__name__)

_STATE_FILE = "autofit_run.json"

# failure modes that mean "the sweep window / sampling missed the physics" —
# a pass on a LATER attempt of the same step is a *discovery* that earns the
# post-discovery wide verification (docs/56 v2, LOOP_STUDY case A)
_WINDOW_MODES = ("no_signal", "wrong_peak", "feature_present_fit_failed",
                 "out_of_band")


# ---------------------------------------------------------------------------
# Backend protocol
# ---------------------------------------------------------------------------

@dataclass
class StepRunResult:
    status: str                    # done | failed | skipped | aborted
    run: dict | None = None        # {experiment_name, fit_results, outcomes,
    #                                 parameters, folder_path, patches}
    error: str | None = None
    run_ref: dict | None = None    # dataset attribution (uid/run_id/name)


class Backend(Protocol):
    def run_step(self, step: Step, targets: list[str], params: dict,
                 attempt: int, abort: threading.Event) -> StepRunResult: ...


class Writer(Protocol):
    def current_value_of(self, dotted: str) -> Any: ...
    def apply_rows(self, rows: list[dict], *, label: str) -> dict: ...
    def revert_patches(self, patches: list[dict], *, label: str) -> dict: ...
    def restore_values(self, rows: list[dict], *, label: str) -> dict: ...
    # optional: the MERGED state+wiring view. Power coupling resolves a
    # feedline port through the wiring pointer chain, so `state` alone would
    # make every port lookup (silently) refuse.
    def merged_view(self) -> dict | None: ...


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------

_ENGINES: dict[str, "PlanEngine"] = {}
_ENGINES_LOCK = threading.Lock()


def get_engine(instance_path) -> "PlanEngine | None":
    with _ENGINES_LOCK:
        return _ENGINES.get(str(instance_path))


def is_active(instance_path) -> bool:
    eng = get_engine(instance_path)
    return bool(eng and eng.is_running())


def locks_chip(instance_path) -> bool:
    """True when a RUNNING plan owns the real chip/OPX — the edit-lock and the
    /scheduler/* two-masters guard key on this. A sim plan (its own throwaway
    world under instance/autofit/sim) never locks the user's chip (audit R2)."""
    eng = get_engine(instance_path)
    return bool(eng and eng.is_running() and not eng.is_sim)


# stat-cached persisted summary: the badge poll (every 2.5 s on every page)
# must survive an SM restart — the review count comes off autofit_run.json
# without re-reading it unless the file changed.
_PERSIST_CACHE: dict[str, tuple[int, dict | None]] = {}


def persisted_summary(instance_path) -> dict | None:
    p = Path(instance_path) / _STATE_FILE
    try:
        m = p.stat().st_mtime_ns
    except OSError:
        return None
    key = str(p)
    hit = _PERSIST_CACHE.get(key)
    if hit is not None and hit[0] == m:
        return hit[1]
    try:
        st = json.loads(p.read_text(encoding="utf-8"))
        out = {"status": st.get("status"), "running": False,
               "sim": bool(st.get("is_sim")),
               "plan": (st.get("plan") or {}).get("name"),
               "current": None,
               "review_count": len(st.get("review_queue") or [])}
    except (OSError, ValueError):
        out = None
    _PERSIST_CACHE[key] = (m, out)
    return out


class PlanEngine:
    def __init__(self, instance_path, plan: Plan, targets: list[str],
                 backend: Backend, writer: Writer, auditor: Auditor, *,
                 autonomy: str | None = None,
                 snapshot_fn: Callable[[str], Any] | None = None,
                 history_points_of: Callable[[dict[str, str]],
                                             dict[str, list[float]]]
                 | None = None,
                 abstain_policy: str = "defer",
                 is_sim: bool = False,
                 budget: "stoploss.Budget | None" = None,
                 resolve_node: Callable[[str], str | None] | None = None):
        # Tier 1 (docs/78 D-8). An unset budget is UNLIMITED, not zero — but
        # the plan cap and wall clock now EXIST, which §4.7 listed as absent
        # from the day the plan was written.
        self.budget = budget or stoploss.Budget(
            max_steps=(int(plan.max_steps) if plan.max_steps else None),
            wall_clock_s=(float(plan.wall_clock_min) * 60.0
                          if plan.wall_clock_min else None))
        self.instance_path = str(instance_path)
        self.plan = plan
        self.targets = list(targets)
        self.backend = backend
        self.writer = writer
        self.auditor = auditor
        self.autonomy = autonomy or plan.autonomy
        self.snapshot_fn = snapshot_fn or (lambda label: None)
        self.history_points_of = history_points_of
        self.abstain_policy = abstain_policy      # defer | keep | revert
        self.is_sim = bool(is_sim)
        # family key → node file for runtime-inserted escalation steps (the
        # web layer passes the calibrations-folder resolver; sim maps by family)
        self.resolve_node = resolve_node
        self.plan_run_id = "af_" + uuid.uuid4().hex[:10]
        self.abort_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._starting = False        # claim → thread-alive gap cover (audit E3)
        self._lock = threading.RLock()
        self.state: dict = {
            "plan_run_id": self.plan_run_id,
            "status": "idle",                     # running|done|failed|aborted
            "plan": plan.as_dict(),
            "targets": self.targets,
            "autonomy": self.autonomy,
            "board": {},                          # step_id -> target -> cell
            "current": None,                      # {step_id, attempt}
            "halted": {},                         # target -> reason
            "review_queue": [],                   # deferred cells
            "started_at": None, "ended_at": None, "error": None,
            "llm_calls": 0,
        }
        self._ledger_dir = (Path(self.instance_path) / "autofit" / "runs"
                            / self.plan_run_id)
        # first-touched pre-plan values for the review-mode end restore
        self._preplan_values: dict[str, Any] = {}
        # (step_id, target) → synthesized replace-patches for an outstanding
        # scan seed: restored on terminal failure, consumed on success
        # (docs/56 v2 rail ③ — the node's own write supersedes a good seed)
        self._seeds: dict[tuple[str, str], list[dict]] = {}
        # (family, target) -> the fit entry the plan finally accepted. The
        # cross-experiment review (P6c) is the only thing that reads across
        # runs, so this is the only place the whole night is in one dict.
        self._fits: dict[tuple[str, str], dict] = {}
        # the verification context each accepted fit was obtained under, so the
        # review can refuse to compare two that were not (docs/78 §17 B3)
        self._fit_contexts: dict[tuple[str, str], dict | None] = {}
        # (step_id, target) → {"patches": [...]}: a value DISCOVERED on a
        # retry after window-class failures, pending wide verification
        self._discoveries: dict[tuple[str, str], dict] = {}
        # the replace-patches of the write _decide just applied (node's own
        # patches OR the engine's forward-applied rows) — read once by the
        # discovery capture in _run_step_inner right after _decide returns
        self._last_write: list[dict] = []
        # {path: pre-seed value} for the seed _decide just consumed on a pass —
        # lets the discovery capture chain a verify-fail revert past the seed
        self._last_seed_old: dict[str, Any] = {}

    # ---- lifecycle -------------------------------------------------------

    def start(self) -> str:
        with _ENGINES_LOCK:
            cur = _ENGINES.get(self.instance_path)
            if cur is not None and cur.is_running():
                raise RuntimeError("an autofit plan is already running")
            # claim the slot ATOMICALLY: _starting keeps is_running() True
            # through the mkdir/persist gap before the thread is alive, so a
            # concurrent start() can't double-claim (audit E3)
            self._starting = True
            _ENGINES[self.instance_path] = self
        try:
            self._ledger_dir.mkdir(parents=True, exist_ok=True)
            self.state["status"] = "running"
            self.state["is_sim"] = self.is_sim
            self.state["started_at"] = _now()
            self._persist()
            self._thread = threading.Thread(target=self._run, daemon=True,
                                            name=f"autofit-{self.plan_run_id}")
            self._thread.start()
        finally:
            # the thread is alive (or start raised) — the claim flag can drop
            self._starting = False
        return self.plan_run_id

    def abort(self) -> None:
        self.abort_event.set()

    def is_running(self) -> bool:
        if self._starting:
            return True
        t = self._thread
        return bool(t and t.is_alive())

    def status(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self.state))

    # ---- internals -------------------------------------------------------

    def _ledger(self, event: str, **payload) -> None:
        rec = {"t": _now(), "event": event, **payload}
        try:
            with open(self._ledger_dir / "ledger.jsonl", "a",
                      encoding="utf-8") as fh:
                fh.write(json.dumps(rec, default=str) + "\n")
        except OSError:
            logger.exception("autofit ledger write failed")

    def _persist(self) -> None:
        try:
            with self._lock:
                safe_io.atomic_write_json(
                    Path(self.instance_path) / _STATE_FILE, self.state)
        except Exception:  # noqa: BLE001
            logger.exception("autofit state persist failed")

    def _cell(self, step_id: str, target: str, state: str, **extra) -> None:
        with self._lock:
            cell = self.state["board"].setdefault(step_id, {}).setdefault(
                target, {"attempts": 0})
            cell.update(state=state, **extra)
        self._persist()

    def _notify(self, event: str, **payload) -> None:
        """Best-effort (docs/78 D-9) — a notifier must never fail a night."""
        try:
            notify.notify(self.instance_path, event,
                          {"plan_run_id": self.plan_run_id, **payload})
        except Exception:  # noqa: BLE001
            logger.warning("autofit notify failed", exc_info=True)

    def _record_preplan(self, dotted: str, old_value) -> None:
        if dotted not in self._preplan_values:
            self._preplan_values[dotted] = old_value

    # ---- the plan loop ---------------------------------------------------

    def _run(self) -> None:
        try:
            self._ledger("plan_started", plan=self.plan.as_dict(),
                         targets=self.targets, autonomy=self.autonomy)
            try:
                self.snapshot_fn(f"autofit pre-plan ({self.plan.name})")
            except Exception:  # noqa: BLE001
                logger.exception("pre-plan snapshot failed (continuing)")
            # a WORK QUEUE, not a frozen list: v2 rungs insert steps at run
            # time (wide verification after a discovery, cross-node re-cal
            # before a retry) — docs/56 v2
            queue: deque[Step] = deque(s for s in self.plan.steps if s.enabled)
            while queue:
                if self.abort_event.is_set():
                    break
                # Tier 1 (docs/78 D-8). The queue is a work queue that runtime
                # rungs push INTO, so "steps left" is not a bound — without a
                # cap and a clock a plan that keeps finding new work has
                # nothing to stop it. Checked before each step so the ledger
                # records where the night ended.
                spent = self.budget.plan_exhausted()
                if spent:
                    self._ledger("plan_stopped", tier=1, reason=spent,
                                 steps_run=self.budget.steps_run,
                                 elapsed_s=round(self.budget.elapsed_s(), 1))
                    with self._lock:
                        self.state["stopped_reason"] = spent
                    self._notify("plan_stopped", tier=1, reason=spent,
                                 plan=self.plan.name)
                    break
                step = queue.popleft()
                self.budget.note_step()
                alive = [t for t in self.targets
                         if t not in self.state["halted"]]
                if not alive:
                    break
                if step.only_targets:
                    alive = [t for t in alive if t in step.only_targets]
                    if not alive:
                        continue        # its targets halted — skip, not end
                self._run_step(step, alive, queue)
            # P6c (docs/78 §1.1 #3): the review that only exists across runs.
            # Every gate so far judged one run against itself; the pair that is
            # each internally consistent and mutually impossible is invisible
            # until the results are laid side by side.
            try:
                rep = consistency.reconcile(self._fits,
                                            contexts=self._fit_contexts)
                self._ledger("consistency_review", **rep.as_dict())
                with self._lock:
                    self.state["consistency"] = rep.as_dict()
                if rep.findings:
                    self._notify("needs_human", plan=self.plan.name,
                                 question=consistency.summarize(rep),
                                 contradictions=len(rep.findings))
            except Exception:  # noqa: BLE001 — a review must not lose a night
                logger.exception("consistency review failed")
            if self.autonomy == "review" and not self.abort_event.is_set():
                self._end_restore()
            with self._lock:
                self.state["status"] = ("aborted" if self.abort_event.is_set()
                                        else "done")
                self.state["ended_at"] = _now()
                self.state["current"] = None
            self._ledger("plan_done", status=self.state["status"],
                         review_queue=len(self.state["review_queue"]))
            self._notify("plan_done", status=self.state["status"],
                         plan=self.plan.name,
                         review_queue=len(self.state["review_queue"]),
                         halted=len(self.state.get("halted") or {}),
                         stopped_reason=self.state.get("stopped_reason"))
        except Exception as exc:  # noqa: BLE001 — the engine must never die silently
            logger.exception("autofit plan crashed")
            with self._lock:
                self.state["status"] = "failed"
                self.state["error"] = str(exc)
                self.state["ended_at"] = _now()
            self._ledger("plan_crashed", error=str(exc))
        finally:
            self._persist()

    def _run_step(self, step: Step, alive: list[str],
                  queue: deque[Step] | None = None) -> None:
        try:
            self._run_step_inner(step, alive, queue)
        finally:
            # SAFETY NET (docs/56 v2 rail ③): any scan seed written this step
            # and not already consumed (pass) or restored (terminal fail) —
            # i.e. leaked by an abort / skip / crash / escalation handoff —
            # goes back to its pre value. A deliberately-shifted frequency
            # must never linger on the chip. Consumed/restored seeds were
            # already popped, so this is a no-op on the normal path.
            for sid, tgt in [k for k in self._seeds if k[0] == step.id]:
                self._restore_seed(sid, tgt)

    def _run_step_inner(self, step: Step, alive: list[str],
                        queue: deque[Step] | None = None) -> None:
        pending = list(alive)
        attempt = 0
        params = dict(step.params)
        # per-failure-mode ladder position (a mode's rung advances each time
        # THAT mode drives an adaptation, independent of other modes)
        mode_counts: dict[str, int] = {}
        # targets that had window-class failures → a later pass is a DISCOVERY
        # (wide verification due). Seeded from the escalation-continuation
        # carry so case-A recovery earns verification even if it converges on
        # this continuation's attempt 0.
        carried: set[str] = set(step.carry_window_failure)
        window_failures: set[str] = set(carried)
        discovered: set[str] = set()
        while pending and attempt <= step.retry_max \
                and not self.abort_event.is_set():
            with self._lock:
                self.state["current"] = {"step_id": step.id, "attempt": attempt,
                                         "targets": list(pending)}
            for t in pending:
                self._cell(step.id, t, "running", attempts=attempt + 1)
            self._ledger("step_started", step=step.id, attempt=attempt,
                         targets=pending, params=params)

            res = self.backend.run_step(step, pending, params, attempt,
                                        self.abort_event)
            if res.status == "aborted" or self.abort_event.is_set():
                for t in pending:
                    self._cell(step.id, t, "aborted")
                return
            if res.status == "skipped":
                # benign non-run (dry-run refusal / no sim generator): record
                # and move on — never a defer, never a halt
                for t in pending:
                    self._cell(step.id, t, "skipped",
                               detail=res.error or "skipped")
                self._ledger("step_skipped", step=step.id,
                             reason=res.error)
                return
            self._ledger("run_finished", step=step.id, attempt=attempt,
                         status=res.status, error=res.error,
                         run_ref=res.run_ref)

            # shared-path patches (no target segment / not one of this run's
            # targets — e.g. a port full_scale_power_dbm): record their
            # pre-plan values for the review-mode restore, and surface them if
            # any target gets rejected — they can't be target-attributed, so
            # they are never auto-reverted (audit E2)
            orphan_patches = [p for p in ((res.run or {}).get("patches") or [])
                              if _patch_target(p) not in pending]
            for p in orphan_patches:
                self._record_preplan(patch_path_to_dotted(p.get("path", "")),
                                     p.get("old"))

            verdicts = self._evaluate(step, res, pending)
            retry_targets: list[str] = []
            retry_mode: str | None = None
            any_reject = False
            fam = (fam_mod.family_for(res.run["experiment_name"])
                   if res.run else None)
            for t in pending:
                v = verdicts[t]
                self._ledger("verdict", step=step.id, attempt=attempt,
                             **v.as_dict())      # as_dict carries target
                decision = self._decide(step, t, v, res, fam, attempt)
                self._ledger("decision", step=step.id, attempt=attempt,
                             target=t, decision=decision)
                if decision in ("retry", "defer") and v.verdict == "fail":
                    any_reject = True
                if decision == "retry":
                    retry_targets.append(t)
                    retry_mode = retry_mode or v.failure_mode
                    if v.failure_mode in _WINDOW_MODES:
                        window_failures.add(t)
                elif decision in ("keep", "applied") and t in window_failures \
                        and (attempt > 0 or t in carried):
                    # a window-class failure chain that CONVERGED — record the
                    # discovery keyed to the ACTUAL write (node patches OR the
                    # engine's forward-applied rows, captured in _decide) so
                    # the verify-fail revert isn't a no-op for forward writes.
                    # Where the write sits on a SEEDED path, chain its revert
                    # back to the ORIGINAL pre-seed value (not the seed) so a
                    # verify-fail undoes the whole hypothesis (docs/56 v2).
                    disc = [dict(p) for p in self._last_write]
                    for p in disc:
                        # node patches are slash paths, forward rows + seeds
                        # are dotted — normalize before the seed-old lookup
                        dotted = patch_path_to_dotted(p.get("path", ""))
                        if dotted in self._last_seed_old:
                            p["old"] = self._last_seed_old[dotted]
                    discovered.add(t)
                    self._discoveries[(step.id, t)] = {"patches": disc}
            if orphan_patches and any_reject:
                paths = [patch_path_to_dotted(p.get("path", ""))
                         for p in orphan_patches]
                self._ledger("orphan_patches_flagged", step=step.id,
                             paths=paths)
                with self._lock:
                    self.state["review_queue"].append({
                        "step_id": step.id, "target": "(shared)",
                        "reason": (f"{len(orphan_patches)} shared-path "
                                   "patch(es) not target-attributable — left "
                                   "as written: " + ", ".join(paths[:4])),
                        "failure_mode": None, "reverted": False,
                        "verdict": {"paths": paths},
                    })
                self._persist()
            pending = retry_targets
            if pending and fam is not None and retry_mode:
                params, escalated = self._adapt(
                    step, fam, retry_mode, params, mode_counts, pending,
                    verdicts, attempt, queue)
                if escalated:
                    # targets that CONVERGED earlier in this step still deserve
                    # their wide verification before the plan trusts them, even
                    # though the step ends here for the escalating ones (their
                    # continuation carries window_failures so it can verify too)
                    self._maybe_verify_wide(step, discovered, params, queue)
                    return                     # (finally restores leaked seeds)
            attempt += 1

        self._maybe_verify_wide(step, discovered, params, queue)

    def _maybe_verify_wide(self, step: Step, discovered: set[str],
                           params: dict, queue: deque[Step] | None) -> None:
        """Insert the post-discovery wide verification (LOOP_STUDY case A: a
        recovered feature is re-checked with a broad survey before it is
        trusted). Family is resolved from the STEP — never the last run —
        so a final-attempt crash / escalation-return can't skip it."""
        if not discovered or queue is None or step.verify_of \
                or step.inserted_by == "verify_wide":
            return
        fam = fam_mod.family_for(step.family or step.node or "")
        vw = getattr(fam, "verify_wide", None) if fam is not None else None
        if not vw:
            return
        survey = vw.get("survey_params")
        if survey:
            # An ABSOLUTE mode switch, not a relative widening. power_rabi is
            # the family that needed this (docs/78 §17.6): its wide check is
            # "go back to the single-pulse survey", and scaling its window
            # instead would alias rather than survey. Everything the family
            # does not pin is carried through untouched.
            vparams = {**params, **survey}
        else:
            span_param = vw.get("span_param", "frequency_span_in_mhz")
            span = float(params.get(span_param, vw.get("span_default", 60.0)))
            vparams = {**params, span_param: span * float(vw.get("factor", 4.0))}
        vstep = Step(id=f"{step.id}__verify_wide", node=step.node,
                     family=step.family,
                     label=f"wide verification of {step.id}",
                     params=vparams, retry_max=0,
                     criticality=step.criticality,
                     only_targets=tuple(sorted(discovered)),
                     verify_of=step.id, inserted_by="verify_wide")
        queue.appendleft(vstep)
        self._ledger("verify_wide_inserted", step=step.id,
                     targets=sorted(discovered), params=vparams)

    # ---- adaptation ladder (docs/56 v2) ----------------------------------

    def _adapt(self, step: Step, fam, mode: str, params: dict,
               mode_counts: dict[str, int], pending: list[str],
               verdicts: dict, attempt: int,
               queue: deque[Step] | None) -> tuple[dict, bool]:
        """Walk one rung of the failure mode's ladder. Returns
        ``(new_params, escalated)`` — escalated=True means a re-cal step +
        this step's continuation were queued and the caller must stop."""
        rungs = fam_mod.rungs_for(fam, mode)
        if not rungs:
            return params, False
        idx = mode_counts.get(mode, 0)
        mode_counts[mode] = idx + 1
        rung = rungs[min(idx, len(rungs) - 1)]

        if rung.kind == "params" and rung.rule is not None:
            try:
                overrides = rung.rule(params)
                params = {**params, **overrides}
                self._ledger("params_adapted", step=step.id, mode=mode,
                             rung=idx, overrides=overrides)
            except Exception:  # noqa: BLE001
                logger.exception("adaptation rule failed")
            return params, False

        if rung.kind == "seed_shift":
            for t in pending:
                v = verdicts.get(t)
                direction = getattr(v, "direction_hint", None) if v else None
                if direction not in ("left", "right"):
                    # no qualitative evidence — a blind shift is a guess, and
                    # guesses are what this whole design forbids
                    self._ledger("seed_skipped", step=step.id, target=t,
                                 reason="no direction evidence (edge/vision)")
                    continue
                self._seed_shift(step, rung, t, params, direction)
            return params, False

        if rung.kind == "escalate" and rung.escalate_family:
            if step.inserted_by.startswith("escalation") or queue is None:
                self._ledger("escalation_blocked", step=step.id,
                             reason="already an escalation step (no re-escalate)")
                return params, False
            node_file = ""
            if self.resolve_node is not None:
                try:
                    node_file = self.resolve_node(rung.escalate_family) or ""
                except Exception:  # noqa: BLE001
                    logger.exception("escalation node resolve failed")
            recal = Step(id=f"{step.id}__recal", node=node_file,
                         family=rung.escalate_family,
                         label=rung.note or f"re-cal for {step.id}",
                         params=dict(rung.escalate_params or {}),
                         retry_max=1, criticality="soft",
                         only_targets=tuple(pending),
                         inserted_by="escalation_recal")
            cont = Step(id=f"{step.id}__retry", node=step.node,
                        family=step.family,
                        label=f"{step.label or step.id} (after re-cal)",
                        params=dict(params), retry_max=1,
                        criticality=step.criticality,
                        only_targets=tuple(pending),
                        inserted_by="escalation",
                        # these targets reached the escalate rung via window
                        # failures — a convergence in the continuation is a
                        # discovery and must be wide-verified (LOOP_STUDY A)
                        carry_window_failure=tuple(pending))
            queue.appendleft(cont)
            queue.appendleft(recal)
            self._ledger("escalation_inserted", step=step.id,
                         recal_family=rung.escalate_family,
                         targets=list(pending), note=rung.note)
            for t in pending:
                self._cell(step.id, t, "retrying",
                           detail=f"escalation: {rung.note or rung.escalate_family}")
            return params, True

        return params, False

    def _seed_shift(self, step: Step, rung, target: str, params: dict,
                    direction: str) -> bool:
        """Scan-seed write (docs/56 v2 rails): magnitude = window math over
        the family's span param (never an LLM number), write via the audited
        writer + ledger, pre-values recorded for the failure restore."""
        span_hz = float(params.get(rung.span_param, rung.span_default)) * 1e6
        delta = span_hz * rung.shift_frac * (1.0 if direction == "right" else -1.0)
        rows = []
        for tmpl in rung.seed_paths:
            path = tmpl.replace("{q}", target).replace("{pair}", target)
            try:
                cur = self.writer.current_value_of(path)
            except Exception:  # noqa: BLE001
                cur = None
            if not isinstance(cur, (int, float)) or isinstance(cur, bool):
                self._ledger("seed_skipped", step=step.id, target=target,
                             reason=f"{path} not a literal number")
                return False
            rows.append({"path": path, "value": cur + delta, "old_hint": cur,
                         "label": "scan seed", "op": "assign"})
        for r in rows:
            self._record_preplan(r["path"], r["old_hint"])
        out = self.writer.apply_rows(rows, label=f"{step.id}:{target}:seed")
        self._ledger("seed_write", step=step.id, target=target,
                     direction=direction, delta_hz=delta, **out)
        if out.get("ok"):
            new = [{"path": r["path"], "op": "replace", "old": r["old_hint"],
                    "value": r["value"]} for r in rows]
            # if a seed on these paths is already outstanding (a second seed
            # rung — not in the shipped ladders, but defensively), keep the
            # ORIGINAL pre-seed `old` so a restore unwinds the whole shift, not
            # just the last hop.
            prior = {p["path"]: p["old"]
                     for p in self._seeds.get((step.id, target), [])}
            for p in new:
                if p["path"] in prior:
                    p["old"] = prior[p["path"]]
            self._seeds[(step.id, target)] = new
            return True
        return False

    def _restore_seed(self, step_id: str, target: str) -> None:
        """Terminal failure with an outstanding seed: put the window back
        (CAS — if anything else moved the value since, defer, never clobber)."""
        patches = self._seeds.pop((step_id, target), None)
        if not patches:
            return
        out = self.writer.revert_patches(patches,
                                         label=f"{step_id}:{target}:seed-restore")
        self._ledger("seed_restored", step=step_id, target=target, **out)

    # ---- evaluation ------------------------------------------------------

    def _stamp(self, verdicts: dict, run: dict | None,
               figure_source: str | None) -> dict:
        """Attach the verification triple to every verdict (docs/78 §17 B3).

        Stamped on the way OUT rather than at construction, so no return path
        can forget it — including the two that never reach the gate pipeline.
        A verdict whose context is unrecorded is one nothing downstream may
        compare, and the ledger has to be able to say that.
        """
        ctx = verification.for_sm_gates(
            (run or {}).get("folder_path"),
            figure_source=figure_source).as_dict()
        for v in verdicts.values():
            if v.context is None:
                v.context = ctx
        return verdicts

    def _evaluate(self, step: Step, res: StepRunResult,
                  targets: list[str]) -> dict[str, gates_mod.GateVerdict]:
        if res.status != "done" or not res.run:
            # no run ⇒ no data and no figure, but the gate revision that
            # refused is still part of the record
            return self._stamp({t: gates_mod.GateVerdict(
                target=t, verdict="fail", failure_mode="node_failed",
                reasons=[res.error or f"run status {res.status}"])
                for t in targets}, None, "none")
        run = res.run
        fam = fam_mod.family_for(run["experiment_name"])
        if fam is None:
            # unknown family: gate-less — the node's own outcome is all we have
            out = {}
            for t in targets:
                ok = (run.get("outcomes") or {}).get(t) == "successful"
                out[t] = gates_mod.GateVerdict(
                    target=t, verdict="suspect" if ok else "fail",
                    failure_mode=None if ok else "node_failed",
                    reasons=["no autofit family registered — node outcome only"])
            return self._stamp(out, run, "none")

        patched_old = {patch_path_to_dotted(p.get("path", "")): p.get("old")
                       for p in (run.get("patches") or [])}

        def pre_update(path: str):
            if path in patched_old:
                return patched_old[path]
            return self.writer.current_value_of(path)

        hp = None
        if self.history_points_of is not None:
            # The trend anchor is the STATE path this family WRITES, not the
            # bare fit key — a family whose value has no writable home (the
            # verify-only coupler nodes) honestly has no history to drift
            # against, and the provider is told the paths rather than guessing
            # them (docs/78 P2d). Resolved for every target in ONE map so the
            # provider can serve a whole column from a single snapshot pass
            # instead of re-parsing the history N times.
            run_params = run.get("parameters") or {}
            path_map = {}
            for t in targets:
                p = fam_mod.trend_path_for(fam, fam.value_key, t, run_params,
                                           self.writer.current_value_of)
                if p:
                    path_map[t] = p
            series: dict = {}
            if path_map:
                try:
                    series = self.history_points_of(path_map) or {}
                except Exception:  # noqa: BLE001 — history must never gate-block
                    logger.warning("history trend lookup failed", exc_info=True)
                    series = {}
            hp = series.get

        verdicts = gates_mod.evaluate_run(
            run, fam, targets,
            current_value_of=self.writer.current_value_of,
            pre_update_value_of=pre_update,
            history_points_of=hp)

        # LLM rounds. (1) judge audit on SUSPECTS only — an accept can never
        # override a deterministic fail (one ack never collapses two gates).
        # (2) v2 presence reading on node-FAILED targets of families with NO
        # deterministic raw-data localizer (the 2-D vs_power class): vision
        # refines WHICH failure ladder applies (fit-died vs empty-window) —
        # the verdict stays a fail either way.
        # ---- stage 1: ONE triage call over the sheet (docs/78 §18) ---------
        sheet = _first_figure(run)
        # the engine feeds the judge the run's OWN saved PNGs; it never
        # regenerates. That is a different analysis revision from anything we
        # replay, so the verdict has to say which it was.
        saw_figure = False
        triage = None
        if self.auditor.enabled and len(targets) > 1 and sheet is not None:
            tb = auditor_mod.build_triage_bundle(
                family_key=fam.key, family_label=fam.label, targets=targets,
                figure_path=sheet)
            triage = self.auditor.triage(tb, known_targets=targets)
            saw_figure = True
            with self._lock:
                self.state["llm_calls"] = self.auditor.calls_made
            self._ledger("llm_triage", step=step.id, **triage.as_dict())
        gate_suspects = [t for t, v in verdicts.items() if v.verdict != "pass"]
        look_at: set[str] = set()
        if self.auditor.enabled:
            if triage is None and len(targets) <= 1:
                # a one-target run HAS no overview to lean on — and the sheet
                # already IS that target's panel, so the whole cost is one
                # call. "overview_only" here would be a lie.
                look_at = set(targets)
            else:
                look_at = set(auditor_mod.dedicated_look_set(
                    gate_suspects, triage, targets))

        for t, v in verdicts.items():
            if not self.auditor.enabled:
                break
            entry = (run.get("fit_results") or {}).get(t) or {}
            # stage 2: this target's OWN panel where one exists; the sheet is
            # the honest fallback and the ledger records which was used
            panel = _panel_figure(run, t)
            figure = panel or sheet
            v.panel_kind = "panel" if panel is not None else "sheet"
            saw_figure = saw_figure or figure is not None
            if v.verdict == "suspect":
                bundle = build_bundle(family_label=fam.label, target=t,
                                      fit_entry=entry, gate_reasons=v.reasons,
                                      figure_path=figure)
                av = self.auditor.audit(bundle)
                with self._lock:
                    self.state["llm_calls"] = self.auditor.calls_made
                self._ledger("llm_verdict", step=step.id, target=t,
                             **av.as_dict())
                if av.verdict == "accept":
                    v.verdict = "pass"
                    v.reasons.append(f"LLM accept: {av.reason}")
                elif av.verdict == "reject":
                    v.verdict = "fail"
                    v.failure_mode = av.failure_mode or v.failure_mode or "noisy"
                    v.reasons.append(f"LLM reject: {av.reason}")
                # abstain → stays suspect; policy resolves below
                if v.direction_hint is None and av.direction:
                    v.direction_hint = av.direction
            elif v.verdict == "fail" and v.failure_mode == "node_failed" \
                    and fam.feature_check is None:
                bundle = build_bundle(family_label=fam.label, target=t,
                                      fit_entry=entry, gate_reasons=v.reasons,
                                      figure_path=figure, ask="presence")
                av = self.auditor.audit(bundle)
                with self._lock:
                    self.state["llm_calls"] = self.auditor.calls_made
                self._ledger("llm_verdict", step=step.id, target=t,
                             **av.as_dict())
                if av.feature_visible is True:
                    v.failure_mode = "feature_present_fit_failed"
                    v.feature_present = True
                    v.reasons.append(f"vision: feature visible — {av.reason}")
                elif av.feature_visible is False:
                    v.failure_mode = "no_signal"
                    v.feature_present = False
                    v.direction_hint = av.direction
                    v.reasons.append(f"vision: window empty — {av.reason}")
                # null → stays node_failed (defer)

        # ---- the §1.3 terminator -------------------------------------------
        # "done ONLY when the gates PASS and the judge ACCEPTS the signature."
        # Asked of every target that the gates passed AND that stage 1 (or the
        # gates) singled out — a target nobody flagged terminates on gates +
        # overview and is STAMPED as such, so no report can imply it got a
        # dedicated look it never got.
        for t, v in verdicts.items():
            if v.verdict != "pass":
                continue
            if not self.auditor.enabled:
                v.vision = "unavailable"          # policy: gates-only, stamped
                continue
            if t not in look_at:
                v.vision = "overview_only"
                continue
            fig = _panel_figure(run, t) or sheet
            if fig is None:
                # nothing to look AT — asking anyway would spend a call to be
                # told "unclear" by a judge shown nothing
                v.vision = "no_figure"
                continue
            saw_figure = True
            sb = auditor_mod.build_signature_bundle(
                family_key=fam.key, family_label=fam.label, target=t,
                figure_path=fig)
            sv = self.auditor.signature(sb)
            with self._lock:
                self.state["llm_calls"] = self.auditor.calls_made
            self._ledger("llm_signature", step=step.id, target=t,
                         panel=v.panel_kind, **sv.as_dict())
            v.vision = sv.signature
            if sv.accepted:
                v.reasons.append(f"signature clear: {sv.reason}")
                continue
            # gates pass + judge does not accept ⇒ NOT done. This is the loop.
            v.verdict = "fail"
            v.failure_mode = sv.failure_mode or v.failure_mode or "noisy"
            v.reasons.append(f"signature {sv.signature}: {sv.reason}")
        return self._stamp(verdicts, run,
                           "archived" if saw_figure else "none")

    # ---- decision + writes ------------------------------------------------

    def _decide(self, step: Step, target: str, v: gates_mod.GateVerdict,
                res: StepRunResult, fam, attempt: int) -> str:
        run = res.run or {}
        self._last_write = []          # the write this call performs, if any
        self._last_seed_old = {}       # pre-SEED value per seeded path (below)
        target_patches = [p for p in (run.get("patches") or [])
                          if _patch_target(p) == target]
        for p in target_patches:
            self._record_preplan(patch_path_to_dotted(p.get("path", "")),
                                 p.get("old"))

        # Only an ACCEPTED fit enters the cross-experiment review: comparing a
        # value the plan itself rejected against a good one would report a
        # contradiction we already resolved.
        if fam is not None and v.verdict == "pass":
            entry = (run.get("fit_results") or {}).get(target)
            if isinstance(entry, dict):
                self._fits[(fam.key, target)] = entry
                # the review is the ONE place that reasons across runs, so it
                # is the one place D-13 can actually bite: two values obtained
                # under different gate revisions or state generations are not
                # a contradiction, they are a category error.
                self._fit_contexts[(fam.key, target)] = v.context

        # P6b (minimal): the board carries HOW this target was looked at, so a
        # cell reading "pass" can never be mistaken for a vision-verified one.
        self._cell(step.id, target, self.state["board"].get(step.id, {})
                   .get(target, {}).get("state", "running"),
                   vision=v.vision, panel=v.panel_kind)

        effective = v.verdict
        if effective == "suspect":
            effective = {"defer": "defer", "keep": "pass",
                         "revert": "fail"}.get(self.abstain_policy, "defer")

        if effective == "pass":
            # an outstanding scan seed is consumed by success — the node's own
            # write supersedes it (rail ③: seeds auto-expire, never linger).
            # Remember its ORIGINAL pre-seed values: if this pass turns out to
            # be a discovery, a later verify-fail revert must chain all the way
            # back to pre-plan, not stop at the seeded (wrong) window.
            seed = self._seeds.pop((step.id, target), None)
            self._last_seed_old = {p["path"]: p["old"] for p in (seed or [])}
            if step.verify_of:
                # wide verification PASSED — the discovery stands
                self._discoveries.pop((step.verify_of, target), None)
            if target_patches:
                # the node wrote its own state — the discovery revert (if the
                # wide verify later refutes it) undoes exactly these patches
                self._last_write = [dict(p) for p in target_patches]
                self._cell(step.id, target,
                           "corrected" if attempt > 0 else "pass",
                           detail="node applied; gates passed")
                return "keep"
            rows = self._forward_rows(fam, target, run)
            if not rows:
                self._cell(step.id, target,
                           "corrected" if attempt > 0 else "pass",
                           detail="verified (nothing to write)")
                return "keep"
            for r in rows:
                self._record_preplan(r["path"], r.get("old_hint"))
            out = self.writer.apply_rows(rows, label=f"{step.id}:{target}")
            self._ledger("write_applied", step=step.id, target=target, **out)
            if out.get("ok"):
                # capture the ACTUAL applied write as replace-patches so a
                # later verify-fail revert works for forward-applied writes
                # too (the node wrote nothing — its patch list is empty)
                self._last_write = [
                    {"path": p.get("path"), "op": "replace",
                     "old": p.get("old"), "value": p.get("new")}
                    for p in (out.get("paths") or [])]
                self._cell(step.id, target,
                           "corrected" if attempt > 0 else "applied",
                           detail=f"{len(rows)} value(s) applied",
                           group_id=out.get("group_id"))
                return "applied"
            self._defer(step, target, f"write failed: {out.get('error')}", v)
            return "defer"

        if effective == "fail":
            if target_patches:
                out = self.writer.revert_patches(target_patches,
                                                 label=f"{step.id}:{target}")
                self._ledger("revert_applied", step=step.id, target=target,
                             **out)
                if not out.get("ok"):
                    self._defer(step, target,
                                f"revert failed: {out.get('error')}", v)
                    return "defer"
            can_retry = (attempt < step.retry_max and fam is not None
                         and v.failure_mode in (fam.adaptations or {}))
            if can_retry:
                self._cell(step.id, target, "retrying",
                           detail=f"{v.failure_mode}: {'; '.join(v.reasons[:1])}")
                return "retry"
            # terminal failure: an outstanding seed goes back to its pre value
            # (after the node-patch revert above restored the seeded state)
            self._restore_seed(step.id, target)
            if step.verify_of:
                # the wide verification REFUTED the discovery — revert the
                # discovered write too (its patches' values are current again
                # after this verify run's own revert), and flag the original
                orig = self._discoveries.pop((step.verify_of, target), None)
                if orig and orig.get("patches"):
                    out2 = self.writer.revert_patches(
                        orig["patches"], label=f"{step.id}:{target}:verify-fail")
                    self._ledger("verify_failed_original_reverted",
                                 step=step.verify_of, target=target, **out2)
                self._cell(step.verify_of, target, "deferred",
                           detail="wide verification failed — discovery reverted")
            self._defer(step, target,
                        f"{v.failure_mode}: {'; '.join(v.reasons[:2])}", v,
                        reverted=bool(target_patches))
            if step.criticality == "hard":
                with self._lock:
                    self.state["halted"][target] = (
                        f"hard step {step.id!r} failed ({v.failure_mode})")
                self._ledger("target_halted", step=step.id, target=target,
                             reason=v.failure_mode)
                # FYI, not an alarm: the other targets continue (D-8's
                # revert-and-continue), so this must not read as a night lost
                self._notify("target_halted", step=step.id, target=target,
                             reason=v.failure_mode, plan=self.plan.name)
            return "defer"

        # defer (abstain policy / unverifiable) — the node's write is KEPT for
        # review. Restore any outstanding seed CAS-guarded: if the node
        # overwrote the seeded path its write stands (CAS refuses), but if the
        # deferred run produced NO patch on the seed path the shifted window
        # must go back — never leave a deliberate scan shift on the chip.
        self._restore_seed(step.id, target)
        self._defer(step, target, "; ".join(v.reasons[:2]) or "unverifiable", v)
        return "defer"

    def _forward_rows(self, fam, target: str, run: dict) -> list[dict]:
        if fam is None or not fam.updates:
            return []
        entry = (run.get("fit_results") or {}).get(target) or {}
        try:
            rows = fam_mod.resolve_updates(fam, target, entry,
                                           run.get("parameters") or {},
                                           self.writer.current_value_of)
        except Exception:  # noqa: BLE001
            logger.exception("resolve_updates failed")
            return []
        # Declared gaps: fields this node writes that we cannot compute from
        # its own fit output (measured, docs/78 §23). The forward path only
        # runs when the node wrote nothing, so writing our subset leaves the
        # rest stale — a quiet partial, which r12 forbids. It is written (the
        # calibrated frequency is still worth having) but never silently: the
        # ledger and the review queue both name what was left behind.
        gaps = getattr(fam, "forward_gaps", None) or {}
        if rows and gaps:
            listed = {p.replace("{q}", target).replace("{pair}", target): why
                      for p, why in gaps.items()}
            self._ledger("forward_partial", target=target, family=fam.key,
                         wrote=[r["path"] for r in rows],
                         not_written=sorted(listed))
            with self._lock:
                self.state["review_queue"].append({
                    "step_id": None, "target": target,
                    "reason": (f"{fam.label}: wrote {len(rows)} field(s); "
                               f"{len(listed)} field(s) this node also writes "
                               f"were left unchanged — " +
                               "; ".join(f"{p} ({why})"
                                         for p, why in sorted(listed.items()))),
                    "failure_mode": None, "reverted": False,
                    "verdict": {"forward_gaps": sorted(listed)},
                })
            self._persist()
        # The rvp node's update is ATOMIC across frequency + readout amplitude
        # + the SHARED port FSP + every sibling amp on that feedline. Writing
        # only the frequency half silently de-couples the readout power
        # calibration (docs/56 §6G) — so build the coupled rows here too, and
        # when they can't be built from node-authored numbers say so in the
        # ledger rather than writing a quiet partial (r12 doctrine).
        if fam.key == power_rows.POWER_COUPLED_FAMILY and rows:
            merged = None
            view = getattr(self.writer, "merged_view", None)
            if callable(view):
                try:
                    merged = view()
                except Exception:  # noqa: BLE001
                    logger.exception("merged_view failed")
            if not isinstance(merged, dict):
                self._ledger("power_rows_skipped", target=target,
                             reason="no merged state+wiring view available")
            else:
                try:
                    pr = power_rows.coupled_power_rows(fam.key, target,
                                                       dict(entry), merged)
                except Exception as exc:  # noqa: BLE001
                    self._ledger("power_rows_skipped", target=target,
                                 reason=f"power rows failed: {exc}")
                else:
                    rows = rows + pr["rows"]
                    if pr["skipped"]:
                        self._ledger("power_rows_skipped", target=target,
                                     reason=pr["skipped"])
                    for w in pr["warnings"]:
                        self._ledger("power_rows_warning", target=target,
                                     reason=w)
        return rows

    def _defer(self, step: Step, target: str, reason: str,
               v: gates_mod.GateVerdict, *, reverted: bool = False) -> None:
        self._cell(step.id, target, "reverted" if reverted else "deferred",
                   detail=reason)
        with self._lock:
            self.state["review_queue"].append({
                "step_id": step.id, "target": target, "reason": reason,
                "failure_mode": v.failure_mode, "reverted": reverted,
                "verdict": v.as_dict(),
            })
        self._persist()

    def _end_restore(self) -> None:
        """review autonomy: put every first-touched path back to its pre-plan
        value — 'the chip ends where it started' (docs/56 §7b-A)."""
        rows = [{"path": p, "value": old} for p, old in
                self._preplan_values.items() if old is not None]
        # add-op patches carry no pre-plan value — they can't be restored;
        # surface them instead of silently leaving the key behind (audit E8)
        unrestorable = [p for p, old in self._preplan_values.items()
                        if old is None]
        if unrestorable:
            with self._lock:
                self.state["review_queue"].append({
                    "step_id": "(plan end)", "target": "(shared)",
                    "reason": ("review restore could not undo "
                               f"{len(unrestorable)} added key(s): "
                               + ", ".join(unrestorable[:4])),
                    "failure_mode": None, "reverted": False,
                    "verdict": {"paths": unrestorable},
                })
            self._persist()
        if not rows:
            return
        out = self.writer.restore_values(rows, label="plan-end restore")
        self._ledger("plan_restored", **out)


def _patch_target(p: dict) -> str | None:
    parts = [x for x in str(p.get("path", "")).split("/") if x]
    if parts and parts[0] == "quam":
        parts = parts[1:]
    return parts[1] if len(parts) > 1 else None


# `figures.<name>.png` is the SHEET; `figures.<name>.<target>.png` is one
# target's panel. The difference is a segment count, not a suffix — matching on
# the tail alone calls every sheet a panel, which silently disables the whole
# vision round (caught in the first end-to-end run).
_SHEET_RE = re.compile(r"^figures\.[^.]+\.png$")


def _first_figure(run: dict) -> Path | None:
    """The run's SHEET — every target on one picture. Stage 1 (triage) only."""
    folder = run.get("folder_path")
    if not folder:
        return None
    try:
        files = sorted(Path(folder).glob("figures.*.png"))
    except OSError:
        return None
    for f in files:
        if _SHEET_RE.match(f.name):
            return f
    return files[0] if files else None      # honest degrade, never nothing


def _panel_figure(run: dict, target: str) -> Path | None:
    """The single-panel figure for ONE target, or None.

    Stage 2 asks the per-target question; asking it against a sheet holding
    every target is the D-11.1 defect. When no panel exists the caller must
    fall back to the sheet AND say so — never silently.
    """
    folder = run.get("folder_path")
    if not folder or not target:
        return None
    try:
        for f in sorted(Path(folder).glob(f"figures.*.{target}.png")):
            return f
    except OSError:
        pass
    return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
