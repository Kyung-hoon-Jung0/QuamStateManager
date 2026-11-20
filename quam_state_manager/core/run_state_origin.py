"""Was the newest run made from the state of the chip open in SM?

A QUAlibrate run saves the quam state it ran with (``quam_state/state.json``
in the run folder). A node may run from an in-memory state that evolves run to
run while the chip file on disk stays put, even when the project's configured
state folder IS the open chip; the folder rule (``routes._project_state_mismatch``)
cannot see that. This module is the content rule: compare the newest run's saved
state with the open chip's state, leaf by leaf, through ``differ.compare_equal``
(the one equality rule, contract 10).

What is NOT a difference:

* a path the run itself records writing -- its ``state_updates.json``
  ``operations`` and node.json ``patches`` / ``state_changes`` -- and every leaf
  under it. Both sides of such a write may differ legitimately (the run's
  ``value`` is not yet accepted; its ``old`` is the state it started from).
* nothing else: SM keeps no list of paths a node rewrites on every run, so any
  other leaf difference counts.

READ-ONLY. Reads go through ``safe_io``; one run folder per call; results are
cached by (run folder, state file mtimes, chip revision). Never raises.
"""

from __future__ import annotations

import logging
import os
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, Iterator

from quam_state_manager.core import differ, fit_targets, safe_io

logger = logging.getLogger(__name__)

MAX_EXAMPLES = 3
_SHORT = 48
_CACHE_MAX = 8
_cache: OrderedDict[tuple, dict | None] = OrderedDict()
_lock = threading.Lock()
_MISSING = object()


def _mtime_ns(path: Path) -> int | None:
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return None


def run_written_paths(run_folder: Path | str) -> set[str]:
    """Dot paths the run records writing (any of its write sources)."""
    folder = Path(run_folder)
    out: set[str] = set()
    node = safe_io.scan_json(folder / "node.json") if (folder / "node.json").is_file() else None
    proposals = (safe_io.scan_json(folder / "state_updates.json")
                 if (folder / "state_updates.json").is_file() else None)
    for writes in (fit_targets._patch_writes(node), fit_targets._change_writes(node),
                   fit_targets._proposal_writes(proposals)):
        out.update(w["path"] for w in writes)
    return out


def _ignored(path: str, ignore: set[str]) -> bool:
    if path in ignore:
        return True
    parts = path.split(".")
    return any(".".join(parts[:i]) in ignore for i in range(1, len(parts)))


def _leaf_diffs(a: Any, b: Any, prefix: str, ignore: set[str]) -> Iterator[tuple[str, Any, Any]]:
    """Yield (path, run value, chip value) for every leaf that differs."""
    if prefix and _ignored(prefix, ignore):
        return
    if isinstance(a, dict) and isinstance(b, dict):
        for k in list(a) + [k for k in b if k not in a]:
            p = f"{prefix}.{k}" if prefix else str(k)
            yield from _leaf_diffs(a.get(k, _MISSING), b.get(k, _MISSING), p, ignore)
        return
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b)):
            yield from _leaf_diffs(x, y, f"{prefix}.{i}" if prefix else str(i), ignore)
        return
    if a is _MISSING or b is _MISSING or not differ.compare_equal(a, b):
        yield prefix, a, b


def _short(v: Any) -> str:
    if v is _MISSING:
        return "(absent)"
    text = repr(v) if isinstance(v, str) else str(v)
    return text if len(text) <= _SHORT else text[:_SHORT - 3] + "..."


def compare_states(run_state: dict, chip_state: dict, ignore: set[str]) -> dict:
    """``{"differs", "count", "examples"}`` for two state dicts."""
    count = 0
    examples: list[dict[str, str]] = []
    for path, rv, cv in _leaf_diffs(run_state, chip_state, "", ignore):
        count += 1
        if len(examples) < MAX_EXAMPLES:
            examples.append({"path": path, "run": _short(rv), "chip": _short(cv)})
    return {"differs": count > 0, "count": count, "examples": examples}


def newest_run_origin(run_folder: Path | str, run_id: Any, chip_state: dict,
                      chip_rev: Any) -> dict | None:
    """Compare one run's saved quam state with the open chip's state.

    Returns ``{"differs", "count", "examples", "run_id"}`` (examples: up to
    :data:`MAX_EXAMPLES` ``{"path", "run", "chip"}`` with short text values), or
    None when the run has no readable ``quam_state/state.json`` (nothing to say).
    ``chip_rev`` must change whenever ``chip_state`` does (the cache key)."""
    folder = Path(run_folder)
    state_file = folder / "quam_state" / "state.json"
    mtime = _mtime_ns(state_file)
    if mtime is None or not isinstance(chip_state, dict):
        return None
    key = (str(folder), mtime, _mtime_ns(folder / "state_updates.json"),
           _mtime_ns(folder / "node.json"), chip_rev)
    with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    try:
        run_state = safe_io.scan_json(state_file)
        if run_state is None:
            return None             # mid-write or unreadable: look again later
        out = compare_states(run_state, chip_state, run_written_paths(folder))
        out["run_id"] = run_id
    except Exception:  # noqa: BLE001 -- a notice must never break a render
        logger.debug("run state compare failed for %s", folder, exc_info=True)
        return None
    with _lock:
        _cache[key] = out
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return out


def clear_cache() -> None:
    with _lock:
        _cache.clear()
