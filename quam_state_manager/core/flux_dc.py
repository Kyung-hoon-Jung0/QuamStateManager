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
import numbers
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
    """A finite real number; a numpy integer or float counts, bool does not."""
    return isinstance(v, numbers.Real) and not isinstance(v, bool) and math.isfinite(v)


def real_path(merged: dict, segs: tuple) -> tuple[tuple | None, str | None]:
    """The real path of *segs*, following a reference at ANY segment (a
    qubit entry that is a reference, a ``z`` that is a reference, a leaf
    that is a reference): ``(path, None)``, or ``(None, reason naming the
    leaf)`` when a reference dangles or loops. An absent node is not a
    refusal: its path is returned and reading it gives the sentinel."""
    cur: tuple = ()
    for seg in segs:
        cur = cur + (seg,)
        v = _get(merged, list(cur))
        if isinstance(v, str) and v.startswith("#"):
            real = _landing(merged, v, cur)
            if real is None:
                return None, f"the reference {v} at {'.'.join(cur)} could not be followed"
            cur = tuple(real)
    return cur, None


def read_leaf(merged: dict, segs: tuple) -> tuple[Any, str | None]:
    """``(value, None)`` for the leaf at *segs*, following references;
    ``(_SENTINEL, None)`` when the leaf is absent; ``(None, reason)`` when
    a reference does not resolve."""
    path, why = real_path(merged, tuple(segs))
    if why:
        return None, why
    return _get(merged, list(path)), None


