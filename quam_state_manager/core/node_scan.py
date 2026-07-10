"""Discover qualibrate nodes/graphs in a folder — hardware-safe (no import).

Uses only the Python standard library ``ast`` to parse each ``.py`` *without
executing it*. This is critical: qualibrate node files run their experiment at
import time (and dict-style graph files call ``g.run()`` at module top level
with no ``__main__`` guard), so importing one would fire a real hardware run.
``ast.parse`` reads source text only.

Per the Phase-1 verification (docs/40_scheduler.md): of 68 node files, 64 carry
the exact ``@node.run_action(skip_if=node.modes.external) def custom_param``
hook (the parameter-injection point), 4 are hookless utility nodes; ``name=`` is
always a string literal; graphs come in dict-style (top-level ``g.run()``) and
builder-style (``__main__``-guarded) flavours.
"""

from __future__ import annotations

import ast
import json
import logging
import threading
from collections import OrderedDict
from dataclasses import asdict, dataclass
from pathlib import Path

from quam_state_manager.core import safe_io

logger = logging.getLogger(__name__)

KIND_NODE = "node"
KIND_GRAPH = "graph"
KIND_OTHER = "other"


@dataclass
class NodeInfo:
    file: str
    name: str
    kind: str               # "node" | "graph" | "other"
    has_hook: bool          # node has the custom_param injection hook
    targets_name: str       # "qubits" | "qubit_pairs"
    description: str
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


# --- Scan cache (DISPLAY-ONLY) -----------------------------------------------
#
# ``scan_folder`` is the Scheduler's hot path for the library list, over a WSL 9p
# mount where each read_text + ast.parse is expensive. It caches each file's
# derived NodeInfo keyed on a cheap ``(st_mtime_ns, st_size)`` stat fingerprint —
# two tiers: an in-process LRU dict + a JSON persisted under the instance dir.
#
# SAFETY: this cache feeds the library DISPLAY only. Every safety-critical caller
# (queue-add and the run path) goes through ``scan_file``, which ALWAYS reads +
# re-derives from the file's current bytes — so a classification that reaches a
# queued or executed item can never be a stale cache hit. A mtime+size-preserving
# edit that slips past the stat fingerprint would mis-label only the library
# display, never the run path. Bump ``_HEURISTICS_VERSION`` whenever the _classify
# / _has_custom_param_hook / _targets_name / parse semantics change so stale
# derivations are discarded on load. Error NodeInfos are never cached.

_HEURISTICS_VERSION = 1
_CACHE_FILENAME = "scheduler_ast_scan_cache.json"
_MEM_CACHE_CAP = 2000          # bound the process-global dict (renamed/deleted paths linger)


@dataclass
class _Entry:
    mtime_ns: int
    size: int
    info: NodeInfo


_MEM_CACHE: "OrderedDict[str, _Entry]" = OrderedDict()   # absolute path -> _Entry (LRU)
_MEM_LOCK = threading.Lock()


def clear_cache() -> None:
    """Drop the in-process cache (used by tests; not needed in production)."""
    with _MEM_LOCK:
        _MEM_CACHE.clear()


def _mem_put(key: str, entry: _Entry) -> None:
    """Insert into the in-process LRU, evicting the oldest past the cap."""
    with _MEM_LOCK:
        _MEM_CACHE[key] = entry
        _MEM_CACHE.move_to_end(key)
        while len(_MEM_CACHE) > _MEM_CACHE_CAP:
            _MEM_CACHE.popitem(last=False)


def _stat_fingerprint(path: Path) -> tuple[int, int] | None:
    """``(st_mtime_ns, st_size)`` or ``None`` on OSError — metadata only, never opens content."""
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


def _cache_path(instance_path) -> Path:
    return Path(instance_path) / _CACHE_FILENAME


