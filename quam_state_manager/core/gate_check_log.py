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
    """The entry key: a JSON list, so no pair and gate can collide."""
    return json.dumps([str(pair), str(gate)])


class RecordError(OSError):
    """The check record could not be written; the caller says so."""


def chip_dir(history_manager, chip_path) -> Path:
    """The chip's per-chip folder (the history store's identity ladder)."""
    return Path(history_manager.resolve_chip_dir(Path(chip_path))[0])


def _file(folder) -> Path:
    return Path(folder) / FILE_NAME


def read(folder) -> dict[str, dict]:
    """``{key: entry}`` (key: :func:`_key`); ``{}`` when the file is missing
    or cannot be read."""
    if folder is None:
        return {}
    try:
        data = json.loads(_file(folder).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    gates = data.get("gates") if isinstance(data, dict) else None
    return {k: v for k, v in gates.items() if isinstance(v, dict)} if isinstance(gates, dict) else {}


def entry(folder, pair: str, gate: str) -> dict | None:
    """The last record of one gate, or None. A file written with the older
    ``"<pair>/<gate>"`` keys still reads, when that key is not ambiguous."""
    gates = read(folder)
    new = gates.get(_key(pair, gate))
    old = (gates.get(f"{pair}/{gate}")
           if "/" not in str(pair) and "/" not in str(gate) else None)
    if new is None or old is None:
        return new if new is not None else old
    # both: the later time wins; equal or unreadable times: the new key
    try:
        if _time(old) > _time(new):
            return old
    except TypeError:          # a missing time, or naive vs aware: unreadable
        pass
    return new


def _time(ent: dict):
    try:
        return datetime.fromisoformat(str(ent.get("time")))
    except (TypeError, ValueError):
        return None


def content_hash(merged: dict, pair: str, gate: str) -> str | None:
    """The gate's content hash (macro subtree + the operations it owns)."""
    from quam_state_manager.core.gate_parts import content_hash as _hash
    return _hash(merged, pair, gate)


def record(folder, pair: str, gate: str, *, result: str, reason: str | None,
           content: str | None, when: datetime | None = None) -> dict:
    """Write the last check of one gate (atomic). Returns the entry.

    "checked" needs the content hash (a record without one cannot be matched
    to the values later): ValueError. A folder that cannot be written raises
    :class:`RecordError`."""
    if result not in (CHECKED, NOT_CHECKED):
        raise ValueError(f"result must be {CHECKED!r} or {NOT_CHECKED!r}")
    if result == CHECKED and not content:
        raise ValueError("a checked result needs the gate's content hash")
    when = when or datetime.now(timezone.utc)
    from quam_state_manager.core.gate_parts import HASH_RULE
    ent = {"result": result, "reason": reason,
           "time": when.astimezone(timezone.utc).isoformat(timespec="seconds"),
           "hash": content, "rule": HASH_RULE}
    folder = Path(folder)
    try:
        return _write(folder, pair, gate, ent)
    except OSError as exc:
        raise RecordError(f"the check record could not be saved ({type(exc).__name__})") from exc


def _write(folder: Path, pair: str, gate: str, ent: dict) -> dict:
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
    records *check* against the gate's content as it is now. Never raises:
    ``{"ok": True, "entry": ...}`` or ``{"ok": False, "error": text}`` for the
    door to show ("the gate was written, the check record was not saved")."""
    try:
        result, reason = result_of(check)
        content = content_hash(merged, pair, gate)
        if result == CHECKED and not content:
            result, reason = NOT_CHECKED, "the gate's content could not be read"
        ent = record(chip_dir(history_manager, chip_path), pair, gate, result=result,
                     reason=reason, content=content)
        return {"ok": True, "entry": ent}
    except Exception as exc:  # noqa: BLE001 -- a typed failure, never a raise
        return {"ok": False, "error": str(exc) or type(exc).__name__}
