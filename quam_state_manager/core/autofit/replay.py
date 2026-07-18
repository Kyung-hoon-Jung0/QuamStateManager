"""Autofit offline replay harness — real-archive validation (docs/56 §6 tier R).

For a SAVED run folder (never modified — everything lands in *out_root*):

    parse stored claim → gate pipeline (G1/G4/…) → node-faithful REFIT +
    re-plot in the QM env (generator/run_autofit_replay.py) → codify
    stored-vs-fresh → sandbox state fix (revert the node's own patches, or
    apply the refit value when asked) → before/after state diff → report row

The vision round is separate: the report lists (stored figure, refit figure)
pairs; a human/frontier-vision auditor records judge-only verdicts against
them (docs/47 — a self-consistent noise fit fools any replay; only the figure
exposes it).

CLI (dev tool):
    python -m quam_state_manager.core.autofit.replay --runs <folder>[,<folder>…]
        --out <dir> [--python <env python>] [--source-root <tree>] [--fix refit|revert]
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from quam_state_manager.core import safe_io
from quam_state_manager.core.autofit import families as fam_mod
from quam_state_manager.core.autofit import gates as gates_mod
from quam_state_manager.core.autofit.synth import patch_path_to_dotted

# dev defaults for THIS workstation (override via CLI/kwargs; the gated test
# auto-skips when absent)
DEFAULT_PYTHON = "/mnt/c/ProgramData/miniconda3/envs/QRS/python.exe"
DEFAULT_SOURCE_ROOT = ("/mnt/d/work_laptop/Customer_Codes/QRS/"
                       "qualibration_graphs/superconducting")

# family key -> util module override (node-name derivation isn't 1:1 here)
_UTIL_OVERRIDES = {
    "resonator_spectroscopy_vs_power": "resonator_spectroscopy_vs_power_iq",
    "qubit_spectroscopy_vs_power": "qubit_spectroscopy_vs_amplitude",
}

_RUNNER = Path(__file__).resolve().parents[1].parent / "generator" \
    / "run_autofit_replay.py"


def _to_native(path: str | Path, python_path: str) -> str:
    """WSL→Windows path when the interpreter is a Windows .exe."""
    p = str(path)
    if python_path.endswith(".exe") and p.startswith("/"):
        out = subprocess.run(["wslpath", "-w", p], capture_output=True,
                             text=True, check=True)
        return out.stdout.strip()
    return p


def replay_run(run_folder: Path, out_dir: Path, *, python_path: str,
               source_root: str, util: str | None = None,
               timeout: float = 600.0) -> dict:
    """Subprocess refit+replot; returns the afreplay/v1 envelope (or an
    error-shaped dict)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    envelope = out_dir / "envelope.json"
    argv = [python_path, _to_native(_RUNNER, python_path),
            "--run", _to_native(run_folder, python_path),
            "--source-root", _to_native(source_root, python_path),
            "--figs-out", _to_native(out_dir, python_path),
            "--out", _to_native(envelope, python_path)]
    if util:
        argv += ["--util", util]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"errors": [{"stage": "spawn", "trace": str(exc)}], "qubits": {}}
    try:
        return json.loads(envelope.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"errors": [{"stage": "envelope",
                            "trace": (proc.stderr or "")[-800:]}], "qubits": {}}


def _codify(stored: dict, fresh: dict, tol: float, value_key: str) -> tuple[str, str]:
    """docs/50 codifier over (stored claim, fresh replay) for one qubit."""
    s_ok = bool(stored.get("success"))
    f_ok = bool(fresh.get("success"))
    sv, fv = stored.get(value_key), fresh.get(value_key)
    if not fresh:
        return "unverifiable", "replay produced no result"
    if s_ok and not f_ok:
        return "reject", "stored success — today's own analysis REJECTS this data"
    if not s_ok and f_ok:
        return "recover", f"stored failure — fresh analysis finds {fv!r}"
    if not s_ok and not f_ok:
        return "agrees_fail", "both gates agree the data yields no fit"
    if isinstance(sv, (int, float)) and isinstance(fv, (int, float)):
        if abs(sv - fv) > tol:
            return "drift", (f"both succeed but values differ by "
                             f"{abs(sv - fv):.3g} (> {tol:.0e})")
        return "agrees", f"fresh value within {tol:.0e} of stored"
    return "unverifiable", "values not comparable"


