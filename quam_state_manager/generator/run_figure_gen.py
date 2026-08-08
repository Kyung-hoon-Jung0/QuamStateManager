"""Standalone QM-stack subprocess: regenerate a run's figure with the LAB's OWN
plotting module (docs/78 D-11, P0e).

The vision judge must see the figure the physicist sees — the lab's
``calibration_utils/<util>/plotting.py`` output, never an SM re-render (docs/78
§4.2 proved SM's re-render can be transposed). Two modes:

* ``--targets <name>``  → a **per-target single panel**: the dataset AND the
  node-shim namespace are subset to one target before process/fit/plot, so the
  judge can never be handed another qubit's panel (docs/78 D-11.1 / R7). All 9
  family analyses are subset-safe when ds + namespace move in lockstep (recon
  2026-08-06).
* ``--targets ""`` (default) → the **full sheet**, reproducing the archived
  multi-panel figure — the fidelity-check input (docs/78 D-11.2).

``--fit-source fresh`` refits with the tree's analysis (the loop's use);
``stored`` overlays the run's own archived ``ds_fit.h5`` (same-generation
fidelity checks only — field names drift across generations, docs/78 §4.5b).
``--override-fit <json>`` merges ``{target: {var: value}}`` onto the fit
dataset — the docs/78 D-7 wrong-fit manufacturing seam (measures judge
LENIENCY; never used for acceptance scoring).

stdlib-only at import; QM/matplotlib imports inside ``main`` (Agg backend is
forced BEFORE calibration_utils import — its plotting modules import pyplot at
module import time). Emits ONE JSON line (schema ``figgen/v1``) to stdout.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import traceback

# Shared helpers live in the sibling replay script. Spawned by path, the
# generator dir IS sys.path[0] so the flat import wins (and works in a customer
# env that has never heard of quam_state_manager); imported as a package module
# (tests, tooling) the flat name doesn't exist and the package path resolves the
# same module. Both branches are stdlib-only at import.
try:
    import run_fit_audit as RFA
except ImportError:  # pragma: no cover - exercised by the package-import path
    from quam_state_manager.generator import run_fit_audit as RFA


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--source-root", default="")
    ap.add_argument("--util", default="", help="calibration_utils submodule (auto if blank)")
    ap.add_argument("--plot-fn", required=True,
                    help="plotting function name, e.g. plot_raw_data_with_fit")
    ap.add_argument("--figure-key", default="amplitude",
                    help="output name stem (matches the node's figures key)")
    ap.add_argument("--arg2", default="qubits", choices=("qubits", "pairs"),
                    help="second plot-fn argument: measured qubits or pair objects")
    ap.add_argument("--targets", default="",
                    help="comma list of targets (qubit or pair names) — one "
                         "single-panel figure each; blank = one full sheet")
    ap.add_argument("--fit-source", default="fresh", choices=("fresh", "stored"))
    ap.add_argument("--override-fit", default="",
                    help="JSON file {target: {var: value}} merged onto the fit "
                         "dataset (wrong-fit manufacturing, docs/78 D-7)")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--dpi", type=int, default=100)
    ap.add_argument("--out", default="", help="write the JSON envelope here (else stdout)")
    args = ap.parse_args()

    if args.source_root:
        sys.path.insert(0, args.source_root)

    node = RFA._read_node(args.run)
    node_name = (node.get("metadata") or {}).get("name") \
        or RFA._name_from_folder(os.path.basename(args.run))
    util = args.util or RFA._derive_util(node_name)
    params = RFA.run_params(node)

    result = {"schema": "figgen/v1", "util": util, "node_name": node_name,
              "run": os.path.basename(args.run), "figures": [], "errors": [],
              "fit_source": args.fit_source, "gate_hash": None,
              "lib_versions": {}, "preprocessing_ok": False}

    if not RFA._VALID_UTIL.match(util):
        result["errors"].append({"stage": "import", "trace": f"non-identifier util {util!r}"})
        return _emit(result, args.out)

    try:
        import matplotlib
        matplotlib.use("Agg")           # BEFORE calibration_utils (imports pyplot)
        import matplotlib.pyplot as plt
        import xarray as xr
        from quam_config import Quam
        U = importlib.import_module(f"calibration_utils.{util}")
    except Exception:  # noqa: BLE001
        result["errors"].append({"stage": "import", "trace": traceback.format_exc()})
        return _emit(result, args.out)

    result["lib_versions"] = RFA._lib_versions()
    try:
        result["gate_hash"], _ = RFA._gate_hash(U)
    except Exception:  # noqa: BLE001
        pass

    plot_fn = getattr(U, args.plot_fn, None)
    if plot_fn is None:
        try:
            P = importlib.import_module(f"calibration_utils.{util}.plotting")
            plot_fn = getattr(P, args.plot_fn, None)
        except Exception:  # noqa: BLE001
            plot_fn = None
    if plot_fn is None:
        result["errors"].append({"stage": "plot_fn",
                                 "trace": f"{util} has no {args.plot_fn}"})
        return _emit(result, args.out)
    fit_fn = getattr(U, "fit_raw_data", None)
    process_fn = getattr(U, "process_raw_dataset", None)

    p_raw = os.path.join(args.run, "ds_raw.h5")
    if not os.path.exists(p_raw):
        result["errors"].append({"stage": "datasets", "trace": "no ds_raw.h5"})
        return _emit(result, args.out)

    try:
        machine = Quam.load(os.path.join(args.run, "quam_state"))
        ds_full = RFA._open_ds(xr, p_raw)
        cube_q = [str(x.decode() if isinstance(x, bytes) else x)
                  for x in ds_full["qubit"].values] \
            if "qubit" in ds_full.coords or "qubit" in ds_full.dims else []
        pair_names, measured, sel = RFA._derive_pairs(machine, cube_q, params, [])
        if pair_names is None:
            sel = [n for n in cube_q if n in machine.qubits]
        if not sel:
            raise ValueError(
                f"no usable targets: cube {cube_q} matches neither the "
                f"machine's qubits nor a recorded pair list")
    except Exception:  # noqa: BLE001
        result["errors"].append({"stage": "qubits", "trace": traceback.format_exc()})
        return _emit(result, args.out)

    overrides = {}
    if args.override_fit:
        try:
            with open(args.override_fit, "r", encoding="utf-8") as fh:
                overrides = json.load(fh)
        except (OSError, ValueError):
            result["errors"].append({"stage": "override",
                                     "trace": "unreadable --override-fit JSON"})
            return _emit(result, args.out)

    os.makedirs(args.out_dir, exist_ok=True)
    want = [t for t in args.targets.split(",") if t]
    jobs = [[t] for t in want] if want else [None]     # None = the full sheet

    defaults = RFA._param_defaults(util)
    for job in jobs:
        label = job[0] if job else "all"
        try:
            fig_path = _one_figure(
                args, RFA, xr, plt, machine, ds_full, plot_fn, fit_fn,
                process_fn, params, defaults, pair_names, measured, sel,
                job, overrides, result)
            result["figures"].append({"target": label, "path": fig_path})
        except Exception:  # noqa: BLE001 — one bad target must not kill the rest
            result["errors"].append({"stage": f"figure:{label}",
                                     "trace": traceback.format_exc()[-1500:]})
    return _emit(result, args.out)


def _aliases(pair_name, cube_name, measured_qubit) -> set:
    """Every name a caller may legitimately use for one pair-shaped slot."""
    out = {pair_name, cube_name}
    n = getattr(measured_qubit, "name", None)
    if n:
        out.add(n)
    return {x for x in out if x}


def _relabel_qubit(ds, from_names, to_names):
    """Rewrite a dataset's ``qubit`` coord from one vocabulary to another.

    No-op when the coord is already in the target vocabulary (or absent), so a
    newer archive that already stores pair names passes through untouched.
    """
    coord = getattr(ds, "coords", {})
    if "qubit" not in coord:
        return ds
    have = [str(x.decode() if isinstance(x, bytes) else x)
            for x in ds["qubit"].values]
    mapping = dict(zip(from_names, to_names))
    if not any(h in mapping for h in have):
        return ds
    return ds.assign_coords(qubit=[mapping.get(h, h) for h in have])


def _one_figure(args, RFA, xr, plt, machine, ds_full, plot_fn, fit_fn,
                process_fn, params, defaults, pair_names, measured, sel,
                job, overrides, result):
    """Build ONE figure (single target or the full sheet) and save it."""
    if job is None:
        t_sel, t_pairs, t_measured = sel, pair_names, measured
    elif pair_names is not None:
        # A pair-shaped run carries THREE names per slot and the families
        # disagree about which one they report: the coupler resonator node
        # keys its fit_results by PAIR name, the coupler qubit-spec node by the
        # MEASURED QUBIT name, and the cube coord may be either. Accept any of
        # them (docs/78 D-14: resolve from the run, never assume one vocabulary)
        # so a caller that passes a fit_results key always gets the right panel.
        idx = [i for i in range(len(pair_names))
               if _aliases(pair_names[i], sel[i], measured[i]) & set(job)]
        if not idx:
            known = [sorted(_aliases(pair_names[i], sel[i], measured[i]))
                     for i in range(len(pair_names))]
            raise ValueError(f"target {job} not among run slots {known}")
        t_pairs = [pair_names[i] for i in idx]
        t_measured = [measured[i] for i in idx]
        t_sel = [sel[i] for i in idx]
    else:
        t_sel = [n for n in sel if n in job]
        if not t_sel:
            raise ValueError(f"target {job} not among run qubits {sel}")
        t_pairs, t_measured = None, None

    ds = ds_full.sel(qubit=t_sel) \
        if ("qubit" in ds_full.dims or "qubit" in ds_full.coords) else ds_full
    if t_pairs is not None:
        qubits = t_measured
    else:
        qubits = [machine.qubits[n] for n in t_sel]
    shim = RFA._Node(qubits, RFA._Params(params, defaults), t_pairs)

    if process_fn is not None:
        ds = process_fn(ds, shim)
        result["preprocessing_ok"] = True

    if args.fit_source == "stored":
        p_fit = os.path.join(args.run, "ds_fit.h5")
        if not os.path.exists(p_fit):
            raise FileNotFoundError("no ds_fit.h5 for --fit-source stored")
        fits = RFA._open_ds(xr, p_fit)
        names = t_pairs if t_pairs is not None else t_sel
        if "qubit" in fits.dims or "qubit" in fits.coords:
            have = {str(x.decode() if isinstance(x, bytes) else x)
                    for x in fits["qubit"].values}
            fits = fits.sel(qubit=[n for n in names if n in have])
    else:
        if fit_fn is None:
            raise RuntimeError("util has no fit_raw_data for --fit-source fresh")
        out = fit_fn(ds, shim)
        fits = out[0] if isinstance(out, tuple) else out

    # Wrong-fit injection (docs/78 D-7). Two rules, both load-bearing for the
    # leniency measurement to MEAN anything:
    #  * only entries addressing THIS panel are applied — `fits` is already
    #    subset to this job, so a sibling target's entry would raise and lose
    #    every figure of a multi-target manufacture run;
    #  * an entry that cannot be applied RAISES. A silently skipped override
    #    (misspelled variable, cross-generation rename) would hand the harness a
    #    pristine figure labelled "manufactured wrong fit", and the judge would
    #    be scored for accepting a fit that was never corrupted.
    names_here = set(t_pairs or []) | set(t_sel or [])
    if t_measured:
        names_here |= {getattr(q, "name", None) for q in t_measured} - {None}
    for tgt, patch in (overrides or {}).items():
        if tgt not in names_here:
            continue
        if not isinstance(patch, dict):
            raise ValueError(f"override for {tgt!r} is not a mapping")
        for var, value in patch.items():
            if var not in getattr(fits, "variables", {}):
                raise ValueError(
                    f"override {tgt}.{var}: no such variable in the fit dataset "
                    f"(have: {sorted(getattr(fits, 'variables', {}))[:12]})")
            try:
                # the fit dataset is keyed by whichever vocabulary this family
                # reports; address the slot by the label it actually carries
                key = tgt
                if "qubit" in getattr(fits, "coords", {}):
                    have = {str(x.decode() if isinstance(x, bytes) else x): x
                            for x in fits["qubit"].values}
                    if tgt not in have and t_pairs:
                        for cand in (t_pairs[0], (t_sel or [None])[0]):
                            if cand in have:
                                key = cand
                                break
                    key = have.get(key, key)
                fits[var].loc[{"qubit": key}] = value
            except Exception as e:  # noqa: BLE001 — refuse silently-wrong figures
                raise ValueError(f"override {tgt}.{var} failed: {e}") from e

    arg2 = qubits
    if args.arg2 == "pairs" and t_pairs is not None:
        arg2 = [machine.qubit_pairs[p] for p in t_pairs]
        # A pair-grid plotter indexes the data by PAIR name (newer nodes rename
        # qubit_pair->qubit at acquisition). Older archives of the same node
        # stored the MEASURED QUBIT name instead, so the plotter's .sel would
        # raise "not all values found in index 'qubit'". Relabel here — the row
        # IS that pair's measurement — rather than skipping the family.
        ds, fits = (_relabel_qubit(d, t_sel, t_pairs) for d in (ds, fits))
    fig = plot_fn(ds, arg2, fits)
    label = job[0] if job else "all"
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in label)
    # `fit_source` is in the name: a fidelity check regenerates the SAME run
    # both ways and a shared name would silently overwrite one with the other.
    fig_path = os.path.join(args.out_dir,
                            f"{args.figure_key}__{safe}__{args.fit_source}.png")
    fig.savefig(fig_path, dpi=args.dpi, bbox_inches="tight")
    plt.close(fig)
    return fig_path


def _emit(result, out) -> int:
    payload = json.dumps(RFA._sanitize(result), default=RFA._json_default,
                         allow_nan=False)
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(payload)
        RFA._eprint(f"figgen: {len(result['figures'])} figures, "
                    f"{len(result['errors'])} errors -> {out}")
    else:
        print(payload)
    return 0 if result["figures"] and not result["errors"] else 2


if __name__ == "__main__":
    sys.exit(main())
