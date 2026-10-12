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
import ast
import dataclasses
import inspect
import json
import os
import re
import sys
import traceback
import typing
from pathlib import Path


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


# A calibration_utils submodule is a single Python identifier, never a dotted
# path / traversal. Guards importlib.import_module(f"calibration_utils.{util}").
_VALID_UTIL = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def _resolve_node(node_name, source_root=""):
    """Read plotting imports without executing the acquisition node."""
    import importlib.util
    roots = [Path(source_root).resolve()] if source_root else []
    if not roots:
        spec = importlib.util.find_spec("calibration_utils")
        if spec and spec.submodule_search_locations:
            roots = [Path(p).resolve().parent for p in spec.submodule_search_locations]
    stem = re.sub(r"^[0-9]+[A-Za-z]?Q_", "", node_name)
    for root in roots:
        paths = [root]
        tree_root = root / "calibrations"
        if tree_root.is_dir():
            paths += [tree_root] + sorted(p for p in tree_root.rglob("*") if p.is_dir())
        for p in reversed(paths):
            if str(p) not in sys.path:
                sys.path.insert(0, str(p))
        candidates = sorted(tree_root.rglob(stem + ".py")) if tree_root.is_dir() else []
        if not candidates:
            candidates = sorted(root.glob(stem + ".py"))
        for path in candidates:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            imports = {}
            for item in ast.walk(tree):
                if isinstance(item, ast.ImportFrom) and (item.module or "").startswith("calibration_utils."):
                    for alias in item.names:
                        if alias.name.startswith("plot_"):
                            imports[alias.asname or alias.name] = (item.module, alias.name)
            modules = {module for module, _ in imports.values()}
            if len(modules) == 1:
                module = next(iter(modules))
                return module, tree, imports
            if modules:
                raise ValueError("node imports plotting functions from multiple modules")
    return "calibration_utils." + _derive_util(node_name), None, {}


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


def _input_path(expr):
    """Recognize node input references, never evaluate arbitrary source code."""
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        base = _input_path(expr.value)
        return base + "." + expr.attr if base else None
    if isinstance(expr, ast.Subscript) and isinstance(expr.slice, ast.Constant):
        base = _input_path(expr.value)
        return base + "." + str(expr.slice.value) if base else None
    if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute):
        if expr.func.attr == "get_names":
            return _input_path(expr.func.value) + ".get_names"
        if expr.func.attr == "get" and expr.args and isinstance(expr.args[0], ast.Constant):
            base = _input_path(expr.func.value)
            return base + "." + str(expr.args[0].value) if base else None
    return None


def _call_inputs(fn, tree, imports):
    if tree is None:
        return {}
    names = [n for n, (_, original) in imports.items() if original == fn.__name__]
    parameters = list(inspect.signature(fn).parameters)
    found = {}
    for call in ast.walk(tree):
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id in names:
            for name, expr in zip(parameters, call.args):
                path = _input_path(expr)
                if path:
                    found[name] = path
            for kw in call.keywords:
                path = _input_path(kw.value)
                if kw.arg and path:
                    found[kw.arg] = path
    return found


def _report_value(value):
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if value == value and abs(value) != float("inf") else None
    return {"type": type(value).__name__}


def _restore_value(hint, value, report, path, nonfinite):
    if value is None and hint is float:
        nonfinite.append(path)
        return float("nan")
    if isinstance(value, dict) and dataclasses.is_dataclass(hint):
        return _restore_record(hint, value, report, path)
    origin = typing.get_origin(hint)
    args = typing.get_args(hint)
    if isinstance(value, dict) and origin is dict and len(args) == 2:
        return {k: _restore_value(args[1], v, report, path + "." + str(k), nonfinite)
                for k, v in value.items()}
    if isinstance(value, (list, tuple)) and origin in (list, tuple) and args:
        values = [_restore_value(args[0] if origin is list or args[-1] is Ellipsis else args[i],
                                 v, report, path + "." + str(i), nonfinite)
                  for i, v in enumerate(value)]
        return tuple(values) if origin is tuple else values
    return value


def _restore_record(cls, raw, report, path):
    fields = {f.name: f for f in dataclasses.fields(cls)}
    hints = typing.get_type_hints(cls)
    values = {}
    dropped = sorted(set(raw) - set(fields))
    defaults = {}
    nonfinite = []
    for name, field in fields.items():
        if name not in raw:
            if field.default is not dataclasses.MISSING:
                defaults[name] = _report_value(field.default)
            elif field.default_factory is not dataclasses.MISSING:
                defaults[name] = _report_value(field.default_factory())
            continue
        values[name] = _restore_value(hints.get(name), raw[name], report, path + "." + name, nonfinite)
    report.append({"record": path, "class": cls.__name__, "dropped": dropped, "defaulted": defaults,
                   "restored_nonfinite": nonfinite})
    return cls(**values)


