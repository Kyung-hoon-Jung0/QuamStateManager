"""User acknowledgements of Diagnostics warnings and suggestions (FB-03).

A person can say "I know - intended" about a warning or a suggestion. The
acknowledgement is stored per chip in SM's own instance folder (never in
``state.json``) and is tied to the VALUES the finding was about: each record
keeps a fingerprint of the finding's message, detail and the current values at
every path it names. When any of those change, the fingerprint no longer
matches and the finding counts again -- a new mismatch is not the one the user
accepted.

Errors are never acknowledgeable here (decision D-3): an error is a value that
breaks a run, and silencing it would hide that.

Environment-match findings keep their own acknowledgement door (``env_ack``);
this module leaves any finding that already carries an ``ack_key`` alone.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Iterable

_LOCK = threading.Lock()
ACKABLE_SEVERITIES = ("warning", "info", "recommendation")


def _file(instance_path: str | os.PathLike, chip_key: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in chip_key) or "chip"
    return Path(instance_path) / "diag_acks" / f"{safe}.json"


def _read(instance_path, chip_key: str) -> dict:
    try:
        data = json.loads(_file(instance_path, chip_key).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load(instance_path, chip_key: str) -> dict:
    """``{identity: {"fp": str, "at": float, "message": str, "paths": [...]}}``;
    empty on any read error."""
    acks = _read(instance_path, chip_key).get("acks")
    return acks if isinstance(acks, dict) else {}


def pruned_count(instance_path, chip_key: str) -> int:
    """How many records were dropped because their place left the chip."""
    n = _read(instance_path, chip_key).get("pruned")
    return n if isinstance(n, int) else 0


def _place_gone(rec: dict, store) -> bool:
    """True only when every chip path the record was about is missing now.
    A record with no chip paths (a generated-config or report finding) is
    never judged gone here."""
    paths = rec.get("paths") if isinstance(rec, dict) else None
    if store is None or not isinstance(paths, list) or not paths:
        return False
    for path in paths:
        try:
            store.get_value(path)
            return False
        except Exception:  # noqa: BLE001 -- missing
            continue
    return True


def prune(instance_path, chip_key: str, store) -> int:
    """Drop acknowledgements whose place no longer exists on the chip; keep
    only a count of them (a tombstone), never their content."""
    with _LOCK:
        data = _read(instance_path, chip_key)
        acks = data.get("acks") if isinstance(data.get("acks"), dict) else {}
        gone = [k for k, rec in acks.items() if _place_gone(rec, store)]
        if not gone:
            return 0
        for k in gone:
            acks.pop(k, None)
        _save(instance_path, chip_key, acks, pruned=pruned_count(instance_path, chip_key) + len(gone))
        return len(gone)


def _save(instance_path, chip_key: str, acks: dict, pruned: int | None = None) -> None:
    path = _file(instance_path, chip_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".diag_ack_", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            if pruned is None:
                pruned = pruned_count(instance_path, chip_key)
            json.dump({"version": 1, "acks": acks, "pruned": pruned}, fh, indent=1, sort_keys=True)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def identity(finding) -> str:
    """Which finding this is: its check id and its place (not its values)."""
    raw = "|".join((finding.category or "", finding.location or "", finding.jump_path or ""))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def _fix_paths(finding) -> list[str]:
    out = []
    for fix in [finding.fix, *(getattr(finding, "fixes", None) or [])]:
        if isinstance(fix, dict):
            if fix.get("dot_path"):
                out.append(fix["dot_path"])
            for edit in fix.get("edits") or []:
                if isinstance(edit, dict) and edit.get("path"):
                    out.append(edit["path"])
    return out


def value_paths(finding) -> list[str]:
    """The chip leaves the check read: its field, its fixes' targets and any
    ``value_paths`` the check declared."""
    return sorted({p for p in [finding.jump_path, *_fix_paths(finding),
                               *(getattr(finding, "value_paths", None) or [])] if p})


def has_value_source(finding) -> bool:
    """A value change can bring this finding back: it names chip leaves or
    carries the non-chip inputs it read (``values``)."""
    return bool(value_paths(finding)) or getattr(finding, "values", None) is not None


def fingerprint(finding, store=None) -> str:
    """The values the finding was about. Changes whenever any of them changes."""
    values: list[Any] = []
    paths = value_paths(finding)
    for path in paths:
        try:
            value = store.get_value(path) if store is not None else None
        except Exception:  # noqa: BLE001 -- a missing path is a value too
            value = "<missing>"
        values.append([path, value])
    raw = json.dumps([finding.message, finding.detail, values, getattr(finding, "values", None)],
                     sort_keys=True, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def ackable(finding) -> bool:
    return (finding.severity in ACKABLE_SEVERITIES and not getattr(finding, "ack_key", "")
            and has_value_source(finding))


def stamp(findings: Iterable, instance_path, chip_key: str | None, store=None) -> None:
    """Give every ackable finding its ``vack_key`` and mark the acknowledged ones."""
    if chip_key and store is not None:
        try:
            prune(instance_path, chip_key, store)
        except OSError:
            pass
    acks = load(instance_path, chip_key) if chip_key else {}
    for f in findings:
        if not ackable(f):
            continue
        f.vack_key = identity(f)
        rec = acks.get(f.vack_key)
        if rec and rec.get("fp") == fingerprint(f, store):
            f.acknowledged = {"at": rec.get("at"), "by": "you", "kind": "value"}


def acknowledge(instance_path, chip_key: str, findings: Iterable, keys: Iterable[str], store=None) -> int:
    """Record the CURRENT fingerprint of each named finding. Returns how many."""
    wanted = set(keys)
    with _LOCK:
        acks = load(instance_path, chip_key)
        n = 0
        for f in findings:
            if not ackable(f):
                continue
            key = identity(f)
            if key in wanted:
                acks[key] = {"fp": fingerprint(f, store), "at": time.time(),
                             "category": f.category, "location": f.location,
                             "paths": value_paths(f),
                             "message": f.message[:300]}
                n += 1
        if n:
            _save(instance_path, chip_key, acks)
        return n


def revoke(instance_path, chip_key: str, keys: Iterable[str]) -> int:
    with _LOCK:
        acks = load(instance_path, chip_key)
        n = 0
        for key in keys:
            if acks.pop(key, None) is not None:
                n += 1
        if n:
            _save(instance_path, chip_key, acks)
        return n
