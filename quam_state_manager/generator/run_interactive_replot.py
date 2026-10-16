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
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace


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


def _plot_util(imports):
    """Unify paths only with evidence that they belong to one util package."""
    import importlib
    modules = {module for module, _ in imports.values()}
    roots = {".".join(module.split(".")[:2]) for module in modules}
    if len(roots) != 1:
        raise ValueError("node imports plotting functions from different util packages: " + ", ".join(sorted(roots)))
    root = roots.pop()
    package = importlib.import_module(root)
    if not hasattr(package, "__path__"):
        raise ValueError(f"plotting import paths do not share a util package: {root}")
    functions = {}
    for module, name in imports.values():
        fn = getattr(importlib.import_module(module), name)
        owner = getattr(fn, "__module__", "")
        if not callable(fn) or not (getattr(package, name, None) is fn or owner == root or owner.startswith(root + ".")):
            raise ValueError(f"plotting function {module}.{name} has no ownership evidence for {root} (owner {owner!r})")
        if name in functions and functions[name] is not fn:
            raise ValueError(f"conflicting plotting functions named {name} in {root}")
        functions[name] = fn
    return root


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
                return _plot_util(imports), tree, imports
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
    if isinstance(expr, ast.Subscript):
        base = _input_path(expr.value)
        if isinstance(expr.slice, ast.Constant):
            return base + "." + str(expr.slice.value) if base else None
        selector = _input_path(expr.slice)
        return base + ".[" + (selector or "unknown") + "]" if base else None
    if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute):
        if expr.func.attr == "get_names":
            return _input_path(expr.func.value) + ".get_names"
        if expr.func.attr == "get" and expr.args and isinstance(expr.args[0], ast.Constant):
            base = _input_path(expr.func.value)
            return base + "." + str(expr.args[0].value) if base else None
    return None


def _call_inputs(fn, tree, imports, node_call=None):
    if tree is None:
        return {}
    names = [n for n, (_, original) in imports.items() if original == fn.__name__]
    parameters = list(inspect.signature(fn).parameters)
    found = {}
    owners = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}

    def resolve(expr, call, seen=()):
        if isinstance(expr, ast.Name) and expr.id not in seen:
            scope = call
            while scope in owners and not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
                scope = owners[scope]
            assignments = []
            for item in ast.walk(scope):
                if isinstance(item, ast.Assign) and item.lineno < call.lineno:
                    for target in item.targets:
                        if isinstance(target, ast.Name) and target.id == expr.id:
                            assignments.append((item.lineno, item.value))
                        elif isinstance(target, (ast.Tuple, ast.List)) and isinstance(item.value, (ast.Tuple, ast.List)):
                            for name, value in zip(target.elts, item.value.elts):
                                if isinstance(name, ast.Name) and name.id == expr.id:
                                    assignments.append((item.lineno, value))
            if assignments:
                value = max(assignments, key=lambda item: item[0])[1]
                return resolve(value, call, (*seen, expr.id)) or expr.id
        return _input_path(expr)

    for call in ast.walk(tree):
        if node_call is not None and call is not node_call:
            continue
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id in names:
            for name, expr in zip(parameters, call.args):
                path = resolve(expr, call)
                if path:
                    found[name] = path
            for kw in call.keywords:
                path = resolve(kw.value, call)
                if kw.arg and path:
                    found[kw.arg] = path
    return found


def _call_literals(fn, call):
    """Literal call arguments are saved source evidence, never evaluated code."""
    if call is None:
        return {}
    expressions = dict(zip(inspect.signature(fn).parameters, call.args))
    expressions.update({kw.arg: kw.value for kw in call.keywords if kw.arg})
    values = {}
    for name, expression in expressions.items():
        try:
            values[name] = ast.literal_eval(expression)
        except (ValueError, TypeError, SyntaxError):
            pass
    return values


def _report_value(value):
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if value == value and abs(value) != float("inf") else None
    return {"type": type(value).__name__}


class SavedInputMissing(AttributeError):
    """A plot read an attribute the saved (data.json) record does not have: the node built
    that input in its analysis and saved only a summary of it."""

    def __init__(self, path, name):
        super().__init__(f"{path} has no {name!r}")
        self.path, self.name = path, name