def _load_disk_cache(instance_path) -> dict[str, _Entry]:
    """Tolerant read of the persisted cache. ``{}`` on missing/corrupt/version-mismatch."""
    try:
        raw = json.loads(_cache_path(instance_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict) or raw.get("version") != _HEURISTICS_VERSION:
        return {}
    out: dict[str, _Entry] = {}
    for p, row in (raw.get("files") or {}).items():
        try:
            out[p] = _Entry(int(row["mtime_ns"]), int(row["size"]), NodeInfo(**row["info"]))
        except (KeyError, TypeError, ValueError):
            continue            # skip one bad row, keep the rest
    return out


def _save_disk_cache(instance_path, entries: dict[str, _Entry]) -> None:
    payload = {
        "version": _HEURISTICS_VERSION,
        "files": {p: {"mtime_ns": e.mtime_ns, "size": e.size, "info": e.info.to_dict()}
                  for p, e in entries.items()},
    }
    try:
        safe_io.atomic_write_json(_cache_path(instance_path), payload)
    except OSError:
        logger.warning("Could not persist node-scan cache", exc_info=True)


def _const_str(node) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _call_base(call: ast.Call) -> tuple[str | None, str | None]:
    """Return (base_name, attr) for a Call's func, unwrapping Subscript/Attribute.

    ``QualibrationNode[Params, Quam](...)`` → ("QualibrationNode", None);
    ``QualibrationGraph.build(...)``        → ("QualibrationGraph", "build");
    ``QualibrationGraph(...)``              → ("QualibrationGraph", None).
    """
    f = call.func
    if isinstance(f, ast.Subscript):
        f = f.value
    if isinstance(f, ast.Attribute):
        base = f.value
        if isinstance(base, ast.Subscript):
            base = base.value
        return (base.id if isinstance(base, ast.Name) else None), f.attr
    if isinstance(f, ast.Name):
        return f.id, None
    return None, None


def _kwarg(call: ast.Call, name: str):
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _has_custom_param_hook(tree: ast.AST, src: str) -> bool:
    """True if a ``custom_param`` run-action decorated with skip_if external exists."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "custom_param":
            for dec in node.decorator_list:
                seg = ast.get_source_segment(src, dec) or ""
                if "run_action" in seg and "external" in seg:
                    return True
    return False


def _targets_name(tree: ast.AST, src: str, name: str) -> str:
    """Best-effort "qubits" vs "qubit_pairs" for a node/graph (no base resolution).

    Looks for an explicit ``targets_name = "qubit_pairs"`` in any class body, then
    falls back to source/name heuristics. The inject is hasattr-guarded, so a
    wrong guess degrades to "run on all active targets", never a crash.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for stmt in node.body:
                # `targets_name: ClassVar[str] = "qubit_pairs"` (the canonical
                # qualibrate form) is an AnnAssign; `targets_name = "..."` is an
                # Assign. Handle both.
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) \
                        and stmt.target.id == "targets_name" and stmt.value is not None:
                    val = _const_str(stmt.value)
                    if val:
                        return val
                if isinstance(stmt, ast.Assign):
                    for tgt in stmt.targets:
                        if isinstance(tgt, ast.Name) and tgt.id == "targets_name":
                            val = _const_str(stmt.value)
                            if val:
                                return val
    low = (name or "").lower()
    if low.startswith("cz") or "qubit_pairs" in src or "get_qubit_pairs" in src:
        return "qubit_pairs"
    return "qubits"


def _classify(tree: ast.AST, src: str) -> tuple[str, str | None]:
    """Return (kind, name) for a parsed module.

    Graph-wins: a file that constructs both an inline QualibrationNode helper
    and a QualibrationGraph is a graph. ``attr`` is checked too so a
    module-qualified call (``qm.QualibrationNode[...](...)``) still classifies.
    """
    node_name = None
    graph_name = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        base, attr = _call_base(node)
        is_graph = base == "QualibrationGraph" or attr == "QualibrationGraph"
        is_node = base == "QualibrationNode" or attr == "QualibrationNode"
        if is_graph and graph_name is None:
            nm = _kwarg(node, "name")
            if nm is None and node.args:  # build(name, ...) positional
                nm = node.args[0]
            graph_name = _const_str(nm) or ""
        elif is_node and node_name is None:
            node_name = _const_str(_kwarg(node, "name")) or ""
    if graph_name is not None:
        return KIND_GRAPH, graph_name or None
    if node_name is not None:
        return KIND_NODE, node_name or None
    return KIND_OTHER, None


