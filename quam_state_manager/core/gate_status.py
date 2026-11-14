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

Tones: ``ok``, ``accent``, ``neutral``, ``warn`` (only what needs action).
Each item carries ``pill``: True for an item that says something about this
gate (shown as a pill), False for a default every gate shares (shown only in
the line's details). Nothing here
raises on chip shape; SM never imports the QM stack.
"""
from __future__ import annotations

from typing import Any

from quam_state_manager.core import gate_check_log
from quam_state_manager.core.gate_parts import active_gate, content_hash
from quam_state_manager.core.query import pair_operational_gate


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
    now = content_hash(merged, pair, gate)
    if ent.get("hash") and now != ent.get("hash"):
        return _item("check", "values changed since the check", "warn",
                     f"Checked with the lab's code {when} UTC; the gate changed after.")
    if ent.get("result") == gate_check_log.CHECKED:
        return _item("check", "checked", "ok", f"Checked with the lab's code {when} UTC.")
    return _item("check", "not checked", "warn",
                 f"Not checked with the lab's code: {ent.get('reason') or 'no reason given'} "
                 f"({when} UTC).")


def status(store, ctx: dict | None, pair: str, gate: str, *,
           check_folder=None, missing_classes=None, env_name: str | None = None
           ) -> list[dict]:
    """The status items of gate *gate* of pair *pair* (see the module doc).

    *check_folder*: the chip's folder of the check record
    (:func:`gate_check_log.chip_dir`); None reads as "no check record".
    *missing_classes*: the selected env's classes it cannot import (the env
    manifest), with *env_name* for the text."""
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
    if cls and missing_classes and cls in set(missing_classes):
        items.append(_item("class", f"class {cls.rsplit('.', 1)[-1]} not importable in "
                           f"{env_name or 'the selected env'}", "warn", cls))
    else:
        items.append(_item("class", "not a class SM made", "neutral", cls, pill=False))
    items.append(_item("scaling", "amplitude scaling: not known", "neutral",
                       "SM does not yet test whether the waveform scales with amplitude.",
                       pill=False))
    sp = _spectators(macro)
    if sp is not None:
        items.append(sp)
    items.append(_check(check_folder, merged, pair, gate))
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
