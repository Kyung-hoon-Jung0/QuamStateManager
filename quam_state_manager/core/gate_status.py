"""The one status line of a CZ gate: what state the gate is in, item by item.

:func:`status` returns a list of ``{key, text, tone, why}`` items, in this
order:

1. ``where``: "only in the working copy" / "on the chip" (the macro path is in
   the live files, read through the shared live read) / "cannot tell: ...";
2. ``active``: "active" / "not active" (``query.pair_operational_gate``);
3. ``class``: for a gate whose ``extras.sm_class_files`` names SM-made
   classes, the class files compared with the files in the folder they were
   published to: "class file present" / "class file missing:
   quam_config/<file>.py" / "class file changed since the gate was checked"
   / "class file missing" for a record that names no file or carries no hash (the
   live-write check refuses it too); else "not a class SM made"; "class <name> not importable in <env>" when
   the selected env cannot import the gate's class (a missing or changed
   file is said first);
4. ``scaling``: "amplitude scaling: checked" / "not proportional" / "not
   known", from the recorded ``proportional`` of the SM-made classes;
5. ``spectators`` (only for a gate with spectators): "spectator phases not
   calibrated" when their phase shifts are all 0;
6. ``check``: "checked with the lab's code <when>" / "not checked: <reason>"
   / "values changed since the check" / "no check record".

A gate with SM-made classes also carries one ``needs`` item per file, the
notice that the chip needs that file (:data:`NEEDS_NOTICE`).

Tones: ``ok``, ``accent``, ``neutral``, ``warn`` (only what needs action).
Each item carries ``pill``: True for an item that says something about this
gate (shown as a pill), False for a default every gate shares (shown only in
the line's details). Nothing here
raises on chip shape; SM never imports the QM stack.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from quam_state_manager.core import gate_check_log
from quam_state_manager.core.gate_parts import active_gate, content_hash
from quam_state_manager.core.query import pair_operational_gate


#: the notice of a chip that needs a class file SM made, kept for as long as a
#: gate records the file
NEEDS_NOTICE = ("this chip now needs quam_config/{file}; every computer that opens this "
                "state needs the same file")
FILE_PRESENT = "class file present"
FILE_MISSING = "class file missing: quam_config/{file}"
FILE_CHANGED = "class file changed since the gate was checked"
NOT_SM_CLASS = "not a class SM made"
SCALING_CHECKED = "amplitude scaling: checked"
SCALING_NOT = "amplitude scaling: not proportional"
SCALING_UNKNOWN = "amplitude scaling: not known"


def class_file_records(macro: Any) -> list[dict]:
    """Every class file record a gate macro carries under
    ``extras.sm_class_files`` (``[{qclass, file, sha256, proportional}]``),
    raw: a record without a usable file name is kept, so the live-write check
    can refuse it. Entries that are not dicts are dropped; [] for a gate
    without records. The one parser of the records."""
    extras = macro.get("extras") if isinstance(macro, dict) else None
    files = extras.get("sm_class_files") if isinstance(extras, dict) else None
    return [f for f in files if isinstance(f, dict)] if isinstance(files, list) else []


def names_file(record: dict) -> bool:
    """True when *record* names its file (a non-empty string)."""
    name = record.get("file")
    return isinstance(name, str) and bool(name)


def has_hash(record: dict) -> bool:
    """True when *record* carries a non-empty string content hash."""
    digest = record.get("sha256")
    return isinstance(digest, str) and bool(digest)


def sm_class_files(macro: Any) -> list[dict]:
    """The records of :func:`class_file_records` that name their file."""
    return [f for f in class_file_records(macro) if names_file(f)]


def file_state(entry: dict, path: str | None) -> str:
    """``"present"`` / ``"missing"`` / ``"changed"`` of one recorded class
    file: the bytes at *path* (where it was published) hashed and compared
    with the recorded sha256 (letter case ignored, as the live-write check)."""
    if not path:
        return "missing"
    try:
        data = Path(path).read_bytes()
    except OSError:
        return "missing"
    digest = entry.get("sha256")
    digest = digest.lower() if isinstance(digest, str) else digest
    return "present" if hashlib.sha256(data).hexdigest() == digest else "changed"


def class_file_states(macro: Any, where) -> list[tuple[dict, str]]:
    """``(record, state)`` of every raw record of *macro*: :func:`file_state`
    at ``where(qclass)`` (the published path), "missing" for a record that
    names no file or carries no hash. The status line and Make active both read this."""
    return [(f, file_state(f, where(f.get("qclass")))
             if names_file(f) and has_hash(f) else "missing")
            for f in class_file_records(macro)]


def _class_files(states: list[tuple[dict, str]]) -> dict:
    files = [f for f, _ in states]
    if not all(names_file(f) for f in files):
        return _item("class", "class file missing", "warn",
                     "its class file record has no file name")
    if not all(has_hash(f) for f in files):
        return _item("class", "class file missing", "warn",
                     "its class file record has no content hash")
    gone = [f for f, st in states if st == "missing"]
    moved = [f for f, st in states if st == "changed"]
    if gone:
        if len(gone) == 1:
            name = gone[0]["file"]
            return _item("class", FILE_MISSING.format(file=name), "warn",
                         f"The gate uses {gone[0].get('qclass') or name}, and its file is not in "
                         "the folder it was saved to.")
        names = [f["file"] for f in gone]
        return _item("class", FILE_MISSING.format(file=", quam_config/".join(names)), "warn",
                     "The gate uses " + ", ".join(f.get("qclass") or f["file"] for f in gone)
                     + ", and their files are not in the folder they were saved to.")
    if moved:
        return _item("class", FILE_CHANGED, "warn",
                     "The file differs from the one the gate was checked with: "
                     + ", ".join(f["file"] for f in moved) + ". Re-check runs the lab check "
                     "with the file as it is now.")
    return _item("class", FILE_PRESENT, "ok",
                 "Same file as when the gate was checked: "
                 + ", ".join(f["file"] for f in files) + ".", pill=False)


def _scaling(files: list[dict]) -> dict:
    flags = [f.get("proportional") for f in files]
    if any(v is False for v in flags):
        return _item("scaling", SCALING_NOT, "warn",
                     "An SM-made pulse class of this gate does not scale with amplitude.")
    if any(v is True for v in flags):
        return _item("scaling", SCALING_CHECKED, "ok",
                     "The SM-made pulse classes were checked to scale with amplitude.")
    return _item("scaling", SCALING_UNKNOWN, "neutral",
                 "SM does not know whether the waveform scales with amplitude.", pill=False)


def _item(key: str, text: str, tone: str = "neutral", why: str = "", *,
          pill: bool = True) -> dict:
    """One item; ``pill`` says whether the line shows it as a pill (an item
    that says something) or only in the line's details."""
    return {"key": key, "text": text, "tone": tone, "why": why, "pill": pill}