def _fit_records(fn, name, records, report, *, util_module=None, restore_objects=False):
    try:
        hint = typing.get_type_hints(inspect.unwrap(fn)).get(name)
    except (NameError, TypeError):
        hint = None
    cls = typing.get_args(hint)[-1] if typing.get_origin(hint) is dict else None
    if not dataclasses.is_dataclass(cls):
        cls = None
    if cls is None and restore_objects and util_module is not None:
        keys = set().union(*(set(v) for v in records.values() if isinstance(v, dict)))
        candidates = []
        for value in vars(util_module).values():
            if inspect.isclass(value) and dataclasses.is_dataclass(value) and keys:
                known = {f.name for f in dataclasses.fields(value)}
                score = len(keys & known) / len(keys)
                if score >= 0.8:
                    candidates.append((score, value))
        if candidates:
            best = max(score for score, _ in candidates)
            classes = {value for score, value in candidates if score == best}
            if len(classes) == 1:
                cls = classes.pop()
    if cls is not None:
        return {k: _restore_record(cls, v, report, k) for k, v in records.items()}
    return records


_PARAM_ALIASES = {"target_deg": "phase_target_deg", "gate_max_ns": "partner_gate_max_ns",
                  "num_circles": "num_iq_circles_to_plot", "n_circles": "num_iq_circles_to_plot"}
_RESULT_ALIASES = {"fringe_results": "fringe", "fit_objects": "fit_results"}


def _bind_args(fn, *, ds_raw, ds_fit, qubits, params, results=None, machine=None,
               node_tree=None, plot_imports=None, bind_report=None, util_module=None):
    """Bind saved inputs and record every supplied, defaulted or unbound arg."""
    sig = inspect.signature(fn)
    kwargs = {}
    params = params.get("model", params)
    results = results or {}
    decisions = bind_report if bind_report is not None else {}
    inputs = _call_inputs(fn, node_tree, plot_imports or {})
    pair_source = "parameters.qubit_pairs"
    pair_names = params.get("qubit_pairs")
    if not pair_names:
        for dataset in (ds_fit, ds_raw):
            coords = getattr(dataset, "coords", {})
            for coord in ("qubit_pair", "qubit_pairs", "pair", "pair_name"):
                if coord in coords:
                    pair_names = list(coords[coord].values.tolist())
                    pair_source = "dataset.coords." + coord
                    break
            if pair_names:
                break
    if not pair_names and isinstance(results.get("fit_results"), dict):
        pair_names = list(results["fit_results"])
        pair_source = "data.fit_results.keys"
    for name, p in sig.parameters.items():
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            decisions[name] = {"status": "variadic", "source": "signature"}
            continue
        lname = name.lower()
        path = inputs.get(name, "")
        source = None
        value = None
        reconstruction = []
        if lname in ("ds", "dataset", "ds_raw", "raw"):
            value, source = ds_raw, "ds_raw.h5"
        elif lname == "ds_fit" or path == "node.results.ds_fit":
            value, source = ds_fit, "ds_fit.h5"
        elif lname in ("fits", "fit", "fit_results") and "fit_results" in results:
            value = _fit_records(fn, name, results["fit_results"], reconstruction,
                                 util_module=util_module, restore_objects=path == "node.namespace.fit_objects")
            source = "data.fit_results"
        elif lname in ("fits", "fit"):
            value, source = ds_fit, "ds_fit.h5"
        elif lname in ("qubits", "qubit_list"):
            value, source = qubits, "machine.qubits"
        elif lname == "pair_names" and pair_names:
            value, source = list(pair_names), pair_source
        elif lname == "qubit_pairs" and pair_names and machine is not None:
            pairs = getattr(machine, "qubit_pairs", {})
            missing = [n for n in pair_names if n not in pairs]
            if missing:
                decisions[name] = {"status": "unbound", "source": pair_source, "missing": missing}
                raise ValueError(f"saved pairs missing from snapshot: {missing!r}")
            value, source = [pairs[n] for n in pair_names], "machine.qubit_pairs via " + pair_source
        elif path.startswith("node.parameters.") and path.removeprefix("node.parameters.") in params:
            key = path.removeprefix("node.parameters.")
            value, source = params[key], "parameters." + key
        elif lname in params:
            value, source = params[lname], "parameters." + lname
        elif _PARAM_ALIASES.get(lname) in params:
            key = _PARAM_ALIASES[lname]
            value, source = params[key], "parameters." + key
        elif path.startswith("node.results.") and path.removeprefix("node.results.") in results:
            key = path.removeprefix("node.results.")
            value, source = results[key], "data." + key
        elif lname in results:
            value, source = results[lname], "data." + lname
        elif _RESULT_ALIASES.get(lname) in results:
            key = _RESULT_ALIASES[lname]
            value, source = results[key], "data." + key
        elif lname == "gate_max_ns" and "partner_gate_window_ns" in getattr(ds_fit, "attrs", {}):
            value, source = ds_fit.attrs["partner_gate_window_ns"], "ds_fit.attrs.partner_gate_window_ns"
        if source is not None:
            kwargs[name] = value
            decisions[name] = {"status": "bound", "source": source, "value": _report_value(value)}
            if reconstruction:
                decisions[name]["records"] = reconstruction
        elif p.default is not inspect.Parameter.empty:
            decisions[name] = {"status": "defaulted", "source": "signature", "value": _report_value(p.default)}
        else:
            decisions[name] = {"status": "unbound", "source": None}
            raise ValueError(f"unbound required arg {name!r}")
    return kwargs


