"""Autofit real backend — drives the Scheduler chassis (docs/56 §2a + §7b-B).

One plan step = one tagged queue item run by the existing hardened worker
(temp-copy param splice, kill-tree, fail-closed re-classification). The
design-review races are closed here:

* lost wakeup      → wait-loop watchdog re-``start()``s while the item is still
                     queued and the worker is dead/idle/paused (§7b-B1)
* attribution race → the engine never trusts the async refresh hook; after the
                     item is terminal this backend does its OWN synchronous
                     ingest: reconcile-by-path, bounded dataset re-poll, and
                     exact-name + time-window attribution (§7b-B2, F6)
* heartbeat        → every poll feeds ``touch_ui`` so the worker's
                     browser-heartbeat never pauses an unattended plan
* hygiene          → the autofit item is removed from the queue after ingest
                     (the ledger is the record; the queue stays clean)

The Flask-side collaborators arrive via ``RealAdapter`` so this module stays
import-clean of routes (and testable by monkeypatching ``scheduler``).
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from quam_state_manager.core import node_scan, safe_io, scheduler
from quam_state_manager.core.autofit.engine import StepRunResult
from quam_state_manager.core.autofit.families import normalize_node_name
from quam_state_manager.core.autofit.plan import Step

logger = logging.getLogger(__name__)

_POLL_S = 1.0
_WATCHDOG_S = 5.0            # re-start() if still queued with no live worker
_ATTRIBUTION_POLL_S = 10.0   # bounded re-poll for the run folder to appear
_AUTOFIT_LABEL = "autofit"


@dataclass
class RealAdapter:
    """Flask-side collaborators, captured once at plan start."""
    instance_path: str
    # pull the chip folder's live files into the loaded ctx (reconcile-by-path)
    reconcile: Callable[[], None]
    # rescan all active dataset stores; return [(run_info, folder_key)] newest-first
    rescan_and_list_runs: Callable[[], list[Any]]
    # per-step timeout fallback (scheduler settings own the real watchdog)
    step_timeout_s: float = 3600.0


class RealBackend:
    def __init__(self, adapter: RealAdapter, resolved_files: dict[str, str]):
        self.adapter = adapter
        self.resolved_files = resolved_files      # step_id -> abs node path

    # ------------------------------------------------------------------
    def run_step(self, step: Step, targets: list[str], params: dict,
                 attempt: int, abort: threading.Event) -> StepRunResult:
        inst = self.adapter.instance_path
        source = self.resolved_files.get(step.id)
        # v2 runtime-inserted steps (docs/56 v2) aren't in the plan-start map.
        # Resolve by KIND — a cross-node re-cal runs a DIFFERENT family's node
        # and MUST use its engine-resolved path (scan-candidate-derived, never
        # client input); it is fail-closed rather than ever falling back to
        # the ORIGINAL node id (which would make cross-node escalation a silent
        # no-op on hardware). A verify_wide / escalation CONTINUATION re-runs
        # the original step's file (its own node if node-based, else base id).
        if not source and step.inserted_by == "escalation_recal":
            if step.node and Path(step.node).is_file():
                source = step.node
            else:
                return StepRunResult(
                    status="failed",
                    error=f"escalation re-cal {step.id!r}: node {step.node!r} "
                          "unresolved (refusing to run the original node)")
        if not source and step.verify_of:
            # verify_of may itself be a runtime id (a verify of an escalation
            # continuation) — fall back to the ROOT plan-step id
            source = (self.resolved_files.get(step.verify_of)
                      or self.resolved_files.get(step.verify_of.split("__")[0]))
        if not source and step.inserted_by == "escalation":
            source = (step.node if step.node and Path(step.node).is_file()
                      else self.resolved_files.get(step.id.rsplit("__", 1)[0]))
        if not source:
            return StepRunResult(status="failed",
                                 error=f"step {step.id!r}: no resolved node file")
        info_scan = node_scan.scan_file(Path(source))
        if info_scan.error is not None:
            return StepRunResult(status="failed",
                                 error=f"node unparseable: {info_scan.error}")
        window_start = datetime.now(timezone.utc)
        item = scheduler.add_item(inst, {
            "file": str(source),
            "name": info_scan.name,
            "kind": info_scan.kind,
            "has_hook": info_scan.has_hook,
            "targets_name": info_scan.targets_name,
            "param_overrides": dict(params),
            "label": f"{_AUTOFIT_LABEL}: {step.id} (attempt {attempt + 1})",
        }, targets=list(targets))
        item_id = item["id"]
        scheduler.start(inst)

        status, err = self._wait(item_id, abort)
        if status == "aborted":
            # never leak the queued/cancelled item — the user's next Scheduler
            # ▶ would otherwise run it on hardware with the plan's overrides
            # (audit E4; _remove_item refuses to drop a still-running item)
            self._remove_item(item_id)
            return StepRunResult(status="aborted")

        # --- engine-owned synchronous ingest (never trust the async hook) --
        try:
            self.adapter.reconcile()
        except Exception:  # noqa: BLE001
            logger.exception("post-item reconcile failed")
        run = None
        if status == "done":
            run = self._attribute(info_scan.name, window_start)
        self._remove_item(item_id)
        if status == "skipped":
            return StepRunResult(status="skipped", error=err)
        if status != "done":
            return StepRunResult(status="failed",
                                 error=err or f"item ended {status}")
        if run is None:
            return StepRunResult(
                status="failed",
                error="run finished but no dataset run could be attributed "
                      "(name/time-window match failed) — unverifiable")
        return StepRunResult(status="done", run=run,
                             run_ref={"run_id": run.get("run_id"),
                                      "name": run.get("experiment_name")})

    # ------------------------------------------------------------------
    def _wait(self, item_id: str, abort: threading.Event) -> tuple[str, str | None]:
        """Poll until the item is terminal. Feeds the heartbeat; watchdog
        restarts a stranded worker; honors abort via scheduler.cancel."""
        inst = self.adapter.instance_path
        deadline = time.monotonic() + self.adapter.step_timeout_s
        queued_since: float | None = None
        while True:
            if abort.is_set():
                try:
                    scheduler.cancel(inst)
                except Exception:  # noqa: BLE001
                    logger.exception("cancel failed")
                return "aborted", None
            scheduler.touch_ui(inst)
            state = scheduler.load_queue(inst)
            it = scheduler._find(state, item_id)
            if it is None:
                return "failed", "item vanished from the queue"
            st = it.get("status")
            if st in ("done", "failed", "cancelled", "skipped"):
                if st == "done":
                    return "done", None
                # the chassis' dry-run refusal is benign (docs/56 skipped
                # semantics); failed/cancelled are real failures
                return ("skipped" if st == "skipped" else "failed"), \
                    it.get("error")
            run_status = state["run"].get("status")
            if st == "queued":
                now = time.monotonic()
                queued_since = queued_since or now
                # lost-wakeup watchdog (§7b-B1): still queued with no live
                # worker (exited on the empty queue, or a foreign pause left
                # it idle/paused) → start() again, idempotent under _QLOCK
                if now - queued_since > _WATCHDOG_S and (
                        not scheduler.is_running(inst)
                        or run_status in ("idle", "paused")):
                    logger.info("autofit watchdog: re-starting the scheduler "
                                "worker for stranded item %s", item_id)
                    scheduler.start(inst)
                    queued_since = now
            else:
                queued_since = None
            if time.monotonic() > deadline:
                try:
                    scheduler.cancel(inst)
                except Exception:  # noqa: BLE001
                    pass
                return "failed", "autofit step timeout"
            time.sleep(_POLL_S)

    # ------------------------------------------------------------------
    def _attribute(self, node_name: str, window_start: datetime) -> dict | None:
        """Exact-provenance attribution (§7b-B2 / review F6): the newest run
        whose normalized name EQUALS the node's and whose run_start falls in
        [window_start, now]; bounded re-poll while the writeback lands."""
        want = normalize_node_name(node_name)
        deadline = time.monotonic() + _ATTRIBUTION_POLL_S
        while True:
            try:
                runs = self.adapter.rescan_and_list_runs()
            except Exception:  # noqa: BLE001
                logger.exception("dataset rescan failed")
                runs = []
            for run in runs:                        # newest-first
                name = getattr(run, "experiment_name", None) or \
                    (run.get("experiment_name") if isinstance(run, dict) else None)
                # normalized-PREFIX match (dataset run names carry decorations
                # like `_new` / graph prefixes); the time window below is the
                # hard provenance guard — we launched the only matching run
                # inside it
                if not normalize_node_name(name or "").startswith(want):
                    continue
                started = _parse_ts(_attr(run, "run_start"))
                if started is not None and started < window_start:
                    continue
                return self._run_to_dict(run)
            if time.monotonic() > deadline:
                return None
            time.sleep(1.0)

    def _run_to_dict(self, run) -> dict:
        folder = _attr(run, "folder_path")
        patches: list[dict] = []
        try:
            node_json = safe_io.read_json(Path(folder) / "node.json")
            patches = node_json.get("patches") or []
        except (OSError, ValueError):
            logger.warning("run node.json unreadable — patches unavailable")
        return {
            "experiment_name": _attr(run, "experiment_name"),
            "fit_results": _attr(run, "fit_results") or {},
            "outcomes": _attr(run, "outcomes") or {},
            "parameters": _attr(run, "parameters") or {},
            "folder_path": folder,
            "patches": patches,
            "run_id": _attr(run, "run_id"),
        }

    def _remove_item(self, item_id: str) -> None:
        try:
            with scheduler._QLOCK:
                state = scheduler.load_queue(self.adapter.instance_path)
                state["queue"] = [i for i in state["queue"]
                                  if i.get("id") != item_id
                                  or i.get("status") == "running"]
                scheduler.save_queue(self.adapter.instance_path, state)
        except Exception:  # noqa: BLE001
            logger.exception("autofit queue-item cleanup failed")


def _attr(run, key, default=None):
    if isinstance(run, dict):
        return run.get(key, default)
    return getattr(run, key, default)


def _parse_ts(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None
