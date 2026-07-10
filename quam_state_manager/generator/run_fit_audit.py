"""Standalone QM-stack subprocess: replay a node's OWN committed analysis over a
saved data cube and report the fresh verdict — the oracle for the Fit-Auditor
(gate-migration triage, docs/50).

Invoked by the State-Manager process (``core/fit_audit.py``) with the user-selected
QM env interpreter — the SM process itself never imports quam / xarray / the
customer calibration code.

Pipeline (the node's own, minus acquisition):
    quam_state/{state,wiring}.json   -> machine -> qubits
    ds_raw.h5                        -> xarray dataset
    calibration_utils.<util>.process_raw_dataset(ds, node)   (REAL preprocessing)
    calibration_utils.<util>.fit_raw_data(ds, node)          -> {qubit: FitParameters}

The node's real ``process_raw_dataset`` is used verbatim (never a hand-rolled
re-preprocessing) so the fitter sees the exact conditioned channel it was written
for — this retires the "preprocessing drift" risk (docs/50 R2). If the node exposes
no ``process_raw_dataset`` the cube is passed through and ``preprocessing_ok=False``
so the driver can mark those qubits ``? unverifiable``.

Old backlog ``node.json`` files carry only the run's own (pre-hardening) params and
NONE of the current gate fields, so ``node.parameters`` falls back to the family's
own ``parameters.py`` model-field defaults — i.e. we replay TODAY's gate over the
frozen data, which is the whole point of gate-migration triage.

Every verdict is stamped with a ``gate_hash`` (sha256 over the util package's .py
sources — where the gate actually lives; ``library_versions`` does not cover it) and
``lib_versions``. ``fit_raw_data`` is run twice; per-qubit ``deterministic`` flags
lmfit non-determinism (docs/50 R5) so the driver can abstain.

Usage:
    python run_fit_audit.py --run <folder> --source-root <dir> \
        [--util <module>] [--qubits q1,q2] [--out <json>]
Emits a JSON envelope (schema ``fitaudit/v1``) to ``--out`` (or stdout); one-line
status to stderr.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import importlib
import json
import os
import re
import sys
import traceback


def _eprint(*a):
    print(*a, file=sys.stderr, flush=True)


def _derive_util(node_name: str) -> str:
    """Node name -> ``calibration_utils`` submodule (strip graph + node prefixes)."""
    s = node_name or ""
    s = re.sub(r"^[0-9]+[A-Za-z]?Q_", "", s)   # graph prefix: 1Q_ / 2Q_
    s = re.sub(r"^[0-9]+[a-z]?_", "", s)        # node prefix:  05b_ / 03_
    return s.strip()


# A calibration_utils submodule is a single python identifier — never a dotted path.
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


def _gate_hash(util_module):
    """sha256 over every .py in the util package dir (name-sorted, NUL-delimited)."""
    pkg_init = getattr(util_module, "__file__", None)
    if not pkg_init:
        return None, []
    pkgdir = os.path.dirname(pkg_init)
    files = sorted(f for f in os.listdir(pkgdir) if f.endswith(".py"))
    h = hashlib.sha256()
    for f in files:
        try:
            with open(os.path.join(pkgdir, f), "rb") as fh:
                h.update(f.encode("utf-8"))
                h.update(b"\0")
                h.update(fh.read())
                h.update(b"\0")
        except OSError:
            continue
    return h.hexdigest(), files


def _lib_versions():
    out = {}
    for m in ("quam", "qm", "qualibration_libs", "qualang_tools", "numpy", "scipy", "xarray"):
        try:
            out[m] = getattr(importlib.import_module(m), "__version__", None)
        except Exception:
            out[m] = None
    return out


def _param_defaults(util: str) -> dict:
    """Field defaults from the family's ``parameters.Parameters`` (pydantic v2).

    These fill the current-gate params that old backlog ``node.json`` lacks.
    """
    out = {}
    try:
        P = importlib.import_module(f"calibration_utils.{util}.parameters")
    except Exception:
        return out
    cls = getattr(P, "Parameters", None)
    mf = getattr(cls, "model_fields", None) if cls is not None else None
    if not mf:
        return out
    for name, fld in mf.items():
        try:
            if getattr(fld, "default_factory", None) is not None:
                out[name] = fld.default_factory()
            elif not fld.is_required():
                out[name] = fld.default
        except Exception:
            continue
    return out


class _Params:
    """node.parameters: run's own values first, then current-gate defaults."""

    def __init__(self, over, defaults):
        object.__setattr__(self, "_over", dict(over or {}))
        object.__setattr__(self, "_defaults", dict(defaults or {}))

    def __getattr__(self, name):
        o = object.__getattribute__(self, "_over")
        if name in o:
            return o[name]
        d = object.__getattribute__(self, "_defaults")
        if name in d:
            return d[name]
        raise AttributeError(name)