def _extract_return(value, key, report, extract, not_produced=None):
    """Keep every dictionary member and its binding provenance."""
    figures = value.items() if isinstance(value, dict) else [(None, value)]
    output = []
    for member, fig in figures:
        figure_key = key if member is None else key + "::" + str(member)
        if fig is None:
            if not_produced is not None:
                not_produced.append({"key": figure_key, "reason": "no figure for this run"})
            continue
        extracted = extract(fig, figure_key, title=figure_key.replace("_", " "))
        extracted["bind_report"] = report
        extracted["function_key"] = key
        extracted["return_key"] = member
        output.append(extracted)
    return output


_UNKNOWN = object()


def _saved_condition(expr, ds_fit):
    """Evaluate only saved-mode predicates; unknown source stays unexecuted."""
    if isinstance(expr, ast.Constant):
        return expr.value
    path = _input_path(expr)
    if path and path.startswith("node.results.ds_fit.attrs."):
        return getattr(ds_fit, "attrs", {}).get(path.rsplit(".", 1)[-1])
    if isinstance(expr, ast.Compare) and len(expr.ops) == 1:
        left = _saved_condition(expr.left, ds_fit)
        right = _saved_condition(expr.comparators[0], ds_fit)
        if _input_path(expr.comparators[0]) == "node.results" and left == "ds_fit":
            if isinstance(expr.ops[0], ast.NotIn):
                return ds_fit is None
            if isinstance(expr.ops[0], ast.In):
                return ds_fit is not None
        if left is not _UNKNOWN and right is not _UNKNOWN:
            if isinstance(expr.ops[0], ast.Eq):
                return left == right
            if isinstance(expr.ops[0], ast.NotEq):
                return left != right
    return _UNKNOWN


def _plot_plan(tree, imports, ds_fit):
    """Select the node's plot calls and learn its explicit saved-key aliases."""
    if tree is None or not imports:
        return None, {}
    functions = set()
    aliases = {}
    variables = {}

    def origin(expr):
        if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name) and expr.func.id in imports:
            return {None: imports[expr.func.id][1]}
        if isinstance(expr, ast.Name):
            return variables.get(expr.id, {})
        if isinstance(expr, ast.Dict):
            result = {}
            for key, value in zip(expr.keys, expr.values):
                if isinstance(key, ast.Constant):
                    for fn in origin(value).values():
                        result[str(key.value)] = fn
            return result
        return {}

    def visit(statements):
        for statement in statements:
            if isinstance(statement, ast.If):
                condition = _saved_condition(statement.test, ds_fit)
                if condition is _UNKNOWN:
                    visit(statement.body)
                    visit(statement.orelse)
                elif visit(statement.body if condition else statement.orelse):
                    return True
                continue
            if isinstance(statement, ast.Return):
                return True
            for call in ast.walk(statement):
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id in imports:
                    functions.add(imports[call.func.id][1])
            if isinstance(statement, ast.Assign):
                value = origin(statement.value)
                for target in statement.targets:
                    if isinstance(target, ast.Name):
                        variables[target.id] = value
                        if isinstance(statement.value, ast.Dict):
                            aliases.update({fn: key for key, fn in value.items() if key is not None})
                    elif isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant):
                        key = str(target.slice.value)
                        if _input_path(target) == "node.results.figures":
                            aliases.update({fn: k for k, fn in value.items() if k is not None})
                        elif not key.startswith("figure"):
                            aliases.update({fn: key for fn in value.values()})
        return False

    actions = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name == "plot_data"]
    if not actions:
        return None, {}
    for action in actions:
        visit(action.body)
    return functions, aliases


