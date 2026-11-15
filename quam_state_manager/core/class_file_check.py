"""The class-file check of a live write.

A gate macro that uses classes SM made carries ``extras.sm_class_files``: one
``{"qclass", "file", "sha256", "proportional"}`` per class file. A live write
that adds such a gate, or makes it the pair's active gate (the ``cz`` alias
moves to it), lands only when every listed file is in the folder the chip's
environment imports ``quam_config`` from and its bytes still hash to the
recorded sha256. Otherwise the write is refused with a reason naming the gate
and the file. Gates without the key, and every other edit, are not checked.

:func:`check_payload` is called from one place, ``working_copy.apply_to_live``,
so every door that writes the live chip passes through it.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Callable

from quam_state_manager.core import safe_io
from quam_state_manager.core.gate_status import class_file_records as sm_class_files

_SECTIONS = ("qubit_pairs", "qubits")

#: ``fn(live_folder) -> python path | None``: the environment of the chip
#: whose live folder is being written (registered by the web layer). None
#: means the chip has no environment chosen; a lookup that cannot find the
#: chip raises :class:`ClassFileRefusedError` naming why.
_env_source: Callable[[Path], str | None] | None = None


class ClassFileRefusedError(ValueError):
    """A live write was refused because a gate's class file is missing,
    changed, or cannot be located. Nothing was written."""


def set_env_source(fn: Callable[[Path], str | None] | None) -> None:
    """Register how a live folder maps to its chip's environment."""
    global _env_source
    _env_source = fn


def env_folder(python: str) -> Path:
    """The folder *python* imports ``quam_config`` from, asked without SM's
    lab code folder in front of the path."""
    from quam_state_manager.core import quam_config_save as Q
    from quam_state_manager.core.pulse_class_drafts import DraftError
    try:
        found = Q._run(python, {"action": "discover"}, natural=True)
    except (DraftError, OSError, ValueError) as exc:
        raise ClassFileRefusedError(str(exc)) from exc
    if not found.get("ok") or not found.get("folder"):
        raise ClassFileRefusedError(found.get("error") or "the environment cannot import quam_config")
    return Path(found["folder"]).resolve()


def _macros(doc: Any, section: str, owner: str) -> dict:
    sec = doc.get(section) if isinstance(doc, dict) else None
    obj = sec.get(owner) if isinstance(sec, dict) else None
    macros = obj.get("macros") if isinstance(obj, dict) else None
    return macros if isinstance(macros, dict) else {}


def has_class_files(doc: Any) -> bool:
    """True when any gate macro of *doc* records class files."""
    for section in _SECTIONS:
        owners = doc.get(section) if isinstance(doc, dict) else None
        if isinstance(owners, dict) and any(
                sm_class_files(m) for owner in owners
                for m in _macros(doc, section, owner).values()):
            return True
    return False


def gates_to_check(payload: dict, live: dict | None) -> list[tuple[str, str, list[dict]]]:
    """``(owner, gate, files)`` of every gate with recorded class files that
    *payload* adds (absent from *live*, or recorded with other files) or makes
    a pair's active gate where *live* had another one."""
    from quam_state_manager.core.gate_parts import active_gate
    out = []
    for section in _SECTIONS:
        owners = payload.get(section) if isinstance(payload, dict) else None
        if not isinstance(owners, dict):
            continue
        for owner in owners:
            macros = _macros(payload, section, owner)
            if not any(sm_class_files(m) for m in macros.values()):
                continue
            live_macros = _macros(live or {}, section, owner)
            active = active_gate(owner, macros) if section == "qubit_pairs" else None
            was_active = active_gate(owner, live_macros) if section == "qubit_pairs" else None
            for name, macro in macros.items():
                files = sm_class_files(macro)
                if not files:
                    continue
                added = name not in live_macros or sm_class_files(live_macros[name]) != files
                activated = name == active and was_active != name
                if added or activated:
                    out.append((owner, name, files))
    return out


def _file_problem(folder: Path, entry: dict) -> str | None:
    name = entry.get("file")
    digest = entry.get("sha256")
    if not isinstance(name, str) or not name or not isinstance(digest, str) or not digest:
        return "its class file record has no file name or content hash"
    path = folder / Path(name).name
    data = safe_io.scan_bytes(path)
    if data is None:
        return f"the class file {path} is missing"
    if hashlib.sha256(data).hexdigest() != digest.lower():
        return f"the class file {path} changed after the gate was made"
    return None


def check_payload(live_folder: Path, payload: dict, live: dict | None) -> None:
    """Raise :class:`ClassFileRefusedError` when *payload* adds or activates
    a gate whose class files are not in place; return None otherwise."""
    gates = gates_to_check(payload, live)
    if not gates:
        return
    owner, name, _files = gates[0]
    try:
        python = _env_source(Path(live_folder)) if _env_source is not None else None
    except ClassFileRefusedError as exc:
        raise ClassFileRefusedError(
            f"Not written to the live chip: gate {name} of {owner} uses a class file SM made, "
            f"and {exc}.") from exc
    if not python:
        raise ClassFileRefusedError(
            f"Not written to the live chip: gate {name} of {owner} uses a class file SM made, "
            "and no environment is selected, so SM cannot tell which quam_config folder to check.")
    try:
        folder = env_folder(python)
    except ClassFileRefusedError as exc:
        raise ClassFileRefusedError(
            f"Not written to the live chip: gate {name} of {owner} uses a class file SM made, "
            f"and SM cannot find the environment's quam_config folder ({exc}).") from exc
    for owner, name, files in gates:
        for entry in files:
            why = _file_problem(folder, entry)
            if why:
                raise ClassFileRefusedError(
                    f"Not written to the live chip: gate {name} of {owner} needs its class file, "
                    f"but {why}.")
