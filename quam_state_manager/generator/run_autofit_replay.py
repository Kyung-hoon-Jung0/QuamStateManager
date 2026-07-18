"""Autofit offline replay runner — node-faithful re-fit + re-plot (docs/56).

Runs IN the user-selected QM env (never imported by the SM process). For one
saved run folder it:

  1. loads the run's own ``quam_state`` → machine → cube qubits,
  2. replays the family's OWN committed analysis over the frozen ``ds_raw.h5``
     (``process_raw_dataset`` → ``fit_raw_data`` — the docs/50-sanctioned way
     to produce a number offline; never a generic re-fit),
  3. renders the family's OWN figure with the fresh fit overlaid
     (``calibration_utils.<util>.plotting.plot_raw_data_with_fit``) — the
     "corrected fit drawn into the figure" the before/after report shows,
  4. emits a JSON envelope (schema ``afreplay/v1``) with the fresh per-qubit
     values + figure paths.

Reuses the fit-audit runner's shims (same directory): defaults-backed node
parameters, gate hash, JSON sanitizers.

Usage:
    python run_autofit_replay.py --run <folder> --source-root <tree> \
        [--util <module>] [--qubits q1,q2] [--figs-out <dir>] [--out <json>]
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_fit_audit as RFA  # noqa: E402  (shared shims; stdlib-only at import)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--source-root", default="")
    ap.add_argument("--util", default="")
    ap.add_argument("--qubits", default="")
    ap.add_argument("--figs-out", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    if args.source_root:
        sys.path.insert(0, args.source_root)

    node = RFA._read_node(args.run)
    node_name = (node.get("metadata") or {}).get("name") or os.path.basename(args.run)
    util = args.util or RFA._derive_util(node_name)
    params = RFA._deep_find(node, "parameters") or {}
    if not isinstance(params, dict):
        params = {}

    result = {
        "schema": "afreplay/v1", "util": util, "node_name": node_name,
        "run": os.path.basename(args.run), "qubits": {}, "errors": [],
        "figures": {}, "gate_hash": None, "lib_versions": {},
        "preprocessing_ok": False,
    }

    if not RFA._VALID_UTIL.match(util):
        result["errors"].append({"stage": "import",
                                 "trace": f"non-identifier util {util!r}"})
        return RFA._emit(result, args.out)

    try:
        import matplotlib
        matplotlib.use("Agg")
        import xarray as xr
        from quam_config import Quam
        U = importlib.import_module(f"calibration_utils.{util}")
    except Exception:
        result["errors"].append({"stage": "import", "trace": traceback.format_exc()})
        return RFA._emit(result, args.out)

    result["lib_versions"] = RFA._lib_versions()
    try:
        result["gate_hash"], _ = RFA._gate_hash(U)
    except Exception:
        pass

    fit_fn = getattr(U, "fit_raw_data", None)
    process_fn = getattr(U, "process_raw_dataset", None)
    if fit_fn is None:
        result["errors"].append({"stage": "fit_fn",
                                 "trace": f"{util} has no fit_raw_data"})
        return RFA._emit(result, args.out)

    p = os.path.join(args.run, "ds_raw.h5")
    try:
        try:
            ds_raw = xr.open_dataset(p)
        except Exception:
            ds_raw = xr.open_dataset(p, engine="h5netcdf")
    except Exception:
        result["errors"].append({"stage": "datasets", "trace": traceback.format_exc()})
        return RFA._emit(result, args.out)

    try:
        machine = Quam.load(os.path.join(args.run, "quam_state"))
        cube_q = [str(x.decode() if isinstance(x, bytes) else x)
                  for x in ds_raw["qubit"].values] \
            if "qubit" in ds_raw.coords or "qubit" in ds_raw.dims else []
        want = [q for q in args.qubits.split(",") if q]
        if want:
            cube_q = [q for q in cube_q if q in want]
        if not cube_q:
            cube_q = list(machine.qubits.keys())
        sel = [n for n in cube_q if n in machine.qubits]
        qubits = [machine.qubits[n] for n in sel]
        if not qubits:
            raise ValueError(f"no cube qubits {cube_q} found in machine")
        if "qubit" in ds_raw.dims or "qubit" in ds_raw.coords:
            ds_raw = ds_raw.sel(qubit=sel)
    except Exception:
        result["errors"].append({"stage": "qubits", "trace": traceback.format_exc()})
        return RFA._emit(result, args.out)

    node_shim = RFA._Node(qubits, RFA._Params(params, RFA._param_defaults(util)))

    # ---- replay the node's own analysis ----------------------------------
    try:
        ds_proc = ds_raw
        if process_fn is not None:
            ds_proc = process_fn(ds_raw, node_shim)
            result["preprocessing_ok"] = True
        out = fit_fn(ds_proc, node_shim)
        ds_fit, fit_results = (out if isinstance(out, tuple) else (None, out))
    except Exception:
        result["errors"].append({"stage": "fit", "trace": traceback.format_exc()})
        return RFA._emit(result, args.out)

    import dataclasses
    for qn, fp in fit_results.items():
        if dataclasses.is_dataclass(fp):
            result["qubits"][qn] = dataclasses.asdict(fp)
        elif isinstance(fp, dict):
            result["qubits"][qn] = dict(fp)
        else:
            result["qubits"][qn] = {"success": bool(getattr(fp, "success", False))}

    # ---- re-render the family's own figure with the FRESH fit ------------
    if args.figs_out and ds_fit is not None:
        os.makedirs(args.figs_out, exist_ok=True)
        try:
            P = importlib.import_module(f"calibration_utils.{util}.plotting")
        except Exception:
            P = None
            result["errors"].append({"stage": "plot_import",
                                     "trace": traceback.format_exc()})
        if P is not None:
            for fn_name in ("plot_raw_data_with_fit",):
                fn = getattr(P, fn_name, None)
                if fn is None:
                    continue
                try:
                    fig = fn(ds_proc, qubits, ds_fit)
                    fp = os.path.join(args.figs_out, f"refit_{fn_name}.png")
                    fig.savefig(fp, dpi=110, bbox_inches="tight")
                    result["figures"][fn_name] = fp
                except Exception:
                    result["errors"].append({"stage": f"plot:{fn_name}",
                                             "trace": traceback.format_exc()})

    return RFA._emit(result, args.out)


if __name__ == "__main__":
    sys.exit(main())
