"""Autofit sim backend + sim writer — the hardware-free loop (docs/56 §2b).

``SimBackend`` "runs" a step by generating a synthetic run folder into a
dataset root (indistinguishable to every SM reader) and applying node-style
patches to the ``SimChip`` — exactly what a real node subprocess does to the
live files. A ``corruption_plan`` injects per-(step, target, attempt) bad
fits so retry/convergence is exercised deterministically:

    {("qubit_spec", "qA1"): ["wrong_peak"],          # attempt 0 bad, then clean
     ("t1", "qA2"): ["noisy", "noisy", None]}        # two bad attempts

``SimWriter`` implements the engine's Writer protocol directly over the
SimChip (same CAS/exact-typing semantics as core.autofit.writer) for pure
state-machine tests; the E2E tier swaps in the RealWriter over an actual
QuamStore loaded from the sim chip's files.
"""
from __future__ import annotations

import math
import threading
from itertools import count
from pathlib import Path
from typing import Any

from quam_state_manager.core.autofit import synth
from quam_state_manager.core.autofit.engine import StepRunResult
from quam_state_manager.core.autofit.plan import Step
from quam_state_manager.core.autofit.synth import SimChip, patch_path_to_dotted

# family key → synth generator node name
FAMILY_TO_NODE: dict[str, str] = {
    "resonator_spectroscopy": "03_resonator_spectroscopy",
    "qubit_spectroscopy": "08_qubit_spectroscopy",
    "power_rabi": "11_power_rabi",
    "ramsey": "12_ramsey",
    "T1": "25_T1",
    "echo": "26_echo",
    "readout_frequency_optimization": "15a_readout_frequency_optimization",
    "iq_blobs": "16_iq_blobs",
    "chevron_11_02": "31_chevron_11_02",
    "cz_conditional_phase": "32_cz_conditional_phase",
    # the error-amp variant IS a conditional-phase scan (narrower window)
    "cz_conditional_phase_error_amp": "32_cz_conditional_phase",
}


class SimBackend:
    def __init__(self, chip: SimChip, dataset_root: Path, *, seed: int = 0,
                 corruption_plan: dict | None = None,
                 crash_plan: dict | None = None):
        self.chip = chip
        self.dataset_root = Path(dataset_root)
        self.seed = seed
        # {(step_id, target): [mode per attempt] | mode}
        self.corruption_plan = corruption_plan or {}
        # {(step_id, attempt): error string} — whole-item crashes
        self.crash_plan = crash_plan or {}
        self._ids = count(1000)
        self.runs: list[synth.SynthRun] = []

    def _mode_for(self, step_id: str, target: str, attempt: int):
        spec = self.corruption_plan.get((step_id, target))
        if spec is None:
            return None
        if isinstance(spec, str):
            return spec if attempt == 0 else None
        try:
            return spec[attempt]
        except IndexError:
            return None

    def run_step(self, step: Step, targets: list[str], params: dict,
                 attempt: int, abort: threading.Event) -> StepRunResult:
        crash = self.crash_plan.get((step.id, attempt))
        if crash:
            return StepRunResult(status="failed", error=crash)
        if abort.is_set():
            return StepRunResult(status="aborted")
        fam_key = step.family or ""
        node_name = FAMILY_TO_NODE.get(fam_key)
        if node_name is None and step.node:
            stem = step.node.rsplit("/", 1)[-1].rsplit(".py", 1)[0]
            node_name = stem if stem in synth.GENERATORS else None
        if node_name is None:
            # benign: real-lab-only steps (e.g. phase compensation) have no
            # sim generator — the demo skips them instead of failing the plan
            return StepRunResult(status="skipped",
                                 error=f"no sim generator for step "
                                       f"{step.id!r} (family={fam_key!r})")
        corrupt = {t: self._mode_for(step.id, t, attempt) for t in targets}
        run_id = next(self._ids)
        sr = synth.synth_run(node_name, self.chip, list(targets),
                             self.dataset_root, run_id, params=params,
                             corrupt=corrupt, seed=self.seed)
        self.runs.append(sr)
        return StepRunResult(
            status="done",
            run={
                "experiment_name": sr.node_name,
                "fit_results": sr.fit_results,
                "outcomes": {t: ("successful"
                                 if (sr.fit_results.get(t) or {}).get("success")
                                 else "failed") for t in targets},
                "parameters": dict(params),
                "folder_path": sr.folder,
                "patches": sr.patches,
            },
            run_ref={"run_id": run_id, "name": sr.node_name,
                     "uid": f"sim:{run_id}"})