def scan_file(path: Path | str) -> NodeInfo:
    """Classify one ``.py`` file by reading + parsing its CURRENT bytes. Never raises.

    Always fresh (no cache) — this is the **safety-critical** entry used by the
    Scheduler's queue-add and run paths, so a stale cache can never feed a queued
    or executed classification. The library list (``scan_folder``) is the only
    cached path. Errors land in ``NodeInfo.error``.
    """
    path = Path(path)
    try:
        src = path.read_text(encoding="utf-8")
    except OSError as exc:
        return NodeInfo(str(path), path.stem, KIND_OTHER, False, "qubits", "", f"read error: {exc}")
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        return NodeInfo(str(path), path.stem, KIND_OTHER, False, "qubits", "", f"parse error: {exc}")

    kind, name = _classify(tree, src)
    name = name or path.stem
    has_hook = _has_custom_param_hook(tree, src) if kind == KIND_NODE else False
    targets = _targets_name(tree, src, name)
    doc = (ast.get_docstring(tree) or "").strip().splitlines()
    description = doc[0] if doc else ""
    return NodeInfo(str(path), name, kind, has_hook, targets, description)


def scan_folder(folder: Path | str, *, instance_path=None) -> list[NodeInfo]:
    """Scan every ``.py`` in *folder* (non-recursive). Sorted by filename, cached.

    Loads the disk cache once, then per file does a cheap stat; unchanged files
    reuse their cached ``NodeInfo`` and only new/modified files are read+parsed.
    The persisted set is rebuilt from exactly the (non-error) files present, so
    deleted/renamed files drop out automatically. Skips our own ``_sched_*`` temp
    copies and dunder files. DISPLAY-only — see the cache-safety note above.
    """
    folder = Path(folder)
    if not folder.is_dir():
        return []
    try:
        files = sorted(folder.glob("*.py"))
    except OSError:
        return []
    disk = _load_disk_cache(instance_path) if instance_path is not None else {}
    out: list[NodeInfo] = []
    new_entries: dict[str, _Entry] = {}
    for f in files:
        if f.name.startswith("_sched_") or f.name.startswith("__"):
            continue
        key = str(f)
        fp = _stat_fingerprint(f)
        if fp is None:                          # vanished mid-scan / unreadable
            out.append(scan_file(f))
            continue
        mtime_ns, size = fp
        with _MEM_LOCK:
            e = _MEM_CACHE.get(key)
        if not (e is not None and e.mtime_ns == mtime_ns and e.size == size):
            e = disk.get(key)
            if not (e is not None and e.mtime_ns == mtime_ns and e.size == size):
                e = None
        if e is None:                           # miss -> the only read+parse cost
            info = scan_file(f)
            e = _Entry(mtime_ns, size, info)
            if info.error is None:
                _mem_put(key, e)
        else:
            _mem_put(key, e)                    # promote disk -> memory (LRU touch)
        out.append(e.info)
        if e.info.error is None:
            new_entries[key] = e
    # Persist by MERGING: keep other folders' rows, replace exactly this folder's
    # rows with new_entries (so switching calibration folders doesn't evict the
    # rest of the cross-restart cache). Write only when the merged set changed.
    if instance_path is not None:
        merged = {k: v for k, v in disk.items() if Path(k).parent != folder}
        merged.update(new_entries)
        if merged != disk:                      # _Entry/NodeInfo are dataclasses (deep ==)
            _save_disk_cache(instance_path, merged)
    return out
