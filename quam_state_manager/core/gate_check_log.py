"""The per-chip record of the last lab check of each CZ gate.

One file per chip, ``gate_checks.json``, in the chip's folder of SM's history
store (the same folder :meth:`HistoryManager.resolve_chip_dir` gives every
other per-chip record). Never in the repository and never in the state: the
state carries no check results. One entry per (pair, gate)::

    {"result": "checked" | "partly checked" | "not checked", "reason": str | None,
     "not_run": [label] (partly checked only),
     "time": ISO-8601 UTC, "hash": gate_parts.content_hash at that time}

A missing or unreadable file reads as "no record", never an error. Writes are
atomic (a temporary file, then a replace).

The door that creates a gate calls, after its write::

    gate_check_log.record_check(history_manager, chip_path, store.merged,
                                pair, gate, check_result)

where *check_result* is the :func:`gate_check.check_gates` result (or None
when no check ran) or its :func:`outcome` for the gate.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FILE_NAME = "gate_checks.json"
CHECKED = "checked"
PARTLY = "partly checked"
NOT_CHECKED = "not checked"

# what :func:`outcome` says a gate's check came to
KIND_CHECKED = "checked"   # every check ran and passed
KIND_PARTLY = "partly"     # the checks that ran passed, some could not run
KIND_FAILED = "failed"     # a check failed
KIND_NONE = "none"         # the lab's code did not run the gate

CHECK_LABELS = {"load_config": "loading the chip", "ops": "running the gate",
                "length": "length check", "preview": "preview comparison",
                "timeline": "timeline", "range": "output range check",
                "spectator_scale": "the spectator levels"}

#: checks whose ``note`` means "this gate has nothing of that kind": such a
#: note is neither a passed check nor one of the total, so the counts of a
#: gate without spectator rows read as they did before the check existed
UNCOUNTED_NOTES = ("spectator_scale",)


def _counted(name: str, c: Any) -> bool:
    """Use one inclusion rule for the client checks and the stored count."""
    return not (name in UNCOUNTED_NOTES and isinstance(c, dict) and c.get("status") == "note")

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
           content: str | None, when: datetime | None = None,
           not_run: list | None = None) -> dict:
    """Write the last check of one gate (atomic). Returns the entry.

    "checked" needs the content hash (a record without one cannot be matched
    to the values later): ValueError. A folder that cannot be written raises
    :class:`RecordError`."""
    if result not in (CHECKED, PARTLY, NOT_CHECKED):
        raise ValueError(f"result must be {CHECKED!r}, {PARTLY!r} or {NOT_CHECKED!r}")
    if result in (CHECKED, PARTLY) and not content:
        raise ValueError("a checked result needs the gate's content hash")
    when = when or datetime.now(timezone.utc)
    from quam_state_manager.core.gate_parts import HASH_RULE
    ent = {"result": result, "reason": reason,
           "time": when.astimezone(timezone.utc).isoformat(timespec="seconds"),
           "hash": content, "rule": HASH_RULE}
    if result == PARTLY:
        ent["not_run"] = list(not_run or [])
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


def _short(text: Any) -> str:
    t = str(text or "").strip()
    t = re.sub(r"^(Not checked|Not compared)[:.]\s*", "", t).rstrip(". ")
    return (t[:1].lower() + t[1:]) if t else "no reason given"


def outcome(result: dict | None, entry: dict | None, *,
            reasons: dict | None = None) -> dict:
    """The ONE judgement of a gate's check: the dialog's banner, summary and
    chips, the check record and the status row all read it.

    *result* is a :func:`gate_check.check_gates` result, *entry* its entry for
    the gate; *reasons* may give a shorter reason per check name. Returns
    ``{"kind", "passed", "total", "not_run": [{check, label, reason}],
    "failed": [{check, label, reason}], "reason", "summary"}``:

    - ``checked``: the lab's code ran the gate and every check passed;
    - ``partly``: the gate ran, the checks that ran passed, the ones in
      ``not_run`` could not run (not a failure);
    - ``failed``: at least one check failed;
    - ``none``: the lab's code did not run the gate (``reason`` says why).
    """
    reasons = reasons or {}
    out = {"kind": KIND_NONE, "passed": 0, "total": 0, "not_run": [], "failed": [],
           "reason": None, "summary": None}
    result = result if isinstance(result, dict) else {}
    checks = (entry or {}).get("checks") or {}
    checks = {name: c for name, c in checks.items() if _counted(name, c)}
    out["total"] = len(checks)
    for name, c in checks.items():
        if isinstance(c, dict) and c.get("status") == "fail":
            out["failed"].append({"check": name, "label": CHECK_LABELS.get(name, name),
                                  "reason": str(c.get("message") or "A check failed.")})
    if out["failed"]:
        out["kind"] = KIND_FAILED
        out["reason"] = out["failed"][0]["reason"]
        out["summary"] = (f"{len(out['failed'])} of {out['total']} checks failed: "
                          + ", ".join(f["label"] for f in out["failed"]) + ".")
        return out
    if (not entry or result.get("status") != "ok" or not result.get("checked")
            or not entry.get("ran")):
        ops = checks.get("ops") or {}
        out["reason"] = (result.get("why_not_checked") or result.get("error")
                         or (ops.get("message") if isinstance(ops, dict) else None)
                         or "no check ran")
        out["summary"] = "Not checked with the lab's code: " + _short(out["reason"]) + "."
        return out
    unchecked = set(entry.get("unchecked") or []) | set(entry.get("partly_checked") or [])
    for name, c in checks.items():
        c = c if isinstance(c, dict) else {}
        if (name in unchecked or c.get("status") == "skipped" or c.get("not_run")
                or c.get("partly")):
            why = reasons.get(name) or (
                "only some operations were compared"
                if c.get("partly") and c.get("status") != "skipped" else _short(c.get("message")))
            out["not_run"].append({"check": name, "label": CHECK_LABELS.get(name, name),
                                   "reason": why})
        elif c.get("status") in ("pass", "note"):
            out["passed"] += 1
    if not out["not_run"]:
        out["kind"] = KIND_CHECKED
        out["summary"] = "every check passed."
        return out
    out["kind"] = KIND_PARTLY
    n, p = len(out["not_run"]), out["passed"]
    out["reason"] = "; ".join(f"{x['label']} ({x['reason']})" for x in out["not_run"])
    out["summary"] = (f"{p} {'check' if p == 1 else 'checks'} passed, {n} could not run: "
                      f"{out['reason']}.")
    return out


def result_of(check: dict | None, entry: dict | None = None) -> tuple[str, str | None]:
    """``(result, reason)`` for the record, from an :func:`outcome` (a dict
    with ``kind``) or from a check result and its gate entry (judged by
    :func:`outcome`)."""
    if not isinstance(check, dict):
        return NOT_CHECKED, "no check ran"
    oc = check if "kind" in check else outcome(check, entry)
    if oc["kind"] == KIND_CHECKED:
        return CHECKED, None
    if oc["kind"] == KIND_PARTLY:
        return PARTLY, oc["reason"]
    return NOT_CHECKED, oc["reason"] or "the lab check did not run"


def record_check(history_manager, chip_path, merged: dict, pair: str, gate: str,
                 check: dict | None) -> dict:
    """The one call a door makes after it wrote a gate (or made it active):
    records *check* against the gate's content as it is now. Never raises:
    ``{"ok": True, "entry": ...}`` or ``{"ok": False, "error": text}`` for the
    door to show ("the gate was written, the check record was not saved")."""
    try:
        if isinstance(check, dict) and "kind" not in check:
            entry = next((g for g in check.get("gates") or []
                          if g.get("pair") == pair and g.get("gate") == gate), None)
            check = outcome(check, entry)
        result, reason = result_of(check)
        content = content_hash(merged, pair, gate)
        if result in (CHECKED, PARTLY) and not content:
            result, reason = NOT_CHECKED, "the gate's content could not be read"
        labels = [x["label"] for x in (check or {}).get("not_run") or []]
        ent = record(chip_dir(history_manager, chip_path), pair, gate, result=result,
                     reason=reason, content=content, not_run=labels)
        return {"ok": True, "entry": ent}
    except Exception as exc:  # noqa: BLE001 -- a typed failure, never a raise
        return {"ok": False, "error": str(exc) or type(exc).__name__}
