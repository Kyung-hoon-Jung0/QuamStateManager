"""Inject Scheduler overrides into a qualibrate node/graph — stdlib ``ast`` only.

The Scheduler never edits a user's source file. To run an experiment with chosen
qubits/parameters it writes a *temp copy in the same folder* (so the file's
``../../../..`` / ``Path(__file__).parents[n]`` import math resolves identically)
with the overrides spliced in, runs the copy, then deletes it. The copy's
filename never leaks into node identity — ``QualibrationNode(name=...)`` is always
a hardcoded string literal (verified). See docs/40_scheduler.md.

For **nodes**: overrides go into the ``custom_param`` run-action body, which is
the framework's intended terminal-run injection point (skipped under GUI/graph
``external`` mode). Every consumer reads ``node.parameters.X`` live at call time,
so the injection takes effect for the whole run. Assignments are hasattr-guarded
so a param a particular node lacks (e.g. ``simulate``) is silently skipped rather
than tripping Pydantic's ``extra='forbid'``.

For **graphs**: the single graph-level targets field default is replaced (Phase 3
run wiring).
"""

from __future__ import annotations

import ast
import json
import logging
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

_MARKER = "# --- overridden by State Manager Scheduler ---"


class NoHookError(ValueError):
    """The node has no ``custom_param`` hook to inject into."""


class SpliceError(ValueError):
    """The graph's targets field could not be located for splicing."""


def _find_custom_param(tree: ast.AST, src: str):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "custom_param":
            for dec in node.decorator_list:
                seg = ast.get_source_segment(src, dec) or ""
                if "run_action" in seg and "external" in seg:
                    return node
    return None


def splice_node(src: str, overrides: dict) -> str:
    """Return *src* with the ``custom_param`` body replaced by the overrides.

    Raises :class:`NoHookError` if the file has no custom_param hook. An empty
    *overrides* returns *src* unchanged.
    """
    if not overrides:
        return src
    tree = ast.parse(src)
    func = _find_custom_param(tree, src)
    if func is None:
        raise NoHookError("no custom_param hook in this node")

    # Serialise via JSON rather than repr(): repr() of a non-finite float
    # (NaN/inf) emits the bare tokens `nan`/`inf`, which are NameErrors at run
    # time. json.dumps(allow_nan=False) rejects those *here* (surfaced to the
    # worker as a prepare failure) instead of crashing the experiment, and
    # ensure_ascii sidesteps any source-encoding issue.
    try:
        payload = json.dumps(overrides, ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"override values are not JSON-serialisable: {exc}") from exc

    lines = src.splitlines(keepends=True)
    body_start = func.body[0].lineno          # 1-based, first body statement
    body_end = func.end_lineno                # 1-based, last line of the function
    # Indent = leading whitespace of the first body line.
    first = lines[body_start - 1]
    indent = first[: len(first) - len(first.lstrip())]

    inject = [
        f"{indent}{_MARKER}\n",
        f"{indent}import json as _sched_json\n",
        f"{indent}_sched_overrides = _sched_json.loads({payload!r})\n",
        f"{indent}for _sched_k, _sched_v in _sched_overrides.items():\n",
        f"{indent}    if hasattr(node.parameters, _sched_k):\n",
        f"{indent}        setattr(node.parameters, _sched_k, _sched_v)\n",
    ]
    return "".join(lines[: body_start - 1] + inject + lines[body_end:])


def _graph_call_base(call: ast.Call) -> str | None:
    """Base name of a QualibrationGraph(...) / QualibrationGraph.build(...) call."""
    f = call.func
    if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
        return f.value.id          # QualibrationGraph.build → "QualibrationGraph"
    if isinstance(f, ast.Name):
        return f.id
    return None


def _graph_params_class_name(tree: ast.AST) -> str | None:
    """Resolve the class actually used as the graph's ``parameters``.

    Builder graphs bind it at module level (``parameters = Parameters()``);
    dict-style graphs pass it inline (``QualibrationGraph(parameters=Parameters())``).
    Returns the class name, or ``None`` if it can't be determined. This is what
    keeps the splice off a sibling subgraph-params class (e.g. RetuneParameters).
    """
    assigned: dict[str, str] = {}  # module-level var -> class name from `var = X()`
    for stmt in ast.walk(tree):
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 \
                and isinstance(stmt.targets[0], ast.Name) \
                and isinstance(stmt.value, ast.Call) and isinstance(stmt.value.func, ast.Name):
            assigned[stmt.targets[0].id] = stmt.value.func.id
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _graph_call_base(node) == "QualibrationGraph":
            for kw in node.keywords:
                if kw.arg == "parameters":
                    val = kw.value
                    if isinstance(val, ast.Call) and isinstance(val.func, ast.Name):
                        return val.func.id
                    if isinstance(val, ast.Name):
                        return assigned.get(val.id)
    return assigned.get("parameters")