class _SavedNamespace(dict):
    """Saved namespace records retain mapping access and expose saved attributes.

    Nested records (dicts inside dicts, lists or tuples) are wrapped too: a lab plot reads
    ``plan.control`` of every saved plan, not only attributes of the top-level mapping."""

    def __init__(self, value=(), path="saved"):
        super().__init__()
        self._saved_path = path
        for key, item in dict(value).items():
            self[key] = _saved_view(item, f"{path}[{key!r}]")

    def __getattr__(self, name):
        if name.startswith("__") or name == "_saved_path":
            raise AttributeError(name)
        try:
            return self[name]
        except KeyError:
            raise SavedInputMissing(self._saved_path, name) from None


def _saved_view(value, path):
    """Saved JSON as the attribute-readable records a lab plot expects, at every depth."""
    if isinstance(value, dict):
        return _SavedNamespace(value, path)
    if isinstance(value, (list, tuple)):
        items = [_saved_view(item, f"{path}[{i}]") for i, item in enumerate(value)]
        return tuple(items) if isinstance(value, tuple) else items
    return value


def _plot_error_trace(error, report):
    """The traceback of a failed plot (call inside ``except``). The tile shows its last line,
    so a plot a saved summary could not feed ends on the missing input, not the Python error."""
    trace = traceback.format_exc()
    reason = _saved_input_reason(error, report)
    return trace.rstrip() + "\n" + reason if reason else trace


def _saved_input_reason(error, report):
    """The tile reason for a plot that a saved summary could not feed, or None.

    A plot bound to a saved stand-in for ``node.namespace[...]`` (data.json keeps a summary of
    what the node's analysis built) names that input instead of a Python error."""
    missing = error if isinstance(error, SavedInputMissing) else None
    cause = error.__cause__ or error.__context__
    while missing is None and cause is not None:
        missing = cause if isinstance(cause, SavedInputMissing) else None
        cause = cause.__cause__ or cause.__context__
    inputs = [(name, d["source"].split(" via ", 1)[1]) for name, d in (report or {}).items()
              if isinstance(d, dict) and " via node.namespace." in str(d.get("source", ""))]
    if missing is None or not inputs:
        return None
    root = missing.path.split("[", 1)[0].removeprefix("data.")
    name, path = next(((n, p) for n, p in inputs if p == "node.namespace." + root), inputs[0])
    key = path.removeprefix("node.namespace.")
    return (f"missing input {name!r}: the plot needs node.namespace[{key!r}] as the node's analysis built it; "
            f"data.json saves only a summary (no {missing.path.removeprefix('data.')}.{missing.name})")


def _mapping_hint(hint):
    origin = typing.get_origin(hint) or hint
    return isinstance(origin, type) and issubclass(origin, Mapping)


def _restore_value(hint, value, report, path, nonfinite):
    if value is None and hint is float:
        nonfinite.append(path)
        return float("nan")
    if isinstance(value, dict) and dataclasses.is_dataclass(hint):
        return _restore_record(hint, value, report, path)
    origin = typing.get_origin(hint)
    args = typing.get_args(hint)
    if isinstance(value, dict) and _mapping_hint(hint) and len(args) == 2:
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
    try:
        hints = typing.get_type_hints(cls)
    except (NameError, TypeError) as error:
        report.append({"record": path, "class": cls.__name__,
                       "reason": f"unresolved record annotation: {type(error).__name__}: {error}"})
        return raw
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