class _Node:
    """Minimal QualibrationNode shim: what fit_raw_data / process_raw_dataset read."""

    def __init__(self, qubits, params):
        self.namespace = {"qubits": qubits}
        self.parameters = params


def _fit_once(fit_fn, process_fn, ds_raw, node, result):
    """Run (process ->) fit; return {qubit: dataclass-dict} or None on failure."""
    ds = ds_raw
    if process_fn is not None:
        ds = process_fn(ds_raw, node)
        result["preprocessing_ok"] = True
    out = fit_fn(ds, node)
    # fit_raw_data returns (ds_fit, fit_results); tolerate a bare dict too.
    fit_results = out[1] if isinstance(out, tuple) else out
    packed = {}
    for qn, fp in fit_results.items():
        if dataclasses.is_dataclass(fp):
            packed[qn] = dataclasses.asdict(fp)
        elif isinstance(fp, dict):
            packed[qn] = dict(fp)
        else:
            packed[qn] = {"success": bool(getattr(fp, "success", False))}
    return packed


def _deterministic(a: dict, b: dict) -> bool:
    """Same success + all shared finite floats within 1 Hz/1e-9 between two runs."""
    if bool(a.get("success")) != bool(b.get("success")):
        return False
    for k, va in a.items():
        vb = b.get(k)
        if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
            if va != va or vb != vb:   # NaN on one side only
                if (va != va) != (vb != vb):
                    return False
                continue
            tol = 1.0 if abs(va) > 1e3 else 1e-9
            if abs(va - vb) > tol:
                return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--source-root", default="",
                    help="dir holding calibration_utils/ + quam_config/ (prepended to sys.path)")
    ap.add_argument("--util", default="", help="util under calibration_utils (auto if blank)")
    ap.add_argument("--qubits", default="", help="comma list; auto from machine if blank")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    if args.source_root:
        sys.path.insert(0, args.source_root)

    node = _read_node(args.run)
    node_name = (node.get("metadata") or {}).get("name") or os.path.basename(args.run)
    util = args.util or _derive_util(node_name)
    params = _deep_find(node, "parameters") or {}
    if not isinstance(params, dict):
        params = {}

    result = {
        "schema": "fitaudit/v1", "util": util, "node_name": node_name,
        "run": os.path.basename(args.run), "qubits": {}, "errors": [],
        "gate_hash": None, "gate_files": [], "lib_versions": {},
        "preprocessing_ok": False, "has_process_fn": False,
    }

    if not _VALID_UTIL.match(util):
        result["errors"].append({"stage": "import", "trace": f"non-identifier util {util!r}"})
        return _emit(result, args.out)

    try:
        import xarray as xr
        from quam_config import Quam
        U = importlib.import_module(f"calibration_utils.{util}")
    except Exception:
        result["errors"].append({"stage": "import", "trace": traceback.format_exc()})
        return _emit(result, args.out)

    result["lib_versions"] = _lib_versions()
    try:
        result["gate_hash"], result["gate_files"] = _gate_hash(U)
    except Exception:
        result["errors"].append({"stage": "gate_hash", "trace": traceback.format_exc()})

    fit_fn = getattr(U, "fit_raw_data", None)
    if fit_fn is None:
        result["errors"].append({"stage": "fit_fn", "trace": f"{util} has no fit_raw_data"})
        return _emit(result, args.out)
    process_fn = getattr(U, "process_raw_dataset", None)
    result["has_process_fn"] = process_fn is not None

    p = os.path.join(args.run, "ds_raw.h5")
    if not os.path.exists(p):
        result["errors"].append({"stage": "datasets", "trace": "no ds_raw.h5"})
        return _emit(result, args.out)
    try:
        try:
            ds_raw = xr.open_dataset(p)
        except Exception:
            ds_raw = xr.open_dataset(p, engine="h5netcdf")
    except Exception:
        result["errors"].append({"stage": "datasets", "trace": traceback.format_exc()})
        return _emit(result, args.out)

    try:
        machine = Quam.load(os.path.join(args.run, "quam_state"))
        # The fit runs over EXACTLY the cube's qubits, in the cube's order —
        # process_raw_dataset assigns a (qubit, detuning) coord that must align
        # with ds_raw's qubit dim (passing all machine qubits mis-sizes it).
        cube_q = [str(x.decode() if isinstance(x, bytes) else x)
                  for x in ds_raw["qubit"].values] if "qubit" in ds_raw.coords or "qubit" in ds_raw.dims else []
        want = [q for q in args.qubits.split(",") if q]
        if want:
            cube_q = [q for q in cube_q if q in want]
        if not cube_q:
            cube_q = list(machine.qubits.keys())
        sel = [n for n in cube_q if n in machine.qubits]
        qubits = [machine.qubits[n] for n in sel]
        if not qubits:
            raise ValueError(f"no cube qubits {cube_q} found in machine")
        # Keep ds_raw's qubit axis EXACTLY aligned with `qubits` (membership + order)
        # so a --qubits subset can't mis-size process_raw_dataset's (qubit, detuning)
        # coord assign. No-op when auditing the whole cube.
        if "qubit" in ds_raw.dims or "qubit" in ds_raw.coords:
            ds_raw = ds_raw.sel(qubit=sel)
    except Exception:
        result["errors"].append({"stage": "qubits", "trace": traceback.format_exc()})
        return _emit(result, args.out)

    node_shim = _Node(qubits, _Params(params, _param_defaults(util)))

    try:
        first = _fit_once(fit_fn, process_fn, ds_raw, node_shim, result)
    except Exception:
        result["errors"].append({"stage": "fit", "trace": traceback.format_exc()})
        return _emit(result, args.out)
    try:
        second = _fit_once(fit_fn, process_fn, ds_raw, node_shim, result)
    except Exception:
        second = {}   # determinism unknown -> mark all non-deterministic below

    for qn, d in first.items():
        d["deterministic"] = bool(second and qn in second and _deterministic(d, second[qn]))
        result["qubits"][qn] = d

    return _emit(result, args.out)


def _json_default(o):
    import numpy as np
    if isinstance(o, np.generic):
        v = o.item()
        if isinstance(v, float) and not np.isfinite(v):
            return None
        return v
    if isinstance(o, np.ndarray):
        # scrub non-finite floats the list may (re)introduce, so the outer
        # json.dumps(allow_nan=False) can't raise mid-encode on a NaN cell.
        return _sanitize(o.tolist())
    if isinstance(o, (bytes, bytearray)):
        return o.decode("utf-8", "replace")
    raise TypeError(f"Object of type {o.__class__.__name__} is not JSON serializable")


def _sanitize(o):
    """Recursively map non-finite floats -> None so allow_nan=False can't reject
    a native ``float('nan')`` (fit fields are ``float(...)``-cast, not numpy)."""
    import math
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _sanitize(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_sanitize(v) for v in o]
    return o


def _emit(result, out) -> int:
    payload = json.dumps(_sanitize(result), default=_json_default, allow_nan=False)
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(payload)
        _eprint(f"fitaudit: {len(result['qubits'])} qubits, {len(result['errors'])} errors -> {out}")
    else:
        print(payload)
    return 0 if result["qubits"] else 2


if __name__ == "__main__":
    sys.exit(main())