def _field_value(cls: ast.ClassDef, name: str):
    """Default-value node of class-body field *name* (AnnAssign or Assign), or None."""
    for stmt in cls.body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) \
                and stmt.target.id == name and stmt.value is not None:
            return stmt.value
        if isinstance(stmt, ast.Assign):
            for tgt in stmt.targets:
                if isinstance(tgt, ast.Name) and tgt.id == name:
                    return stmt.value
    return None


def splice_graph(src: str, targets_name: str, targets: list) -> str:
    """Return *src* with the graph's *targets_name* default replaced by *targets*.

    Splices ONLY the field on the class actually bound as the graph's
    ``parameters`` (resolved via :func:`_graph_params_class_name`) — never a
    sibling subgraph-params class. If the bound class can't be resolved, fails
    CLOSED: raises :class:`SpliceError` when more than one class declares the
    field (ambiguous) rather than guessing. Empty *targets* → *src* unchanged.
    """
    if not targets:
        return src
    tree = ast.parse(src)
    bound = _graph_params_class_name(tree)
    if bound is not None:
        classes = [n for n in ast.walk(tree)
                   if isinstance(n, ast.ClassDef) and n.name == bound]
    else:
        classes = [n for n in ast.walk(tree)
                   if isinstance(n, ast.ClassDef) and _field_value(n, targets_name) is not None]
        if len(classes) > 1:
            raise SpliceError(
                f"ambiguous graph Parameters: {len(classes)} classes declare "
                f"'{targets_name}' and the bound class couldn't be resolved")
    value_node = None
    for cls in classes:
        value_node = _field_value(cls, targets_name)
        if value_node is not None:
            break
    if value_node is None:
        raise SpliceError(f"no '{targets_name}' field to override in graph Parameters")
    return _replace_span(src, value_node, repr(list(targets)))


def _replace_span(src: str, node: ast.AST, new_text: str) -> str:
    """Replace the source span of *node* with *new_text* (handles multi-line)."""
    lines = src.splitlines(keepends=True)
    sl, sc = node.lineno, node.col_offset
    el, ec = node.end_lineno, node.end_col_offset
    if sl == el:
        line = lines[sl - 1]
        lines[sl - 1] = line[:sc] + new_text + line[ec:]
        return "".join(lines)
    # multi-line span: keep prefix of first line + suffix of last line
    first = lines[sl - 1][:sc] + new_text
    last = lines[el - 1][ec:]
    return "".join(lines[: sl - 1] + [first + last] + lines[el:])


# Keys the scheduler owns — they come from the targets row + the Dry-run toggle,
# NEVER from a param override (a `simulate`/`qubits` override would silently
# defeat the dry-run gate or retarget the run).
RESERVED_OVERRIDE_KEYS = ("simulate", "qubits", "qubit_pairs", "targets")


def strip_reserved_overrides(overrides: dict | None, targets_name: str | None = None) -> dict:
    """Drop reserved keys (simulate / targets) from a param-override dict."""
    reserved = set(RESERVED_OVERRIDE_KEYS)
    if targets_name:
        reserved.add(targets_name)
    return {k: v for k, v in (overrides or {}).items() if k not in reserved}


def build_node_overrides(targets_name: str, targets: list | None, *,
                         simulate: bool, extra: dict | None = None) -> dict:
    """Assemble the override dict for a node run.

    Param overrides (*extra*) are applied first, then the scheduler's reserved
    keys are stripped and the targets field + ``simulate`` are force-set last, so
    a param override can never override the Dry-run flag or the run targets. Only
    sets the targets field when *targets* is non-empty (empty/None = all active).
    """
    out: dict = {}
    if extra:
        out.update(extra)
    out = strip_reserved_overrides(out, targets_name)
    if targets:
        out[targets_name or "qubits"] = list(targets)
    out["simulate"] = bool(simulate)
    return out


# ----------------------------------------------------------------------
# Temp copy lifecycle (same folder as the original)
# ----------------------------------------------------------------------

def make_temp_copy(source_file: Path | str, content: str) -> Path:
    """Write *content* as ``_sched_<uuid>_<stem>.py`` next to *source_file*."""
    source_file = Path(source_file)
    name = f"_sched_{uuid.uuid4().hex[:8]}_{source_file.stem}.py"
    dest = source_file.parent / name
    dest.write_text(content, encoding="utf-8")
    return dest


def cleanup_temp_copy(path: Path | str) -> None:
    """Delete a temp copy; tolerate it already being gone."""
    try:
        Path(path).unlink()
    except OSError:
        logger.debug("could not remove temp copy %s", path, exc_info=True)


def cleanup_orphan_temp_copies(folder: Path | str) -> int:
    """Delete leftover ``_sched_*.py`` copies in *folder* (e.g. from a crash).

    A temp copy left inside a configured qualibrate calibration_library.folder
    would be registered by qualibrate's scanner under the original node's name
    and overwrite it — so sweep them before each run. Returns the count removed.
    """
    folder = Path(folder)
    if not folder.is_dir():
        return 0
    removed = 0
    try:
        for p in folder.glob("_sched_*.py"):
            try:
                p.unlink()
                removed += 1
            except OSError:
                logger.debug("could not remove orphan temp copy %s", p, exc_info=True)
    except OSError:
        return removed
    return removed
