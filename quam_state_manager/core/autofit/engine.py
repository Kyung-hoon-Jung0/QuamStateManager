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
                 history_points_of: Callable[[str, str], list[float] | None]
                 | None = None,
                 abstain_policy: str = "defer",
                 is_sim: bool = False,
                 resolve_node: Callable[[str], str | None] | None = None):
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
                step = queue.popleft()
                alive = [t for t in self.targets
                         if t not in self.state["halted"]]
                if not alive:
                    break
                if step.only_targets:
                    alive = [t for t in alive if t in step.only_targets]
                    if not alive:
                        continue        # its targets halted — skip, not end
                self._run_step(step, alive, queue)
            if self.autonomy == "review" and not self.abort_event.is_set():
                self._end_restore()
            with self._lock:
                self.state["status"] = ("aborted" if self.abort_event.is_set()
                                        else "done")
                self.state["ended_at"] = _now()
                self.state["current"] = None
            self._ledger("plan_done", status=self.state["status"],
                         review_queue=len(self.state["review_queue"]))
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

    def _evaluate(self, step: Step, res: StepRunResult,
                  targets: list[str]) -> dict[str, gates_mod.GateVerdict]:
        if res.status != "done" or not res.run:
            return {t: gates_mod.GateVerdict(
                target=t, verdict="fail", failure_mode="node_failed",
                reasons=[res.error or f"run status {res.status}"])
                for t in targets}
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
            return out

        patched_old = {patch_path_to_dotted(p.get("path", "")): p.get("old")
                       for p in (run.get("patches") or [])}

        def pre_update(path: str):
            if path in patched_old:
                return patched_old[path]
            return self.writer.current_value_of(path)

        hp = None
        if self.history_points_of is not None:
            hp = lambda t: self.history_points_of(fam.value_key, t)  # noqa: E731

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
        for t, v in verdicts.items():
            if not self.auditor.enabled:
                break
            entry = (run.get("fit_results") or {}).get(t) or {}
            figure = _first_figure(run)
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
        return verdicts

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
            return fam_mod.resolve_updates(fam, target, entry,
                                           run.get("parameters") or {},
                                           self.writer.current_value_of)
        except Exception:  # noqa: BLE001
            logger.exception("resolve_updates failed")
            return []

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


def _first_figure(run: dict) -> Path | None:
    folder = run.get("folder_path")
    if not folder:
        return None
    try:
        for f in sorted(Path(folder).glob("figures.*.png")):
            return f
    except OSError:
        pass
    return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