def _at(doc: Any, dot: str) -> tuple[bool, Any]:
    from quam_state_manager.core.working_copy import _at_dot_path
    return _at_dot_path(doc, dot)


def _where(ctx: dict | None, mpath: str) -> dict:
    wc = (ctx or {}).get("working_copy") if isinstance(ctx, dict) else None
    if wc is None:
        return _item("where", "cannot tell: no live chip", "neutral",
                     "This chip is not opened from live files.")
    try:
        from quam_state_manager.core import working_copy
        pair = working_copy.read_live_shared(wc)
    except Exception as exc:  # noqa: BLE001 -- an unreadable live pair is a line
        return _item("where", f"cannot tell: {type(exc).__name__}", "neutral",
                     f"The live files could not be read: {exc}")
    for doc in (getattr(pair, "state", None), getattr(pair, "wiring", None)):
        if isinstance(doc, dict) and _at(doc, mpath)[0]:
            return _item("where", "on the chip", "ok", "The gate is in the live files.")
    return _item("where", "only in the working copy", "neutral",
                 "Apply sends it to the chip.")


def _spectators(macro: dict) -> dict | None:
    spect = macro.get("spectator_qubits")
    if not isinstance(spect, dict) or not spect:
        return None
    phases = macro.get("spectator_qubits_phase_shift")
    values = list(phases.values()) if isinstance(phases, dict) else []
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) and v == 0
           for v in values):
        return _item("spectators", "spectator phases not calibrated", "warn",
                     "Every spectator phase shift is 0.")
    return None


def _when(text: str) -> str:
    return str(text or "").replace("T", " ")[:16]


def _check(folder, merged: dict, pair: str, gate: str) -> dict:
    ent = gate_check_log.entry(folder, pair, gate) if folder is not None else None
    if not ent:
        return _item("check", "no check record", "neutral",
                     "This gate was not made or made active in SM.", pill=False)
    when = _when(ent.get("time"))
    if ent.get("result") == gate_check_log.CHECKED and not ent.get("hash"):
        return _item("check", "checked, cannot tell whether values changed", "warn",
                     f"Checked with the lab's code {when} UTC; the record holds no copy "
                     "of the values it checked.")
    from quam_state_manager.core.gate_parts import HASH_RULE
    if (ent.get("result") == gate_check_log.CHECKED and ent.get("hash")
            and ent.get("rule") != HASH_RULE):
        return _item("check", "checked with an older rule: check again", "neutral",
                     f"Checked {when} UTC; SM now compares more of the gate than when "
                     "this was recorded, so it cannot say whether the values changed.")
    if ent.get("result") in (gate_check_log.CHECKED, gate_check_log.PARTLY):
        # only a passed check is compared with the values now
        if content_hash(merged, pair, gate) != ent.get("hash"):
            return _item("check", "values changed since the check", "warn",
                         f"Checked with the lab's code {when} UTC; the gate changed after.")
        if ent.get("result") == gate_check_log.PARTLY:
            n = len(ent.get("not_run") or []) or 1
            return _item("check", f"checked, {n} not run", "ok",
                         f"Checked with the lab's code {when} UTC; the checks that ran passed. "
                         f"{n} could not run: {ent.get('reason') or 'no reason given'}.")
        return _item("check", "checked", "ok", f"Checked with the lab's code {when} UTC.")
    return _item("check", "not checked", "warn",
                 f"Not checked with the lab's code: {ent.get('reason') or 'no reason given'} "
                 f"({when} UTC).")


