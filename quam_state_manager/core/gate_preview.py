"""SM's own drawing of a gate's pulses, for the gate check's preview comparison.

Two sources, one drawing rule (:func:`draw`):

- a planned gate: the pulses the plan would write (``web/cz_gate_routes``);
- a gate already on the chip (Make active, Re-check): the pulses the chip
  stores at every operation the lab's code played (:func:`chip_previews`).

Only a class whose waveform SM transcribed is drawn
(``pulse_catalog.spec_at_known_home``); any other pulse is named as skipped,
so the check says it was not compared instead of claiming it was.

The config element name of a pair's coupler follows quam's ``Channel.name``
(``quam/components/channels.py``): the channel's ``id`` when that is text,
else ``<parent name>.<attribute>``; a pair's name is its ``id``, else its key.
"""
from __future__ import annotations

from typing import Any, Callable

from quam_state_manager.core import flux_dc, pulse_catalog
from quam_state_manager.core.pointer_resolver import _get

def coupler_element(merged: dict, pair: str) -> str | None:
    """The config element name of the coupler stored on pair *pair*, or None."""
    pairs = merged.get("qubit_pairs") if isinstance(merged, dict) else None
    entry = pairs.get(pair) if isinstance(pairs, dict) else None
    if not isinstance(entry, dict):
        return None
    path, why = flux_dc.real_path(merged, ("qubit_pairs", pair, "coupler"))
    node = _get(merged, list(path)) if path and not why else None
    if isinstance(node, dict) and isinstance(node.get("id"), str) and node["id"]:
        return node["id"]
    pid = entry.get("id")
    return f"{pid if isinstance(pid, str) and pid else pair}.coupler"


def draw(qclass: Any, payload_of: Callable[[], dict], length: Any = None, *,
         every_sample: bool = False) -> tuple[Any, str | None]:
    """``(preview, None)`` or ``(None, why it was not drawn)``. *payload_of*
    runs ``waveform_synth`` for the pulse; *every_sample*: a constant pulse
    gives all *length* samples, so the comparison reads the length too."""
    if not isinstance(qclass, str) or pulse_catalog.spec_at_known_home(qclass) is None:
        return None, f"SM cannot draw the pulse class {qclass} itself."
    try:
        pay = payload_of()
    except Exception as exc:  # noqa: BLE001
        return None, f"SM could not draw it ({type(exc).__name__})."
    if not pay.get("ok") or pay.get("truncated"):
        return None, pay.get("error") or "SM could not draw it in full."
    cv = pay.get("constant_value")
    if isinstance(cv, (int, float)) and not isinstance(cv, bool):
        if every_sample and isinstance(length, int) and not isinstance(length, bool):
            return [float(cv)] * length, None
        return float(cv), None
    return list(pay.get("i") or []), None


def _line_path(merged: dict, element: str) -> tuple | None:
    """The chip path of the line whose config element is *element*: a qubit
    component (``<qubit name>.<component>``) or a pair's coupler."""
    qubits = merged.get("qubits") if isinstance(merged, dict) else None
    q, dot, comp = element.partition(".")
    if dot and comp and "." not in comp and isinstance(qubits, dict):
        for key, node in qubits.items():
            if not isinstance(node, dict):
                continue
            name = node.get("id") if isinstance(node.get("id"), str) else key
            if name == q and isinstance(node.get(comp), (dict, str)):
                return ("qubits", key, comp)
    pairs = merged.get("qubit_pairs") if isinstance(merged, dict) else None
    for key in (pairs or {}) if isinstance(pairs, dict) else ():
        if coupler_element(merged, key) == element:
            return ("qubit_pairs", key, "coupler")
    return None


def chip_previews(store, found: list) -> tuple[dict, list[str]]:
    """``({(element, operation): preview}, [why skipped])`` for every played
    operation *found* (the lab check's ``found`` entries), drawn from the
    pulse the chip stores there."""
    from quam_state_manager.core import waveform_synth
    out: dict = {}
    skipped: list[str] = []
    for info in found or []:
        el, op = info.get("element"), info.get("operation")
        if not isinstance(el, str) or not isinstance(op, str):
            continue
        with store._lock:
            merged = store.merged
            line = _line_path(merged, el)
            ops_path = flux_dc.landed_path(merged, line + ("operations",)) if line else None
            pulse_path = flux_dc.landed_path(merged, ops_path + (op,)) if ops_path else None
            pulse = _get(merged, list(pulse_path)) if pulse_path else None
        if not isinstance(pulse, dict):
            skipped.append(f"{op} on {el}: SM does not find this pulse on the chip.")
            continue
        dotted = ".".join(str(s) for s in pulse_path)
        value, why = draw(pulse.get("__class__"),
                          lambda p=dotted: waveform_synth.synth_for_operation(store, p))
        if why:
            skipped.append(f"{op} on {el}: {why}")
            continue
        out[(el, op)] = value
    return out, skipped