class SimWriter:
    """Writer protocol over the SimChip — CAS + exact-typed, no coercion."""

    def __init__(self, chip: SimChip):
        self.chip = chip
        self.log: list[dict] = []

    def current_value_of(self, dotted: str):
        return self.chip.get(dotted)

    def apply_rows(self, rows, *, label: str) -> dict:
        paths = []
        for r in rows:
            old = self._safe_get(r["path"])
            self.chip.set(r["path"], r["value"])
            paths.append({"path": r["path"], "old": old, "new": r["value"]})
        out = {"ok": True, "action": "applied", "group_id": f"sim:{label}",
               "paths": paths, "error": None, "conflicts": []}
        self.log.append(out)
        return out

    def revert_patches(self, patches, *, label: str) -> dict:
        paths, conflicts = [], []
        for p in patches:
            dotted = patch_path_to_dotted(p.get("path", ""))
            old = p.get("old")
            if p.get("op", "replace") != "replace" or old is None \
                    or isinstance(old, (dict, list)):
                conflicts.append({"path": dotted, "reason": "non-revertible"})
                continue
            cur = self._safe_get(dotted)
            if not _eq(cur, p.get("value")):
                conflicts.append({"path": dotted,
                                  "reason": f"CAS: current={cur!r}"})
                continue
            self.chip.set(dotted, old)
            paths.append({"path": dotted, "old": p.get("value"), "new": old})
        ok = bool(paths)
        out = {"ok": ok, "action": "reverted", "group_id": f"sim:{label}",
               "paths": paths,
               "error": None if ok else "nothing revertible",
               "conflicts": conflicts}
        self.log.append(out)
        return out

    def restore_values(self, rows, *, label: str) -> dict:
        paths = []
        for r in rows:
            old = self._safe_get(r["path"])
            self.chip.set(r["path"], r["value"])
            paths.append({"path": r["path"], "old": old, "new": r["value"]})
        out = {"ok": True, "action": "restored", "group_id": f"sim:{label}",
               "paths": paths, "error": None, "conflicts": []}
        self.log.append(out)
        return out

    def _safe_get(self, dotted: str):
        try:
            return self.chip.get(dotted)
        except (KeyError, TypeError):
            return None


def _eq(a: Any, b: Any) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) \
            and not isinstance(a, bool) and not isinstance(b, bool):
        return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=0.0)
    return a == b


class LiveSimBackend(SimBackend):
    """SimBackend that behaves like a REAL node process against live files:
    it RELOADS the chip state from the live folder before each run (nodes do
    ``Quam.load()`` at start — so an engine revert genuinely changes what the
    next attempt measures/sweeps around) and WRITES the post-run state back
    (the node's ``machine.save()``). This is the E2E backend: the engine +
    RealWriter + working-copy machinery operate on the same live folder,
    exercising the full docs/56 §2f write path hardware-free."""

    def __init__(self, chip: SimChip, dataset_root: Path, live_folder: Path,
                 **kw):
        super().__init__(chip, dataset_root, **kw)
        self.live_folder = Path(live_folder)

    def run_step(self, step, targets, params, attempt, abort):
        from quam_state_manager.core import safe_io

        state, wiring = safe_io.read_state_wiring(self.live_folder)
        self.chip.state = state
        self.chip.wiring = wiring
        res = super().run_step(step, targets, params, attempt, abort)
        if res.status == "done":
            safe_io.write_state_wiring(self.live_folder, self.chip.state,
                                       self.chip.wiring)
        return res