def status(store, ctx: dict | None, pair: str, gate: str, *,
           check_folder=None, missing_classes=None, env_name: str | None = None,
           class_path=None) -> list[dict]:
    """The status items of gate *gate* of pair *pair* (see the module doc).

    *check_folder*: the chip's folder of the check record
    (:func:`gate_check_log.chip_dir`); None reads as "no check record".
    *missing_classes*: the selected env's classes it cannot import (the env
    manifest), with *env_name* for the text.
    *class_path*: ``qclass -> path`` of the file Save to quam_config
    published (:func:`quam_config_save.published_path`); without it every
    recorded class file reads as missing."""
    merged = getattr(store, "merged", None)
    merged = merged if isinstance(merged, dict) else {}
    pairs = merged.get("qubit_pairs")
    pobj = pairs.get(pair) if isinstance(pairs, dict) else None
    macros = pobj.get("macros") if isinstance(pobj, dict) else None
    macro = macros.get(gate) if isinstance(macros, dict) else None
    if not isinstance(macro, dict):
        return [_item("gone", "not on this chip", "neutral",
                      f"Pair {pair} has no gate {gate}.")]
    mpath = f"qubit_pairs.{pair}.macros.{gate}"
    items = [_where(ctx, mpath)]
    if active_gate(pair, macros) == gate:
        items.append(_item("active", "active", "accent", "The pair's cz alias names this gate."))
    else:
        items.append(_item("active", "not active", "neutral",
                           "The pair's cz alias names another gate or none.", pill=False))
    cls = str(macro.get("__class__") or "")
    files = sm_class_files(macro)
    states = class_file_states(macro, class_path or (lambda q: None))
    file_item = _class_files(states) if states else None
    if file_item is not None and file_item["tone"] == "warn":
        items.append(file_item)
    elif cls and missing_classes and cls in set(missing_classes):
        items.append(_item("class", f"class {cls.rsplit('.', 1)[-1]} not importable in "
                           f"{env_name or 'the selected env'}", "warn", cls))
    elif file_item is not None:
        items.append(file_item)
    else:
        items.append(_item("class", NOT_SM_CLASS, "neutral", cls, pill=False))
    items.append(_scaling(files))
    for f in files:
        items.append(_item("needs", NEEDS_NOTICE.format(file=f["file"]), "neutral",
                           f"{f.get('qclass') or f['file']} is a class file SM made.",
                           pill=False))
    sp = _spectators(macro)
    if sp is not None:
        items.append(sp)
    check = _check(check_folder, merged, pair, gate)
    items.append(check)
    # only a completed check has a scope to state
    if check["text"].startswith("checked") or check["text"] == "values changed since the check":
        items.append(_item("scope", "what the check covers", "neutral",
                       "The check covered this gate's pulses. Later changes to a port's "
                       "offset, range or delay are not tracked here.", pill=False))
    return items


def cz_gates(merged: dict, pair: str) -> list[str]:
    """The pair's CZ gates (macro dicts, the alias excluded), in state order."""
    from quam_state_manager.core.cz_gate_build import is_cz_macro
    pairs = merged.get("qubit_pairs") if isinstance(merged, dict) else None
    pobj = pairs.get(pair) if isinstance(pairs, dict) else None
    macros = pobj.get("macros") if isinstance(pobj, dict) else None
    if not isinstance(macros, dict):
        return []
    return [str(g) for g, m in macros.items() if is_cz_macro(str(g), m)]


def gates_to_recheck(merged: Any, where) -> list[dict]:
    """Every gate of the chip, over all pairs, whose status line says
    :data:`FILE_CHANGED` (the gates that wait for Re-check), in state order:
    ``[{pair, gate, files}]`` with *files* the recorded file names whose bytes
    changed. *where* is ``qclass -> path`` as for :func:`status`. Read through
    :func:`class_file_states`, so a missing file or a bad record is not
    listed here (the gate's own line says it); a gate that cannot be read is
    skipped, never raised."""
    pairs = merged.get("qubit_pairs") if isinstance(merged, dict) else None
    out: list[dict] = []
    if not isinstance(pairs, dict):
        return out
    for pair, pobj in pairs.items():
        macros = pobj.get("macros") if isinstance(pobj, dict) else None
        if not isinstance(macros, dict):
            continue
        for gate, macro in macros.items():
            try:
                states = class_file_states(macro, where)
                if not states or _class_files(states)["text"] != FILE_CHANGED:
                    continue
                files = [str(f["file"]) for f, st in states if st == "changed"]
            except Exception:  # noqa: BLE001 -- one unreadable gate never hides the rest
                continue
            out.append({"pair": str(pair), "gate": str(gate), "files": files})
    return out