def _fit_records(fn, name, records, report, *, util_module=None, restore_objects=False, input_path=""):
    unresolved = False
    try:
        hint = typing.get_type_hints(inspect.unwrap(fn)).get(name)
    except (NameError, TypeError) as error:
        hint = None
        unresolved = True
        report.append({"annotation": name,
                       "reason": f"unresolved fit annotation: {type(error).__name__}: {error}"})
    mapping_hint = _mapping_hint(hint)
    bases = ("node.results.fit_results", "node.namespace.fit_results", "node.namespace.fit_objects")
    whole_mapping = input_path in bases
    selected_path = next((input_path[len(base) + 1:] for base in bases
                          if input_path.startswith(base + ".")), None)
    single = not mapping_hint and not whole_mapping and (dataclasses.is_dataclass(hint) or selected_path is not None)
    if single and isinstance(records, dict):
        key = selected_path if selected_path in records else next(iter(records)) if len(records) == 1 else None
        if key is not None:
            report.append({"selection": key, "reason": "single element requested by hint or node call"})
            value = records[key]
            return _restore_record(hint, value, report, key) if dataclasses.is_dataclass(hint) else value
        report.append({"selection": "unchanged", "reason": "multiple fit entries; element key is unknown"})
        return records
    args = typing.get_args(hint)
    cls = args[-1] if mapping_hint and args else None
    if not dataclasses.is_dataclass(cls):
        cls = None
    if cls is None and not unresolved and restore_objects and util_module is not None:
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
               node_tree=None, plot_imports=None, bind_report=None, util_module=None, saved_datasets=None,
               node_call=None):
    """Bind saved inputs and record every supplied, defaulted or unbound arg."""
    sig = inspect.signature(fn)
    kwargs = {}
    params = params.get("model", params)
    results = results or {}
    decisions = bind_report if bind_report is not None else {}
    inputs = _call_inputs(fn, node_tree, plot_imports or {}, node_call)
    literals = _call_literals(fn, node_call)
    missing = []
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
        saved_key = path.removeprefix("node.results.") if path.startswith("node.results.") else ""
        saved_file = results.get(saved_key)
        dataset_reference = isinstance(saved_file, str) and saved_file.lower().endswith(".h5")
        if name in literals:
            value, source = literals[name], "node.call.literal"
        elif saved_key and (dataset_reference or saved_key.startswith("ds_")):
            if saved_key == "ds_fit":
                value = ds_fit
            elif saved_key == "ds_raw":
                value = ds_raw
            elif callable(saved_datasets):
                value = saved_datasets(saved_key)
            elif saved_datasets is not None:
                value = saved_datasets.get(saved_key)
            source = Path(saved_file).name if dataset_reference else saved_key + ".h5"
            if value is None:
                reason = f"{name}: saved value {path} is missing"
                if p.default is not inspect.Parameter.empty:
                    decisions[name] = {"status": "defaulted", "source": "signature",
                                       "value": _report_value(p.default), "reason": reason}
                else:
                    decisions[name] = {"status": "unbound", "source": path, "reason": reason}
                    missing.append((name, reason))
                continue
        elif lname == "ds_fit" or path == "node.results.ds_fit":
            value, source = ds_fit, "ds_fit.h5"
        elif lname in ("ds", "dataset", "ds_raw", "raw"):
            value, source = ds_raw, "ds_raw.h5"
        elif lname in ("fits", "fit", "fit_results") and "fit_results" in results:
            value = _fit_records(fn, name, results["fit_results"], reconstruction,
                                 util_module=util_module, restore_objects=path == "node.namespace.fit_objects",
                                 input_path=path)
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
        elif path == "node.parameters":
            value, source = SimpleNamespace(**params), "parameters"
        elif path.startswith("node.namespace.") and path.removeprefix("node.namespace.") in results:
            key = path.removeprefix("node.namespace.")
            value = _saved_view(results[key], "data." + key)
            source = "data." + key + " via " + path
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
            computed = path.startswith("node.namespace.") or (node_tree is not None and any(
                isinstance(item, ast.Assign) and any(isinstance(target, ast.Name) and target.id == path
                    for target in item.targets) for item in ast.walk(node_tree)))
            reason = f"{name}: computed by the node's analysis, not saved" if computed else f"{name}: no saved input"
            decisions[name] = {"status": "unbound", "source": path or None, "reason": reason}
            missing.append((name, reason))
    if missing:
        raise ValueError("; ".join(f"unbound required arg {name!r}: {reason}" for name, reason in missing))
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


def _plot_plan(tree, imports, ds_fit, *, with_calls=False):
    """Select the node's plot calls and learn its explicit saved-key aliases."""
    if tree is None or not imports:
        return (None, {}, [], {}) if with_calls else (None, {})
    functions = set()
    aliases = {}
    variables = {}
    calls = []
    call_aliases = {}

    def origin(expr):
        if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name) and expr.func.id in imports:
            return {None: (imports[expr.func.id][1], id(expr))}
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

    def register(value, key=None):
        for member, (fn, call_id) in value.items():
            label = member if key is None else key
            if label is not None:
                aliases[fn] = label
                call_aliases[call_id] = label

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
                    calls.append(call)
            if isinstance(statement, ast.Assign):
                value = origin(statement.value)
                for target in statement.targets:
                    if isinstance(target, ast.Name):
                        variables[target.id] = value
                        if isinstance(statement.value, ast.Dict):
                            register(value)
                    elif isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant):
                        key = str(target.slice.value)
                        if _input_path(target) == "node.results.figures":
                            register(value)
                        elif not key.startswith("figure"):
                            register(value, key)
        return False

    actions = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name == "plot_data"]
    if not actions:
        return (None, {}, [], {}) if with_calls else (None, {})
    for action in actions:
        visit(action.body)
    return (functions, aliases, calls, call_aliases) if with_calls else (functions, aliases)


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
        candidate = str(member) if member is not None else figure.get("call_archive_key", aliases.get(fn, figure["function_key"]))
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
    if "--serve" in sys.argv[1:]:
        return serve()
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

    return _emit(replay(args), args.out)


