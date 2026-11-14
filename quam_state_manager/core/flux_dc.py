"""The DC level a flux line may sit at while a gate plays on it.

One answer for every range check that adds a DC level to a played pulse:
the gate check's own lines and the spectator lines of a planned gate.

Quantity: volts at the OPX output port, before any amplification stage
outside the port (the same quantity as the port's output range and as the
config's ``analog_outputs[n]["offset"]``).

Where it comes from, per the installed lab classes (read only):

- ``quam/components/channels.py`` ``SingleChannel.set_dc_offset`` calls
  ``qua.set_dc_offset(element=..., element_input="single", offset=...)``.
- ``quam_builder/architecture/superconducting/components/flux_line.py``
  ``FluxLine``: fields ``independent_offset``, ``joint_offset``,
  ``min_offset``, ``arbitrary_offset`` (default 0.0) and ``flux_point``
  (default ``"independent"``); ``to_joint_idle`` / ``to_independent_idle`` /
  ``to_min`` / ``to_zero`` call ``set_dc_offset`` with that field (or 0).
- The QM guide "Output Idle Values": ``set_dc_offset`` changes the current
  hardware value during a job; the config offset is the idle value.

So a run-time flux point REPLACES the port's idle offset, it does not add to
it. Whether the program sets the flux point before the gate is not known to
SM, so both levels are returned and a check must hold for each of them.
"""
from __future__ import annotations

import math
from typing import Any

from quam_state_manager.core.pointer_resolver import _SENTINEL, _get, _landing

#: flux_point -> the FluxLine field holding its offset (None: 0 V)
FLUX_POINT_FIELDS = {
    "joint": "joint_offset",
    "independent": "independent_offset",
    "min": "min_offset",
    "arbitrary": "arbitrary_offset",
    "zero": None,
}
#: the FluxLine default when flux_point is absent
DEFAULT_FLUX_POINT = "independent"
#: keys that mark a node as a FluxLine with run-time offsets
_FLUX_KEYS = ("flux_point",) + tuple(f for f in FLUX_POINT_FIELDS.values() if f)


def _number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def read_leaf(merged: dict, segs: tuple) -> tuple[Any, str | None]:
    """``(value, None)`` for the leaf at *segs*, following references;
    ``(_SENTINEL, None)`` when the leaf is absent; ``(None, reason)`` when
    a reference does not resolve."""
    v = _get(merged, list(segs))
    hops = 0
    path = tuple(segs)
    while isinstance(v, str) and v.startswith("#") and hops < 16:
        real = _landing(merged, v, path)
        if real is None:
            return None, f"the reference {v} at {'.'.join(segs)} could not be followed"
        path = tuple(real)
        v = _get(merged, list(path))
        hops += 1
    if v is _SENTINEL and path != tuple(segs):
        return None, f"the reference at {'.'.join(segs)} could not be followed"
    return v, None


def landed_path(merged: dict, segs: tuple) -> tuple | None:
    """The real path of the node at *segs* (a reference followed), or None."""
    v = _get(merged, list(segs))
    if isinstance(v, str) and v.startswith("#"):
        real = _landing(merged, v, tuple(segs))
        return tuple(real) if real is not None else None
    return tuple(segs)


def is_flux_line(node: Any) -> bool:
    return isinstance(node, dict) and any(k in node for k in _FLUX_KEYS)


def line_dc_levels(merged: dict, z_segs: tuple, port_offset: float,
                   base_label: str = "offset") -> tuple[list[tuple[str, float]] | None, str | None]:
    """The DC levels the line at *z_segs* may hold while a gate plays:
    ``([(label, volts), ...], None)`` or ``(None, reason naming the leaf)``.

    The port's idle offset (*port_offset*) is always one level. When the
    node is a FluxLine, the level of its flux point is the other (it
    replaces the idle offset at run time, see the module text). An unknown
    flux point, an unresolvable reference or a non-finite value is a
    refusal, never 0."""
    levels = [(base_label, float(port_offset))]
    real = landed_path(merged, tuple(z_segs))
    if real is None:
        return None, f"the reference at {'.'.join(z_segs)} could not be followed"
    z_segs = real
    node = _get(merged, list(z_segs))
    if not is_flux_line(node):
        return levels, None
    line = ".".join(z_segs)
    fp, why = read_leaf(merged, tuple(z_segs) + ("flux_point",))
    if why:
        return None, why
    if fp is _SENTINEL or fp is None:
        fp = DEFAULT_FLUX_POINT
    if not isinstance(fp, str) or fp not in FLUX_POINT_FIELDS:
        return None, (f"the flux point {fp!r} at {line}.flux_point is not one SM knows "
                      f"({', '.join(FLUX_POINT_FIELDS)})")
    field = FLUX_POINT_FIELDS[fp]
    if field is None:
        levels.append((f"{fp} flux-point offset", 0.0))
        return levels, None
    v, why = read_leaf(merged, tuple(z_segs) + (field,))
    if why:
        return None, why
    if v is _SENTINEL or v is None:
        v = 0.0  # the FluxLine field default
    if not _number(v):
        return None, f"the value {v!r} at {line}.{field} is not a finite number of V"
    levels.append((f"{fp} flux-point offset", float(v)))
    return levels, None


def element_dc_levels(merged: dict, element: str, port_offset: float,
                      base_label: str = "channel offset"):
    """:func:`line_dc_levels` for a config element named ``<qubit>.z``;
    any other element keeps only its port offset."""
    if isinstance(element, str) and element.endswith(".z"):
        q = element[:-2]
        qubits = merged.get("qubits") if isinstance(merged, dict) else None
        if isinstance(qubits, dict) and q in qubits:
            return line_dc_levels(merged, ("qubits", q, "z"), port_offset, base_label)
    return [(base_label, float(port_offset))], None
