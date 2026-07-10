"""Standalone QM-stack subprocess: re-run an experiment's own plotting on saved data.

Invoked by the State-Manager process (``core/interactive_plots/replot.py``) with
the user-selected QM env interpreter — the State-Manager process itself never
imports quam / xarray / the customer calibration code.

Pipeline (the same one the node ran, minus acquisition):
    quam_state/{state,wiring}.json  -> machine -> qubits
    ds_raw.h5 / ds_fit.h5           -> xarray datasets
    calibration_utils.<util>.plot_* -> matplotlib Figures
    iplot_extract.extract_figure    -> structured JSON (iplot/v1)

Generic by design: every ``plot_*`` callable the util package exposes is
discovered and called with args bound *by parameter name* (``ds`` <- ds_raw,
``fits``/``ds_fit`` <- ds_fit, ``qubits`` <- qubits, ``num_circles`` <- node
param, everything else keeps its default). Add a plot function to the util and it
appears here with no code change; change the analysis and the output tracks it.

Usage:
    python run_interactive_replot.py --run <folder> --source-root <dir> \
        [--util <module>] [--qubits q1,q2] [--out <json>] [--only <key>]
Prints a JSON envelope to ``--out`` (or stdout) and a one-line status to stderr.
"""
from __future__ import annotations

import argparse
import inspect
import json
import os
import re
import sys
import traceback


def _eprint(*a):
    print(*a, file=sys.stderr, flush=True)


def _derive_util(node_name: str) -> str:
    """Node name -> ``calibration_utils`` submodule name.

    Strips an optional QUAlibrate graph prefix (``1Q_`` / ``2Q_`` …, case-
    insensitive) and then the numeric node prefix (``05b_`` / ``03_`` …):
        ``05b_resonator_spectroscopy_vs_power_iq`` -> ``resonator_spectroscopy_vs_power_iq``
        ``1Q_03_resonator_spectroscopy``          -> ``resonator_spectroscopy``
        ``2Q_24_Bell_State_Tomography``           -> ``Bell_State_Tomography``
    """
    s = node_name or ""
    s = re.sub(r"^[0-9]+[A-Za-z]?Q_", "", s)   # graph prefix: 1Q_ / 2Q_
    s = re.sub(r"^[0-9]+[a-z]?_", "", s)        # node prefix:  05b_ / 03_
    return s.strip()


# A calibration_utils submodule is a single python identifier — never a dotted
# path / traversal. Guards importlib.import_module(f"calibration_utils.{util}").
_VALID_UTIL = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def _read_node(run: str) -> dict:
    p = os.path.join(run, "node.json")
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _deep_find(o, key):
    if isinstance(o, dict):
        if key in o:
            return o[key]
        for v in o.values():
            r = _deep_find(v, key)
            if r is not None:
                return r
    elif isinstance(o, list):
        for v in o:
            r = _deep_find(v, key)
            if r is not None:
                return r
    return None