def _execute(args):
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

    result = {"schema": "iplot/v2", "util": util, "node_name": node_name,
              "figures": [], "errors": [], "bind_report": {}, "not_produced": []}

    if not _VALID_UTIL.match(util):
        result["errors"].append({"stage": "import",
                                 "trace": f"refusing non-identifier util name {util!r}"})
        return result

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
        result.update(_source_manifest(args, node_name))
    except Exception:
        result["errors"].append({"stage": "import", "trace": traceback.format_exc()})
        try:
            # what was imported before the failure is still the code to watch:
            # its fix must invalidate whatever the revision ladder shows instead
            result.update(_source_manifest(args, node_name))
        except Exception:  # noqa: BLE001
            pass
        return result

    # qubits
    try:
        qnames = [q for q in args.qubits.split(",") if q] or params.get("qubits") or []
        machine = Quam.load(os.path.join(args.run, "quam_state"))
        if not qnames:
            qnames = list(machine.qubits.keys())
        qubits = [machine.qubits[n] for n in qnames if n in machine.qubits]
    except Exception:
        result["errors"].append({"stage": "qubits", "trace": traceback.format_exc()})
        return result

    # datasets
    opened = {}
    def _open(name):
        if name in opened:
            return opened[name]
        saved_path = results.get(name)
        relative = saved_path if isinstance(saved_path, str) and saved_path.lower().endswith(".h5") else name + ".h5"
        p = (Path(args.run) / relative).resolve()
        if not p.is_relative_to(Path(args.run).resolve()) or not p.is_file():
            return None
        try:
            ds = xr.open_dataset(p)
            args.datasets.append(ds)
            opened[name] = ds
            return ds
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
            ds = xr.open_dataset(p, engine=eng)
            args.datasets.append(ds)
            opened[name] = ds
            return ds
    try:
        ds_raw = _open("ds_raw")
        ds_fit = _open("ds_fit")
    except Exception:
        result["errors"].append({"stage": "datasets", "trace": traceback.format_exc()})
        return result

    from iplot_extract import extract_figure
    from iplot_markers import marker_defaults

    plot_functions = {n: getattr(U, n) for n in dir(U) if n.startswith("plot_") and callable(getattr(U, n))}
    if not args.util:
        for module, name in plot_imports.values():
            plot_functions[name] = getattr(importlib.import_module(module), name)
    plot_fns = sorted(plot_functions)
    selected, aliases, calls, call_aliases = _plot_plan(node_tree, plot_imports, ds_fit, with_calls=True)
    if selected is not None:
        plot_fns = [n for n in plot_fns if n in selected]
    saved = _saved_figures(args.run, results)
    invocations = [(fname, call) for fname in plot_fns
                   for call in ([c for c in calls if plot_imports[c.func.id][1] == fname] or [None])]
    for fname, call in invocations:
        function_key = re.sub(r"^plot_", "", fname)
        repeated = sum(original == fname for original, _ in invocations) > 1
        key = call_aliases.get(id(call), function_key) if repeated else function_key
        fn = plot_functions[fname]
        report = result["bind_report"][key] = {}
        try:
            kwargs = _bind_args(fn, ds_raw=ds_raw, ds_fit=ds_fit, qubits=qubits, params=params,
                                results=results, machine=machine, node_tree=node_tree,
                                plot_imports=plot_imports, bind_report=report, util_module=U, saved_datasets=_open,
                                node_call=call)
            fig = fn(**kwargs)
            produced = _extract_return(fig, key, report, extract_figure, result["not_produced"])
            for figure in produced:
                figure["function_key"] = function_key
                if id(call) in call_aliases:
                    figure["call_archive_key"] = call_aliases[id(call)]
            try:
                defaults = marker_defaults(fn, report)
            except Exception:  # noqa: BLE001
                # the check could not run: say so, never claim "no defaults"
                defaults = ["(marker check failed)"]
            for figure in produced:
                figure["marker_defaults"] = defaults
            result["figures"].extend(produced)
            import matplotlib.pyplot as plt
            plt.close("all")
        except Exception as error:
            result["errors"].append({"stage": f"plot:{key}", "trace": _plot_error_trace(error, report)})

    result["archive_match"] = _match_archive(result["figures"], saved, aliases)
    if args.only:
        result["figures"] = [f for f in result["figures"]
                             if args.only in (f["key"], f["source_key"], f["function_key"])]
    return result


