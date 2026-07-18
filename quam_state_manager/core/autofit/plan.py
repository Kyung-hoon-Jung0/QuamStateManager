"""Autofit plan model — the user's sequence, validated (docs/56 §3).

A *plan* is an ordered list of steps over a target set; a *plan run* is one
execution of it. Steps reference node ``.py`` files relative to the
scheduler's calibrations folder (the same files the Scheduler queues), carry
per-step params (validated downstream against the scheduler's scanned
schemas), a retry budget, and a criticality. Shipped presets encode the
standard 1Q bringup and CZ tuneup chains; user plans persist in
``instance/autofit_plans.json``.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_PLANS_FILE = "autofit_plans.json"

VALID_AUTONOMY = ("full", "review")   # 'staged' dropped (docs/56
# §7b-A — nodes self-write live, a working-copy-only mode is incoherent);
# legacy plans carrying it are mapped to 'review' in validate_plan
VALID_CRITICALITY = ("hard", "soft")
_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


@dataclass
class Step:
    id: str
    node: str = ""                 # node file, relative to calibrations folder;
                                   # "" ⇒ resolved from ``family`` at plan build
    family: str = ""               # portable family key (docs/56 §7b-D) — the
                                   # normalizer resolves it against the scanned
                                   # library, surviving per-lab renumbering
    label: str = ""
    params: dict = field(default_factory=dict)
    retry_max: int = 1
    criticality: str = "hard"      # hard: failure halts THIS target's chain
    enabled: bool = True
    # -- v2 runtime-inserted steps (never user-authored; validate_plan does
    # not accept them from raw plans — the engine synthesizes them) ---------
    only_targets: tuple = ()       # restrict to these targets (verify/retry)
    verify_of: str = ""            # this step wide-verifies that step's find
    inserted_by: str = ""          # "" | verify_wide | escalation

    def as_dict(self) -> dict:
        d = asdict(self)
        d["only_targets"] = list(self.only_targets)
        return d


@dataclass
class Plan:
    name: str
    targets_kind: str              # qubits | qubit_pairs
    steps: list[Step]
    targets: list[str] = field(default_factory=list)   # [] = all active
    autonomy: str = "review"       # full | review
    version: int = 1
    preset: str | None = None      # provenance: which preset seeded this plan

    def as_dict(self) -> dict:
        d = asdict(self)
        d["steps"] = [s.as_dict() if isinstance(s, Step) else s
                      for s in self.steps]
        return d


class PlanError(ValueError):
    pass


def validate_plan(raw: dict) -> Plan:
    """Parse + validate a plan dict. Raises :class:`PlanError` with a
    user-facing message on the first problem."""
    if not isinstance(raw, dict):
        raise PlanError("plan must be an object")
    name = str(raw.get("name") or "").strip()
    if not name:
        raise PlanError("plan needs a name")
    kind = raw.get("targets_kind")
    if kind not in ("qubits", "qubit_pairs"):
        raise PlanError(f"targets_kind must be qubits|qubit_pairs, got {kind!r}")
    autonomy = raw.get("autonomy", "review")
    if autonomy == "staged":       # legacy alias (docs/56 §7b-A)
        autonomy = "review"
    if autonomy not in VALID_AUTONOMY:
        raise PlanError(f"autonomy must be one of {VALID_AUTONOMY}, got {autonomy!r}")
    targets = raw.get("targets") or []
    if not isinstance(targets, list) or not all(isinstance(t, str) for t in targets):
        raise PlanError("targets must be a list of names")
    steps_raw = raw.get("steps")
    if not isinstance(steps_raw, list) or not steps_raw:
        raise PlanError("plan needs at least one step")
    steps: list[Step] = []
    seen_ids: set[str] = set()
    for i, s in enumerate(steps_raw):
        if not isinstance(s, dict):
            raise PlanError(f"step {i} must be an object")
        sid = str(s.get("id") or f"step{i + 1}")
        if not _ID_RE.match(sid):
            raise PlanError(f"step id {sid!r} invalid (alnum/_/- only)")
        if sid in seen_ids:
            raise PlanError(f"duplicate step id {sid!r}")
        seen_ids.add(sid)
        node = str(s.get("node") or "").strip()
        family = str(s.get("family") or "").strip()
        if not node and not family:
            raise PlanError(f"step {sid}: needs a node file or a family key")
        if node:
            if node.startswith(("/", "\\")) or ".." in node.split("/") \
                    or ".." in node.split("\\"):
                raise PlanError(f"step {sid}: node must be a plain relative "
                                f".py filename, got {node!r}")
            if not node.endswith(".py"):
                raise PlanError(f"step {sid}: node must be a .py file")
        params = s.get("params") or {}
        if not isinstance(params, dict):
            raise PlanError(f"step {sid}: params must be an object")
        try:
            retry_max = int(s.get("retry_max", 1))
        except (TypeError, ValueError):
            raise PlanError(f"step {sid}: retry_max must be an integer") from None
        if not 0 <= retry_max <= 5:
            raise PlanError(f"step {sid}: retry_max must be 0..5")
        crit = s.get("criticality", "hard")
        if crit not in VALID_CRITICALITY:
            raise PlanError(f"step {sid}: criticality must be hard|soft")
        steps.append(Step(id=sid, node=node, family=family,
                          label=str(s.get("label") or ""),
                          params=dict(params), retry_max=retry_max,
                          criticality=crit,
                          enabled=bool(s.get("enabled", True))))
    return Plan(name=name, targets_kind=kind, steps=steps, targets=targets,
                autonomy=autonomy, version=int(raw.get("version", 1)),
                preset=raw.get("preset"))


# ---------------------------------------------------------------------------
# Shipped presets (docs/56 §3 + §7b-D/E) — steps pin PORTABLE family keys;
# concrete .py files are resolved against the scanned calibrations folder at
# plan build (per-lab renumbering survives). Ordering follows the proven
# bringup graph (readout opt + IQ blobs BEFORE the coherence steps — state
# discrimination needs thresholds first; physics review #3).
# ---------------------------------------------------------------------------

PRESETS: dict[str, dict] = {
    "1q_bringup": {
        "name": "1Q bringup",
        "targets_kind": "qubits",
        "autonomy": "full",
        "preset": "1q_bringup",
        "steps": [
            {"id": "res_spec", "family": "resonator_spectroscopy",
             "label": "Resonator spectroscopy", "criticality": "hard",
             "retry_max": 1},
            {"id": "qubit_spec", "family": "qubit_spectroscopy",
             "label": "Qubit spectroscopy", "criticality": "hard",
             "retry_max": 2},
            {"id": "power_rabi", "family": "power_rabi",
             "label": "Power Rabi", "criticality": "hard", "retry_max": 1},
            {"id": "readout_freq", "family": "readout_frequency_optimization",
             "label": "Readout frequency opt", "criticality": "soft",
             "retry_max": 1},
            {"id": "iq_blobs", "family": "iq_blobs", "label": "IQ blobs",
             "criticality": "soft", "retry_max": 1},
            {"id": "ramsey", "family": "ramsey", "label": "Ramsey",
             "criticality": "hard", "retry_max": 1},
            {"id": "t1", "family": "T1", "label": "T1",
             "criticality": "soft", "retry_max": 1},
            {"id": "echo", "family": "echo", "label": "Echo (T2)",
             "criticality": "soft", "retry_max": 1},
        ],
    },
    "cz_tuneup": {
        "name": "CZ tuneup",
        "targets_kind": "qubit_pairs",
        "autonomy": "full",
        "preset": "cz_tuneup",
        "steps": [
            {"id": "chevron", "family": "chevron_11_02",
             "label": "Chevron 11↔02", "criticality": "hard", "retry_max": 1},
            # coarse (±3%) BEFORE the error-amp variant (±0.5–1% capture
            # window would miss a chevron-grade amplitude — physics review #4)
            {"id": "cond_phase", "family": "cz_conditional_phase",
             "label": "Conditional phase (coarse)", "criticality": "hard",
             "retry_max": 2, "params": {"operation": "cz_unipolar"}},
            {"id": "cond_phase_ea", "node": "33_cz_conditional_phase_error_amp.py",
             "family": "cz_conditional_phase_error_amp",
             "label": "Conditional phase (error-amp)", "criticality": "soft",
             "retry_max": 1, "params": {"operation": "cz_unipolar",
                                        "amp_range": 0.005}},
            # read-then-fold update — run-and-verify only, excluded from
            # auto-write (fit_targets.py:19–39); the node itself still applies
            {"id": "phase_comp", "node": "35_cz_phase_compensation.py",
             "family": "cz_phase_compensation",
             "label": "Phase compensation (verify-only)",
             "criticality": "soft", "retry_max": 0,
             "params": {"operation": "cz_unipolar"}},
        ],
    },
}


def preset_plan(key: str) -> Plan:
    if key not in PRESETS:
        raise PlanError(f"unknown preset {key!r}")
    return validate_plan(json.loads(json.dumps(PRESETS[key])))


# ---------------------------------------------------------------------------
# Family-key → node-file resolution (docs/56 §7b-D)
# ---------------------------------------------------------------------------

def resolve_steps(p: Plan, available: list[dict]) -> dict[str, dict]:
    """Resolve each step to a concrete node file from the scanned library.

    ``available``: ``[{"name": <node name>, "path": <abs path>}, ...]`` (the
    scheduler's ``node_scan.scan_folder`` output shape). Matching uses the
    SAME normalizer as recipes/fit_targets, so per-lab renumbering/prefixing
    doesn't break presets. Returns per step id:
    ``{"status": "resolved"|"ambiguous"|"missing", "path": str|None,
       "candidates": [paths]}``. A step with an explicit ``node`` file that
    exists in the scan resolves to it directly; its ``family`` is only the
    fallback.
    """
    from quam_state_manager.core.autofit.families import normalize_node_name

    out: dict[str, dict] = {}
    by_name = {(a.get("name") or ""): a for a in available}
    for step in p.steps:
        # explicit filename first (exact scan-name or filename match)
        if step.node:
            stem = step.node.rsplit("/", 1)[-1].rsplit(".py", 1)[0]
            hit = by_name.get(stem)
            if hit is None:
                for a in available:
                    ap = str(a.get("path") or "")
                    if ap.replace("\\", "/").endswith("/" + step.node) \
                            or ap.endswith(step.node):
                        hit = a
                        break
            if hit is not None:
                out[step.id] = {"status": "resolved",
                                "path": str(hit.get("path")),
                                "candidates": [str(hit.get("path"))]}
                continue
        if not step.family:
            out[step.id] = {"status": "missing", "path": None, "candidates": []}
            continue
        want = normalize_node_name(step.family)
        cands = [a for a in available
                 if normalize_node_name(a.get("name") or "").startswith(want)]
        if not cands:
            out[step.id] = {"status": "missing", "path": None, "candidates": []}
        elif len({str(c.get("path")) for c in cands}) == 1:
            out[step.id] = {"status": "resolved",
                            "path": str(cands[0].get("path")),
                            "candidates": [str(cands[0].get("path"))]}
        else:
            # prefer the SHORTEST normalized name (the plain family node, not
            # a decorated variant), but surface the ambiguity to the UI
            cands.sort(key=lambda a: len(normalize_node_name(a.get("name") or "")))
            paths = [str(c.get("path")) for c in cands]
            exact = [c for c in cands
                     if normalize_node_name(c.get("name") or "") == want]
            if len(exact) == 1:
                out[step.id] = {"status": "resolved",
                                "path": str(exact[0].get("path")),
                                "candidates": paths}
            else:
                out[step.id] = {"status": "ambiguous", "path": paths[0],
                                "candidates": paths}
    return out


# ---------------------------------------------------------------------------
# User-plan persistence
# ---------------------------------------------------------------------------

def load_plans(instance_path) -> dict[str, dict]:
    p = Path(instance_path) / _PLANS_FILE
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_plan(instance_path, plan: Plan, plan_id: str | None = None) -> str:
    from quam_state_manager.core import safe_io

    plans = load_plans(instance_path)
    pid = plan_id or uuid.uuid4().hex[:12]
    plans[pid] = plan.as_dict()
    Path(instance_path).mkdir(parents=True, exist_ok=True)
    safe_io.atomic_write_json(Path(instance_path) / _PLANS_FILE, plans)
    return pid


def delete_plan(instance_path, plan_id: str) -> bool:
    from quam_state_manager.core import safe_io

    plans = load_plans(instance_path)
    if plan_id not in plans:
        return False
    plans.pop(plan_id)
    safe_io.atomic_write_json(Path(instance_path) / _PLANS_FILE, plans)
    return True