def landed_path(merged: dict, segs: tuple) -> tuple | None:
    """The real path of the node at *segs* (references followed), or None."""
    return real_path(merged, tuple(segs))[0]


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
    refusal, never 0.

    ``flux_point`` absent or ``None`` reads as the FluxLine class default
    ``"independent"``: the dataclass default fills an absent field, and a
    stored None means "not set", read the same way. The lab programs pick
    the flux point as a run argument, so the stored field is the best guess
    SM has, which is why the port offset stays a level as well."""
    levels = [(base_label, float(port_offset))]
    real, why = real_path(merged, tuple(z_segs))
    if why:
        return None, why
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
        got, why = _min_level(merged, tuple(z_segs))
        if why:
            return None, why
        levels.append(got)
        return levels, None
    v, why = read_leaf(merged, tuple(z_segs) + (field,))
    if why:
        return None, why
    if v is _SENTINEL or v is None:
        v = 0.0  # the FluxLine field default
    if not _number(v):
        return None, f"the value {v!r} at {line}.{field} is not a finite number of V"
    levels.append((f"{fp} flux-point offset", float(v)))
    if fp != "min":
        got, why = _min_level(merged, tuple(z_segs))
        if why:
            return None, why
        levels.append(got)
    return levels, None


def _min_level(merged: dict, z_segs: tuple):
    """The ``min_offset`` level of a FluxLine: the lab programs park every
    qubit line there before most gates (``apply_all_flux_to_min``), so it is a
    level whatever the stored flux point. Absent: the FluxLine class default
    0.0, labelled as such (:func:`_offset_level`, the same rule as the
    coupler decouple offset). None or a non-number is a refusal naming the leaf."""
    return _offset_level(merged, z_segs + ("min_offset",), "min flux-point offset")


#: TunableCoupler flux_point -> its offset field (None: 0 V)
COUPLER_POINT_FIELDS = {
    "off": "decouple_offset",
    "on": "interaction_offset",
    "arbitrary": "arbitrary_offset",
    "zero": None,
}
#: the TunableCoupler default when flux_point is absent or None
DEFAULT_COUPLER_POINT = "off"


def _stored_number(merged: dict, segs: tuple) -> tuple[float | None, str | None]:
    """A stored finite number at *segs*. Absent, None, NaN, inf, bool and
    text are each a refusal naming the leaf."""
    v, why = read_leaf(merged, segs)
    if why:
        return None, why
    leaf = ".".join(segs)
    if v is _SENTINEL:
        return None, f"no value is stored at {leaf}"
    if not _number(v):
        return None, f"the value {v!r} at {leaf} is not a finite number of V"
    return float(v), None


def _offset_level(merged: dict, segs: tuple, label: str):
    """``((label, volts), None)`` for an offset field whose class default is
    0.0: an absent field reads as that default and is labelled
    ``"(class default)"``; a stored value keeps the plain label. Stored None,
    NaN, inf, bool or text is a refusal naming the leaf."""
    raw, why = read_leaf(merged, segs)
    if why:
        return None, why
    if raw is _SENTINEL:
        return (label + " (class default)", 0.0), None
    v, why = _stored_number(merged, segs)
    if why:
        return None, why
    return (label, v), None


def _coupler_levels(merged: dict, segs: tuple, levels: list):
    """The DC levels of the tunable coupler at *segs*: its decouple offset
    (the lab programs park every coupler there) and the offset of its
    stored flux point. A level SM cannot read is a refusal, never 0."""
    line = ".".join(segs)
    fp, why = read_leaf(merged, segs + ("flux_point",))
    if why:
        return None, why
    if fp is _SENTINEL or fp is None:
        fp = DEFAULT_COUPLER_POINT
    if not isinstance(fp, str) or fp not in COUPLER_POINT_FIELDS:
        return None, (f"the flux point {fp!r} at {line}.flux_point is not one SM knows "
                      f"({', '.join(COUPLER_POINT_FIELDS)})")
    fields = ["decouple_offset"]
    if COUPLER_POINT_FIELDS[fp] and COUPLER_POINT_FIELDS[fp] not in fields:
        fields.append(COUPLER_POINT_FIELDS[fp])
    for f in fields:
        label = f"coupler {f.replace('_', ' ')}"
        if f == "decouple_offset":
            # the TunableCoupler class default 0.0 when absent
            got, why = _offset_level(merged, segs + (f,), label)
        else:
            v, why = _stored_number(merged, segs + (f,))
            got = (label, v)
        if why:
            return None, why
        levels.append(got)
    if COUPLER_POINT_FIELDS[fp] is None:
        levels.append((f"coupler {fp} flux point", 0.0))
    return levels, None


def _coupler_names(merged: dict, key: str, node: Any) -> set:
    """The config element names the coupler of pair *key* can have."""
    if isinstance(node, dict) and isinstance(node.get("id"), str):
        return {node["id"]}
    pid = _get(merged, ["qubit_pairs", key, "id"])
    return {f"{key}.coupler", f"{pid}.coupler" if isinstance(pid, str) else ""}


def _is_qubit_component(merged: dict, element: str) -> bool:
    """*element* is ``<qubit>.<component>`` for a component stored on a
    qubit in ``qubits``."""
    qubits = merged.get("qubits") if isinstance(merged, dict) else None
    q, dot, rest = element.partition(".")
    if not (dot and rest) or not isinstance(qubits, dict):
        return False
    node = qubits.get(q)
    return isinstance(node, dict) and rest.split(".")[0] in node


def _find_coupler(merged: dict, element: str) -> tuple[tuple | None, str | None]:
    """``(real path, None)`` of the pair coupler whose config element is
    *element* (its ``id`` when that is text, else ``<pair>.coupler``),
    ``(None, None)`` when no pair has it, or ``(None, reason)`` when it is not
    found and a pair's coupler reference could not be followed (SM cannot
    tell whether that coupler is *element*)."""
    pairs = merged.get("qubit_pairs") if isinstance(merged, dict) else None
    if not isinstance(pairs, dict):
        return None, None
    dangling = None
    for key in pairs:
        path, why = real_path(merged, ("qubit_pairs", key, "coupler"))
        if why:
            # a dangling coupler can be *element* when the element is named like
            # that pair's coupler, or when it names no qubit component at all
            if (element in _coupler_names(merged, key, None)
                    or not _is_qubit_component(merged, element)):
                dangling = dangling or why
            continue
        node = _get(merged, list(path))
        if not isinstance(node, dict):
            continue
        if element in _coupler_names(merged, key, node):
            return path, None
    return None, dangling


def _mutual_levels(merged: dict, q_path: tuple, levels: list):
    """A pair's ``mutual_flux_bias`` ([control, target], set on the two
    qubits' flux lines by the pair's mutual idle) is one more level of
    the qubit at *q_path* for every pair that stores it."""
    pairs = merged.get("qubit_pairs") if isinstance(merged, dict) else None
    if not isinstance(pairs, dict):
        return levels, None
    for key in pairs:
        base = ("qubit_pairs", key)
        bias, why = read_leaf(merged, base + ("mutual_flux_bias",))
        if why:
            return None, why
        if bias is _SENTINEL or bias is None:
            continue
        roles = [real_path(merged, base + (r,)) for r in ("qubit_control", "qubit_target")]
        if not any(qp == q_path for qp, _ in roles):
            for _, why in roles:
                if why:
                    # SM cannot tell whether this pair's qubit is the one checked
                    return None, why
        for i, (qp, _) in enumerate(roles):
            if qp is None or qp != q_path:
                continue
            if not isinstance(bias, list) or len(bias) != 2:
                return None, (f"the value {bias!r} at qubit_pairs.{key}.mutual_flux_bias "
                              "is not a [control, target] pair of V")
            v, why = _stored_number(merged, base + ("mutual_flux_bias", str(i)))
            if why:
                return None, why
            levels.append((f"mutual flux bias of {key}", v))
    return levels, None


def element_dc_levels(merged: dict, element: str, port_offset: float,
                      base_label: str = "channel offset"):
    """The DC levels of the config element *element*: a qubit flux line
    ``<qubit>.z`` (:func:`line_dc_levels`, plus every stored
    ``mutual_flux_bias`` naming that qubit) or a pair's tunable coupler.
    A qubit entry, or any node on the way, that is a reference is followed.
    Any other element keeps only its port offset."""
    levels = [(base_label, float(port_offset))]
    if not isinstance(element, str):
        return levels, None
    if element.endswith(".z"):
        q = element[:-2]
        qubits = merged.get("qubits") if isinstance(merged, dict) else None
        if isinstance(qubits, dict) and q in qubits:
            got, why = line_dc_levels(merged, ("qubits", q, "z"), port_offset, base_label)
            if why:
                return None, why
            q_path, why = real_path(merged, ("qubits", q))
            if why:
                return None, why
            return _mutual_levels(merged, q_path, got)
    c_path, why = _find_coupler(merged, element)
    if why:
        return None, why
    if c_path is not None:
        return _coupler_levels(merged, c_path, levels)
    return levels, None