def _saved_figures(run, results):
    """Census registered figures and standalone PNGs, with no guessed matches."""
    saved = dict(results.get("figures") or {})
    for key, value in results.items():
        if key.startswith("figure_") and isinstance(value, str) and value.lower().endswith(".png"):
            saved[key.removeprefix("figure_")] = value
    for path in sorted(Path(run).glob("figure*.png")):
        if path.name.startswith("figures."):
            key = path.stem.removeprefix("figures.")
        elif path.name.startswith("figure_"):
            key = path.stem.removeprefix("figure_")
        else:
            continue
        saved.setdefault(key, "./" + path.name)
    return saved


def _match_archive(figures, saved, aliases):
    claimed = set()
    for figure in figures:
        source_key = figure["key"]
        member = figure.get("return_key")
        fn = "plot_" + figure["function_key"]
        candidate = str(member) if member is not None else aliases.get(fn, figure["function_key"])
        figure["source_key"] = source_key
        figure["archive_key"] = None
        if candidate in saved and candidate not in claimed:
            claimed.add(candidate)
            figure["key"] = candidate
            figure["archive_key"] = candidate
            figure["archive_path"] = saved[candidate]
    return {"matched": sorted(claimed), "missing": sorted(set(saved) - claimed),
            "extra": [f["key"] for f in figures if f["archive_key"] is None]}


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
    params = params.get("model", params)
    try:
        results = json.loads((Path(args.run) / "data.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        results = {}

    result = {"schema": "iplot/v1", "util": util, "node_name": node_name,
              "figures": [], "errors": [], "bind_report": {}, "not_produced": []}

    if not _VALID_UTIL.match(util):
        result["errors"].append({"stage": "import",
                                 "trace": f"refusing non-identifier util name {util!r}"})
        return _emit(result, args.out)

    try:
        import importlib
        import xarray as xr
        from quam_config import Quam
        module, node_tree, plot_imports = _resolve_node(node_name, args.source_root)
        if args.util:
            module = f"calibration_utils.{util}"
        U = importlib.import_module(module)
        util = module.removeprefix("calibration_utils.")
        result["util"] = util
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
            # Autodetect failed. NetCDF-classic bytes under the .h5 name
            # (xarray's scipy engine wrote them in an env without netCDF4)
            # cannot be read by h5netcdf; pick the fallback by magic.
            eng = "h5netcdf"
            try:
                with open(p, "rb") as _fh:
                    if _fh.read(3) == b"CDF":
                        eng = "scipy"
            except OSError:
                pass
            return xr.open_dataset(p, engine=eng)
    try:
        ds_raw = _open("ds_raw")
        ds_fit = _open("ds_fit")
    except Exception:
        result["errors"].append({"stage": "datasets", "trace": traceback.format_exc()})
        return _emit(result, args.out)

    from iplot_extract import extract_figure

    plot_fns = sorted(n for n in dir(U) if n.startswith("plot_") and callable(getattr(U, n)))
    selected, aliases = _plot_plan(node_tree, plot_imports, ds_fit)
    if selected is not None:
        plot_fns = [n for n in plot_fns if n in selected]
    saved = _saved_figures(args.run, results)
    for fname in plot_fns:
        key = re.sub(r"^plot_", "", fname)
        fn = getattr(U, fname)
        report = result["bind_report"][key] = {}
        try:
            kwargs = _bind_args(fn, ds_raw=ds_raw, ds_fit=ds_fit, qubits=qubits, params=params,
                                results=results, machine=machine, node_tree=node_tree,
                                plot_imports=plot_imports, bind_report=report, util_module=U)
            fig = fn(**kwargs)
            result["figures"].extend(_extract_return(fig, key, report, extract_figure, result["not_produced"]))
            import matplotlib.pyplot as plt
            plt.close("all")
        except Exception:
            result["errors"].append({"stage": f"plot:{key}", "trace": traceback.format_exc()})

    result["archive_match"] = _match_archive(result["figures"], saved, aliases)
    if args.only:
        result["figures"] = [f for f in result["figures"]
                             if args.only in (f["key"], f["source_key"], f["function_key"])]
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
    return 0 if result["figures"] or (result.get("not_produced") and not result["errors"]) else 2


if __name__ == "__main__":
    sys.exit(main())