def _bind_args(fn, *, ds_raw, ds_fit, qubits, params):
    """Bind a plot function's params by name; return kwargs or raise on a gap."""
    sig = inspect.signature(fn)
    kwargs = {}
    for name, p in sig.parameters.items():
        lname = name.lower()
        if lname in ("ds", "dataset", "ds_raw", "raw"):
            kwargs[name] = ds_raw
        elif lname in ("fits", "fit", "ds_fit"):
            kwargs[name] = ds_fit
        elif lname in ("qubits", "qubit_list"):
            kwargs[name] = qubits
        elif lname in ("num_circles", "num_iq_circles_to_plot", "n_circles"):
            kwargs[name] = int(params.get("num_iq_circles_to_plot", 12))
        elif p.default is not inspect.Parameter.empty:
            pass  # keep the function's own default
        else:
            raise ValueError(f"unbound required arg {name!r}")
    return kwargs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--source-root", default="",
                    help="optional dir holding calibration_utils/ + quam_config/ "
                         "(prepended to sys.path); blank = rely on the env install")
    ap.add_argument("--util", default="", help="util module under calibration_utils (auto if blank)")
    ap.add_argument("--qubits", default="", help="comma list; auto from node.json if blank")
    ap.add_argument("--out", default="")
    ap.add_argument("--only", default="", help="extract just this figure key")
    args = ap.parse_args()

    import matplotlib
    matplotlib.use("Agg")

    if args.source_root:
        sys.path.insert(0, args.source_root)
    # extractor lives next to this script
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    node = _read_node(args.run)
    node_name = (node.get("metadata") or {}).get("name") or os.path.basename(args.run)
    util = args.util or _derive_util(node_name)
    params = _deep_find(node, "parameters") or {}
    if not isinstance(params, dict):
        params = {}

    result = {"schema": "iplot/v1", "util": util, "node_name": node_name,
              "figures": [], "errors": []}

    if not _VALID_UTIL.match(util):
        result["errors"].append({"stage": "import",
                                 "trace": f"refusing non-identifier util name {util!r}"})
        return _emit(result, args.out)

    try:
        import importlib
        import xarray as xr
        from quam_config import Quam
        U = importlib.import_module(f"calibration_utils.{util}")
    except Exception:
        result["errors"].append({"stage": "import", "trace": traceback.format_exc()})
        return _emit(result, args.out)

    # qubits
    try:
        qnames = [q for q in args.qubits.split(",") if q] or params.get("qubits") or []
        machine = Quam.load(os.path.join(args.run, "quam_state"))
        if not qnames:
            qnames = list(machine.qubits.keys())
        qubits = [machine.qubits[n] for n in qnames if n in machine.qubits]
    except Exception:
        result["errors"].append({"stage": "qubits", "trace": traceback.format_exc()})
        return _emit(result, args.out)

    # datasets
    def _open(name):
        p = os.path.join(args.run, name + ".h5")
        if not os.path.exists(p):
            return None
        try:
            return xr.open_dataset(p)
        except Exception:
            return xr.open_dataset(p, engine="h5netcdf")
    try:
        ds_raw = _open("ds_raw")
        ds_fit = _open("ds_fit")
    except Exception:
        result["errors"].append({"stage": "datasets", "trace": traceback.format_exc()})
        return _emit(result, args.out)

    from iplot_extract import extract_figure

    plot_fns = sorted(n for n in dir(U) if n.startswith("plot_") and callable(getattr(U, n)))
    for fname in plot_fns:
        key = re.sub(r"^plot_", "", fname)
        if args.only and key != args.only:
            continue
        fn = getattr(U, fname)
        try:
            kwargs = _bind_args(fn, ds_raw=ds_raw, ds_fit=ds_fit, qubits=qubits, params=params)
            fig = fn(**kwargs)
            if fig is None:
                raise ValueError("plot function returned None")
            result["figures"].append(extract_figure(fig, key, title=key.replace("_", " ")))
            import matplotlib.pyplot as plt
            plt.close("all")
        except Exception:
            result["errors"].append({"stage": f"plot:{key}", "trace": traceback.format_exc()})

    return _emit(result, args.out)


def _json_default(o):
    """Coerce stray numpy scalars/arrays to plain Python; non-finite -> ``None``.

    ``None`` (not ``NaN``) so the payload is strict-JSON valid — ``allow_nan=False``
    below would otherwise raise, and a literal ``NaN`` token breaks ``JSON.parse``
    / Plotly in the browser.
    """
    import numpy as np
    if isinstance(o, np.generic):
        v = o.item()
        if isinstance(v, float) and not np.isfinite(v):
            return None
        return v
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(f"Object of type {o.__class__.__name__} is not JSON serializable")


def _emit(result, out) -> int:
    # allow_nan=False: a stray NaN/Infinity is invalid JSON and breaks the browser
    # parser; the extractor already maps non-finite -> None, this is the backstop.
    payload = json.dumps(result, default=_json_default, allow_nan=False)
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(payload)
        _eprint(f"iplot: {len(result['figures'])} figures, {len(result['errors'])} errors -> {out}")
    else:
        print(payload)
    return 0 if result["figures"] else 2


if __name__ == "__main__":
    sys.exit(main())
