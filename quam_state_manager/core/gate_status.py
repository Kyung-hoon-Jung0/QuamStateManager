"""The one status line of a CZ gate: what state the gate is in, item by item.

:func:`status` returns a list of ``{key, text, tone, why}`` items, in this
order:

1. ``where``: "only in the working copy" / "on the chip" (the macro path is in
   the live files, read through the shared live read) / "cannot tell: ...";
2. ``active``: "active" / "not active" (``query.pair_operational_gate``);
3. ``class``: "not a class SM made", or "class <name> not importable in
   <env>" when the selected env cannot import the gate's class;
4. ``scaling``: "amplitude scaling: not known";
5. ``spectators`` (only for a gate with spectators): "spectator phases not
   calibrated" when their phase shifts are all 0;
6. ``check``: "checked with the lab's code <when>" / "not checked: <reason>"
   / "values changed since the check" / "no check record".

Tones: ``ok``, ``neutral``, ``warn`` (only what needs action). Nothing here
raises on chip shape; SM never imports the QM stack.
"""
from __future__ import annotations

from typing import Any

from quam_state_manager.core import gate_check_log
from quam_state_manager.core.gate_parts import content_hash
from quam_state_manager.core.query import pair_operational_gate


def _item(key: str, text: str, tone: str = "neutral", why: str = "") -> dict:
    return {"key": key, "text": text, "tone": tone, "why": why}


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
    if all(isinstance(v, (int, float)) and v == 0 for v in values):
        return _item("spectators", "spectator phases not calibrated", "warn",
                     "Every spectator phase shift is 0.")
    return None


def _when(text: str) -> str:
    return str(text or "").replace("T", " ")[:16]


def _check(folder, merged: dict, pair: str, gate: str) -> dict:
    ent = gate_check_log.entry(folder, pair, gate) if folder is not None else None
    if not ent:
        return _item("check", "no check record", "neutral",
                     "This gate was not made or made active in SM.")
    now = content_hash(merged, pair, gate)
    if ent.get("hash") and now != ent.get("hash"):
        return _item("check", "values changed since the check", "warn",
                     f"Checked {_when(ent.get('time'))} UTC; the gate changed after.")
    if ent.get("result") == gate_check_log.CHECKED:
        return _item("check", f"checked with the lab's code {_when(ent.get('time'))}",
                     "ok", "UTC time of the last check.")
    return _item("check", f"not checked: {ent.get('reason') or 'no reason given'}",
                 "warn", f"Recorded {_when(ent.get('time'))} UTC.")


def status(store, ctx: dict | None, pair: str, gate: str, *,
           check_folder=None, missing_classes=None, env_name: str | None = None
           ) -> list[dict]:
    """The status items of gate *gate* of pair *pair* (see the module doc).

    *check_folder*: the chip's folder of the check record
    (:func:`gate_check_log.chip_dir`); None reads as "no check record".
    *missing_classes*: the selected env's classes it cannot import (the env
    manifest), with *env_name* for the text."""
    merged = getattr(store, "merged", None) or {}
    pobj = (merged.get("qubit_pairs") or {}).get(pair) if isinstance(merged, dict) else None
    macros = pobj.get("macros") if isinstance(pobj, dict) else None
    macro = macros.get(gate) if isinstance(macros, dict) else None
    if not isinstance(macro, dict):
        return [_item("gone", "not on this chip", "neutral",
                      f"Pair {pair} has no gate {gate}.")]
    mpath = f"qubit_pairs.{pair}.macros.{gate}"
    items = [_where(ctx, mpath)]
    if pair_operational_gate(pair, macros) == gate:
        items.append(_item("active", "active", "ok", "The pair's cz alias names this gate."))
    else:
        items.append(_item("active", "not active", "neutral",
                           "The pair's cz alias names another gate or none."))
    cls = str(macro.get("__class__") or "")
    if cls and missing_classes and cls in set(missing_classes):
        items.append(_item("class", f"class {cls.rsplit('.', 1)[-1]} not importable in "
                           f"{env_name or 'the selected env'}", "warn", cls))
    else:
        items.append(_item("class", "not a class SM made", "neutral", cls))
    items.append(_item("scaling", "amplitude scaling: not known", "neutral",
                       "SM does not yet test whether the waveform scales with amplitude."))
    sp = _spectators(macro)
    if sp is not None:
        items.append(sp)
    items.append(_check(check_folder, merged, pair, gate))
    return items


def cz_gates(merged: dict, pair: str) -> list[str]:
    """The pair's CZ gates (macro dicts, the alias excluded), in state order."""
    from quam_state_manager.core.cz_gate_build import is_cz_macro
    pobj = (merged.get("qubit_pairs") or {}).get(pair) if isinstance(merged, dict) else None
    macros = pobj.get("macros") if isinstance(pobj, dict) else None
    if not isinstance(macros, dict):
        return []
    return [str(g) for g, m in macros.items() if is_cz_macro(str(g), m)]
