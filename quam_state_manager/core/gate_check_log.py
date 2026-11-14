"""The per-chip record of the last lab check of each CZ gate.

One file per chip, ``gate_checks.json``, in the chip's folder of SM's history
store (the same folder :meth:`HistoryManager.resolve_chip_dir` gives every
other per-chip record). Never in the repository and never in the state: the
state carries no check results. One entry per (pair, gate)::

    {"result": "checked" | "not checked", "reason": str | None,
     "time": ISO-8601 UTC, "hash": gate_parts.content_hash at that time}

A missing or unreadable file reads as "no record", never an error. Writes are
atomic (a temporary file, then a replace).

The door that creates a gate calls, after its write::

    gate_check_log.record_check(history_manager, chip_path, store.merged,
                                pair, gate, check_result)

where *check_result* is the :func:`gate_check.check_gates` result (or None
when no check ran).
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FILE_NAME = "gate_checks.json"
CHECKED = "checked"
NOT_CHECKED = "not checked"

_LOCK = threading.Lock()


def _key(pair: str, gate: str) -> str:
    return f"{pair}/{gate}"


def chip_dir(history_manager, chip_path) -> Path:
    """The chip's per-chip folder (the history store's identity ladder)."""
    return Path(history_manager.resolve_chip_dir(Path(chip_path))[0])


def _file(folder) -> Path:
    return Path(folder) / FILE_NAME


def read(folder) -> dict[str, dict]:
    """``{"<pair>/<gate>": entry}``; ``{}`` when the file is missing or
    cannot be read."""
    try:
        data = json.loads(_file(folder).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    gates = data.get("gates") if isinstance(data, dict) else None
    return {k: v for k, v in gates.items() if isinstance(v, dict)} if isinstance(gates, dict) else {}


def entry(folder, pair: str, gate: str) -> dict | None:
    """The last record of one gate, or None."""
    return read(folder).get(_key(pair, gate))


def content_hash(merged: dict, pair: str, gate: str) -> str | None:
    """The gate's content hash (macro subtree + the operations it owns)."""
    from quam_state_manager.core.gate_parts import content_hash as _hash
    return _hash(merged, pair, gate)


def record(folder, pair: str, gate: str, *, result: str, reason: str | None,
           content: str | None, when: datetime | None = None) -> dict:
    """Write the last check of one gate (atomic). Returns the entry."""
    if result not in (CHECKED, NOT_CHECKED):
        raise ValueError(f"result must be {CHECKED!r} or {NOT_CHECKED!r}")
    when = when or datetime.now(timezone.utc)
    ent = {"result": result, "reason": reason,
           "time": when.astimezone(timezone.utc).isoformat(timespec="seconds"),
           "hash": content}
    folder = Path(folder)
    with _LOCK:
        gates = read(folder)
        gates[_key(pair, gate)] = ent
        folder.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".gate_checks.", suffix=".tmp", dir=str(folder))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"version": 1, "gates": gates}, fh, indent=1, sort_keys=True)
            os.replace(tmp, _file(folder))
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    return ent


def result_of(check: dict | None) -> tuple[str, str | None]:
    """``(result, reason)`` from a gate-check result: "checked" only when the
    lab's code ran every check of the gate."""
    if not isinstance(check, dict):
        return NOT_CHECKED, "no check ran"
    if check.get("all_checked"):
        return CHECKED, None
    return NOT_CHECKED, (check.get("why_not_checked") or check.get("error")
                         or "the lab check did not run")


def record_check(history_manager, chip_path, merged: dict, pair: str, gate: str,
                 check: dict | None) -> dict:
    """The one call a door makes after it wrote a gate (or made it active):
    records *check* against the gate's content as it is now."""
    result, reason = result_of(check)
    return record(chip_dir(history_manager, chip_path), pair, gate, result=result,
                  reason=reason, content=content_hash(merged, pair, gate))
