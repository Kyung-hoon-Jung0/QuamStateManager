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
                 abstain_policy: str = "defer"):
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
        self.plan_run_id = "af_" + uuid.uuid4().hex[:10]
        self.abort_event = threading.Event()
        self._thread: threading.Thread | None = None
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

    # ---- lifecycle -------------------------------------------------------

    def start(self) -> str:
        with _ENGINES_LOCK:
            cur = _ENGINES.get(self.instance_path)
            if cur is not None and cur.is_running():
                raise RuntimeError("an autofit plan is already running")
            _ENGINES[self.instance_path] = self
        self._ledger_dir.mkdir(parents=True, exist_ok=True)
        self.state["status"] = "running"
        self.state["started_at"] = _now()
        self._persist()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name=f"autofit-{self.plan_run_id}")
        self._thread.start()
        return self.plan_run_id

    def abort(self) -> None:
        self.abort_event.set()

    def is_running(self) -> bool:
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
            for step in [s for s in self.plan.steps if s.enabled]:
                if self.abort_event.is_set():
                    break
                alive = [t for t in self.targets
                         if t not in self.state["halted"]]
                if not alive:
                    break
                self._run_step(step, alive)
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

    def _run_step(self, step: Step, alive: list[str]) -> None:
        pending = list(alive)
        attempt = 0
        params = dict(step.params)
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

            verdicts = self._evaluate(step, res, pending)
            retry_targets: list[str] = []
            retry_mode: str | None = None
            fam = (fam_mod.family_for(res.run["experiment_name"])
                   if res.run else None)
            for t in pending:
                v = verdicts[t]
                self._ledger("verdict", step=step.id, attempt=attempt,
                             **v.as_dict())      # as_dict carries target
                decision = self._decide(step, t, v, res, fam, attempt)
                self._ledger("decision", step=step.id, attempt=attempt,
                             target=t, decision=decision)
                if decision == "retry":
                    retry_targets.append(t)
                    retry_mode = retry_mode or v.failure_mode
            pending = retry_targets
            if pending and fam is not None and retry_mode:
                rule = fam.adaptations.get(retry_mode)
                if rule is not None:
                    try:
                        overrides = rule(params)
                        params = {**params, **overrides}
                        self._ledger("params_adapted", step=step.id,
                                     mode=retry_mode, overrides=overrides)
                    except Exception:  # noqa: BLE001
                        logger.exception("adaptation rule failed")
            attempt += 1

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

        # LLM audit on suspects only (never on deterministic fails — one ack
        # never collapses two gates)
        for t, v in verdicts.items():
            if v.verdict != "suspect" or not self.auditor.enabled:
                continue
            entry = (run.get("fit_results") or {}).get(t) or {}
            figure = _first_figure(run)
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
        return verdicts

    # ---- decision + writes ------------------------------------------------

    def _decide(self, step: Step, target: str, v: gates_mod.GateVerdict,
                res: StepRunResult, fam, attempt: int) -> str:
        run = res.run or {}
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
            if target_patches:
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

        # defer (abstain policy / unverifiable)
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