SERVE_MARK = "@@SM-IPLOT@@"


def _source_manifest(args, node_name):
    from iplot_sources import fingerprint
    files = {str(Path(__file__).resolve()), str(Path(__file__).with_name("iplot_extract.py").resolve()),
             str(Path(__file__).with_name("iplot_sources.py").resolve()),
             str(Path(__file__).with_name("iplot_markers.py").resolve())}
    trees = set()
    roots = {str(Path(args.source_root).resolve())} if args.source_root else set()
    package = sys.modules.get("calibration_utils")
    for directory in getattr(package, "__path__", []):
        roots.add(str(Path(directory).resolve().parent))
    for name, module in list(sys.modules.items()):
        path = getattr(module, "__file__", None)
        if path and (name == "quam_config" or name.startswith(("quam_config.", "calibration_utils."))
                     or name == "calibration_utils"
                     or any(os.path.normcase(os.path.abspath(path)).startswith(os.path.normcase(root) + os.sep) for root in roots)):
            p = Path(path).resolve()
            if p.suffix == ".py":
                files.add(str(p))
            for directory in getattr(module, "__path__", []):
                trees.add(str(Path(directory).resolve()))
            if name == "calibration_utils":
                roots.add(str(p.parent.parent))
    stem = re.sub(r"^[0-9]+[A-Za-z]?Q_", "", node_name)
    for root in roots:
        files.update(str(p.resolve()) for p in (Path(root) / "calibrations").rglob(stem + ".py"))
        files.update(str(p.resolve()) for p in Path(root).glob(stem + ".py"))
    trees = {t for t in trees if not any(t != parent and Path(t).is_relative_to(Path(parent)) for parent in trees)}
    for tree in trees:
        files.update(str(p.resolve()) for p in Path(tree).rglob("*.py"))
    records = []
    for name in sorted(files):
        st = Path(name).stat()
        records.append({"path": name, "mtime_ns": st.st_mtime_ns, "size": st.st_size})
    return {"module_files": records, "module_trees": sorted(trees),
            "source_root": next(iter(sorted(roots)), ""),
            "code_fingerprint": fingerprint(files, trees)}


def replay(args):
    args.datasets = []
    try:
        result = _execute(args)
        manifest = result.get("module_files", [])
        result["sources"] = {m["path"]: [m["mtime_ns"], m["size"]] for m in manifest}
        return result
    finally:
        import matplotlib.pyplot as plt
        plt.close("all")
        for ds in args.datasets:
            close = getattr(ds, "close", None)
            if close:
                close()


def serve():
    """One request and one marked response per line; imports stay warm."""
    from types import SimpleNamespace
    out = sys.stdout
    sys.stdout = sys.stderr
    seen = {}
    try:
        for line in sys.stdin:
            if not line.strip():
                continue
            try:
                spec = json.loads(line)
                args = SimpleNamespace(run=spec["run"], source_root=spec.get("source_root") or "",
                                       only=spec.get("only") or "", util=spec.get("util") or "",
                                       qubits=spec.get("qubits") or "", out="")
                result = replay(args)
                for path, stat in result.get("sources", {}).items():
                    seen.setdefault(path, stat)
                result["sources"] = {p: seen[p] for p in result.get("sources", {})}
                result["status"] = "ok"
            except BaseException:
                result = {"status": "error", "figures": [], "errors": [
                    {"stage": "request", "trace": traceback.format_exc()}]}
            out.write(SERVE_MARK + json.dumps(result, default=_json_default, allow_nan=False) + "\n")
            out.flush()
    finally:
        sys.stdout = out
    return 0


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