def evaluate_run(run_folder: Path, out_dir: Path, *,
                 python_path: str = DEFAULT_PYTHON,
                 source_root: str = DEFAULT_SOURCE_ROOT,
                 fix: str = "revert") -> dict:
    """The full replay row for one run. ``fix``: revert | refit | none —
    what the sandbox state fix applies for bad verdicts."""
    run_folder = Path(run_folder)
    node = safe_io.read_json(run_folder / "node.json")
    data = safe_io.read_json(run_folder / "data.json")
    name = (node.get("metadata") or {}).get("name") or run_folder.name
    fam = fam_mod.family_for(name)
    fit_results = data.get("fit_results") or {}
    outcomes = (node.get("data") or {}).get("outcomes") or {}
    params = ((node.get("data") or {}).get("parameters") or {}).get("model") or {}
    patches = node.get("patches") or []
    targets = [q for q in fit_results if isinstance(fit_results.get(q), dict)]

    row: dict[str, Any] = {
        "run": run_folder.name, "folder": str(run_folder),
        "family": fam.key if fam else None, "targets": {},
        "parameters": params,
        "stored_figures": sorted(str(p) for p in run_folder.glob("figures.*.png")),
        "refit_figures": [], "errors": [],
    }
    if fam is None:
        row["errors"].append(f"no autofit family for {name!r}")
        return row

    # ---- pre-run state values (patches-first anchors) --------------------
    snap_path = run_folder / "quam_state" / "state.json"
    try:
        snap = json.loads(snap_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        snap = {}
    patched_old = {patch_path_to_dotted(p.get("path", "")): p.get("old")
                   for p in patches}

    def current_value_of(dotted: str):
        node_: Any = snap
        for part in dotted.split("."):
            node_ = node_[part]
        return node_

    def pre_update_value_of(dotted: str):
        if dotted in patched_old:
            return patched_old[dotted]
        return current_value_of(dotted)

    # ---- G-pipeline over the stored claim --------------------------------
    run_obj = {"fit_results": fit_results, "outcomes": outcomes,
               "parameters": params, "folder_path": run_folder}
    verdicts = gates_mod.evaluate_run(run_obj, fam, targets,
                                      current_value_of=current_value_of,
                                      pre_update_value_of=pre_update_value_of)

    # ---- node-faithful refit + replot ------------------------------------
    env = replay_run(run_folder, out_dir, python_path=python_path,
                     source_root=source_root,
                     util=_UTIL_OVERRIDES.get(fam.key))
    row["refit_errors"] = [e.get("stage") for e in env.get("errors") or []]
    row["refit_figures"] = sorted(str(v) for v in (env.get("figures") or {}).values())
    fresh_all = env.get("qubits") or {}

    # ---- per-target codify + sandbox fix ---------------------------------
    sandbox = None
    for q in targets:
        stored = fit_results.get(q) or {}
        fresh = fresh_all.get(q) or {}
        code, why = _codify(stored, fresh, tol=1e6, value_key=fam.value_key)
        v = verdicts[q]
        t_row = {
            "gate_verdict": v.verdict, "gate_mode": v.failure_mode,
            "gate_reasons": v.reasons[:3],
            "stored": {k: stored.get(k) for k in
                       (fam.value_key, "success", "optimal_power") if k in stored},
            "fresh": {k: fresh.get(k) for k in
                      (fam.value_key, "success", "optimal_power") if k in fresh},
            # the FULL scalar fit entry — the apply path maps from this, so
            # coupled keys (target_amplitude / target_full_scale_power_dbm /
            # readout_line, ramsey's decay, …) survive past the compact
            # display dict (no silent partial write; docs/56 §6G)
            "fresh_full": {k: v for k, v in fresh.items()
                           if isinstance(v, (int, float, bool, str))
                           or v is None},
            "refit_code": code, "refit_why": why,
            "fix": None,
        }
        bad = (v.verdict == "fail" or code in ("reject", "drift"))
        q_patches = [p for p in patches
                     if f"/{q}/" in (p.get("path") or "")
                     or (p.get("path") or "").endswith(f"/{q}")]
        if bad and fix != "none":
            if sandbox is None:
                sandbox = out_dir / "sandbox_state"
                if sandbox.exists():
                    shutil.rmtree(sandbox)
                shutil.copytree(run_folder / "quam_state", sandbox)
            t_row["fix"] = _sandbox_fix(sandbox, fam, q, q_patches,
                                        fresh, fix)
        row["targets"][q] = t_row
    if sandbox is not None:
        row["sandbox_state"] = str(sandbox)
    return row


def _sandbox_fix(sandbox: Path, fam, target: str, q_patches: list[dict],
                 fresh: dict, mode: str) -> dict:
    """Apply the offline fix to the SANDBOX copy of the run's state and
    return the before/after diff rows. revert = undo the node's own patches
    (pre-run values); refit = write the node-faithful fresh value (only when
    the fresh analysis succeeded)."""
    state_p = sandbox / "state.json"
    try:
        state = json.loads(state_p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": f"sandbox unreadable: {exc}"}

    def set_dotted(dotted: str, value):
        node = state
        parts = dotted.split(".")
        for part in parts[:-1]:
            node = node[part]
        old = node.get(parts[-1])
        node[parts[-1]] = value
        return old

    changes = []
    try:
        if mode == "revert":
            for p in q_patches:
                if p.get("op", "replace") != "replace" or p.get("old") is None:
                    continue
                dotted = patch_path_to_dotted(p["path"])
                old = set_dotted(dotted, p["old"])
                changes.append({"path": dotted, "before": old,
                                "after": p["old"]})
            note = ("node wrote no state for this target — nothing to revert"
                    if not q_patches else None)
        else:  # refit
            if not fresh.get("success"):
                return {"ok": False,
                        "error": "fresh analysis failed — no refit value to apply"}
            fv = fresh.get(fam.value_key)
            rows = fam_mod.resolve_updates(
                fam, target, {fam.value_key: fv}, {},
                lambda dotted: _get_dotted(state, dotted))
            for r in rows:
                old = set_dotted(r["path"], r["value"])
                changes.append({"path": r["path"], "before": old,
                                "after": r["value"]})
            note = None
        state_p.write_text(json.dumps(state), encoding="utf-8")
    except (KeyError, TypeError, OSError) as exc:
        return {"ok": False, "error": f"fix failed: {exc}"}
    return {"ok": True, "mode": mode, "changes": changes, "note": note}


def _get_dotted(state: dict, dotted: str):
    node: Any = state
    for part in dotted.split("."):
        node = node[part]
    return node


def run_corpus(runs: list[Path], out_root: Path, **kw) -> dict:
    out_root = Path(out_root)
    report = {"rows": [], "summary": {}}
    counts: dict[str, int] = {}
    for i, run in enumerate(runs):
        out_dir = out_root / Path(run).name.replace("#", "n")
        row = evaluate_run(Path(run), out_dir, **kw)
        report["rows"].append(row)
        for t in row.get("targets", {}).values():
            key = f"{t['gate_verdict']}/{t['refit_code']}"
            counts[key] = counts.get(key, 0) + 1
        print(f"[{i + 1}/{len(runs)}] {row['run']}: "
              + ", ".join(f"{q}:{t['gate_verdict']}|{t['refit_code']}"
                          for q, t in row["targets"].items()), flush=True)
    report["summary"] = counts
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "report.json").write_text(
        json.dumps(report, indent=1, default=str), encoding="utf-8")
    return report


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", required=True,
                    help="comma-separated run folders, or a @file with one per line")
    ap.add_argument("--out", required=True)
    ap.add_argument("--python", default=DEFAULT_PYTHON)
    ap.add_argument("--source-root", default=DEFAULT_SOURCE_ROOT)
    ap.add_argument("--fix", default="revert", choices=["revert", "refit", "none"])
    args = ap.parse_args()
    if args.runs.startswith("@"):
        runs = [Path(l.strip()) for l in
                Path(args.runs[1:]).read_text().splitlines() if l.strip()]
    else:
        runs = [Path(r) for r in args.runs.split(",") if r]
    run_corpus(runs, Path(args.out), python_path=args.python,
               source_root=args.source_root, fix=args.fix)
    return 0


if __name__ == "__main__":
    sys.exit(main())
