"""Plan pieces for a gate of a slot gate class SM made (pure).

A slot gate class is rendered by :mod:`slot_gate_template` and named per
draft (``quam_config.<Name>.<Name>``), so it is never a row of
``cz_gate_build.GATE_CLASSES``. :func:`slot_desc` builds the row the gate
planner needs for one class; :func:`plan_spectators` fills the class's
spectator rows and plans the shared ``SPECTATOR_UNIT_OP`` operation each
spectator qubit's flux line needs. The scale rule (``|level / unit|``
strictly below the template's limit) is never restated here: every check
goes through :func:`slot_gate_template.spectator_rows_problem` and every
range through :func:`slot_gate_template.reachable_level`.

Pure: no subprocess, no QM-stack import, no store mutation. Only
:func:`slot_drafts` reads files (the draft store).
"""
from __future__ import annotations

import math
from typing import Any

from quam_state_manager.core import cz_gate_build as cgb
from quam_state_manager.core import slot_gate_template as sgt
from quam_state_manager.core.spec_constraints import LENGTH_FLOOR_ANY_QOP_NS

SPECTATOR_QUBITS = "spectator_qubits"
SPECTATOR_LEVELS = "spectator_levels"
SPECTATOR_LENGTHS = "spectator_lengths"
SPECTATOR_FIELDS = (SPECTATOR_QUBITS, SPECTATOR_LEVELS, SPECTATOR_LENGTHS)

#: the pulse class of a unit operation SM creates on a spectator's flux line
UNIT_CLASS = cgb.SPECTATOR_CLASS

UNIT_GIVEN_PROBLEM = ("Spectator {name!r}: the unit operation " + repr(sgt.SPECTATOR_UNIT_OP)
                      + " already exists on that qubit with amplitude {have!r}; it is used as it"
                      " is. Leave the unit amplitude out of this row.")
UNIT_PLANNED_PROBLEM = ("Spectator {name!r}: another gate of this request already plans the unit"
                        " operation " + repr(sgt.SPECTATOR_UNIT_OP) + " on that qubit with amplitude"
                        " {have!r}; this gate uses the same one. Leave the unit amplitude out of"
                        " this row.")
UNIT_NO_AMPLITUDE_PROBLEM = ("Spectator {name!r}: the unit operation " + repr(sgt.SPECTATOR_UNIT_OP)
                             + " exists on that qubit, but it holds no amplitude SM can read."
                             " Give that operation a plain number as its amplitude first.")
ROW_PROBLEM = "Each spectator row needs a qubit name; received {value!r}."
UNREADABLE_PROBLEM = ("Spectator rows could not be read. Send each row as a qubit name, a length,"
                      " a level and, for a new unit operation, a unit amplitude.")
DOT_PROBLEM = ("Spectator {name!r}: the name of that qubit or of its flux line holds a dot, and"
               " SM cannot write an operation under such a name.")
LIST_PROBLEM = ("Spectator {name!r}: its flux line sits inside a list on this chip, and SM cannot"
                " add an operation there. Add the unit operation to that line first.")


def _through_list(merged: Any, segs) -> bool:
    """True when the walk from the chip root along *segs* meets anything
    but a dict before its end (a list is the case that occurs). The create
    door refuses every such path."""
    node = merged
    for seg in segs:
        if not isinstance(node, dict):
            return True
        node = node.get(seg)
    return False


def qclass_of(spec: sgt.SlotSpec) -> str:
    """The class path a gate of *spec* records in ``__class__``."""
    return f"quam_config.{spec.class_name}.{spec.class_name}"


def slot_field(slot: str) -> str:
    return f"{slot}_flux_pulse"


def slot_desc(spec: sgt.SlotSpec) -> dict[str, Any]:
    """A descriptor with the keys of a ``GATE_CLASSES`` row for *spec*.

    ``skeleton`` is a callable returning a fresh macro dict, as in the
    table: the checked slot fields None, phase shifts 0.0, ``duration``
    None, ``extras`` and ``fidelity`` empty, the three spectator dicts
    empty when the class has spectators. ``slots`` lists only the class's
    own slots, all required, in template order."""
    slots = tuple(s for s in sgt.SLOTS if s in spec.slots)
    qclass = qclass_of(spec)

    def skeleton() -> dict[str, Any]:
        macro: dict[str, Any] = {slot_field(s): None for s in slots}
        macro.update({"phase_shift_control": 0.0, "phase_shift_target": 0.0,
                      "duration": None, "extras": {}, "fidelity": {}})
        if spec.spectators:
            for key in SPECTATOR_FIELDS:
                macro[key] = {}
        macro["__class__"] = qclass
        return macro

    return {"skeleton": skeleton,
            "slots": tuple(cgb.Slot(slot_field(s), f"{s}_pulse", s, True) for s in slots)}


def _qubit_entry(merged: dict, qubit: str) -> tuple[tuple, Any]:
    qubits = merged.get("qubits") if isinstance(merged, dict) else None
    entry = qubits.get(qubit) if isinstance(qubits, dict) else None
    segs: tuple = ("qubits", qubit)
    if isinstance(entry, str) and entry.startswith("#"):
        from quam_state_manager.core.flux_dc import landed_path
        from quam_state_manager.core.pointer_resolver import _get
        real = landed_path(merged, segs)
        entry = _get(merged, list(real)) if real is not None else None
        segs = real or segs
    return tuple(segs), entry


def _unit_ops(merged: dict, qubit: str) -> tuple[tuple | None, dict | None]:
    """(path segments of the qubit's z operations, the operations dict) or (None, None)."""
    segs, entry = _qubit_entry(merged, qubit)
    z = entry.get("z") if isinstance(entry, dict) else None
    ops = z.get("operations") if isinstance(z, dict) else None
    if not isinstance(ops, dict):
        return None, None
    return segs + ("z", "operations"), ops


def unit_amplitudes(merged: dict, qubits) -> dict[str, Any]:
    """``{qubit: amplitude}`` of ``SPECTATOR_UNIT_OP`` on each qubit's z
    line, as stored; None when the qubit, its z operations, the operation
    or its amplitude is missing."""
    out: dict[str, Any] = {}
    for q in qubits:
        _segs, ops = _unit_ops(merged, q)
        op = ops.get(sgt.SPECTATOR_UNIT_OP) if ops is not None else None
        out[q] = op.get("amplitude") if isinstance(op, dict) else None
    return out


def _has_unit_op(merged: dict, qubit: str) -> bool:
    _segs, ops = _unit_ops(merged, qubit)
    return ops is not None and sgt.SPECTATOR_UNIT_OP in ops


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _finite(v: Any) -> bool:
    """A number a float can hold; a whole number too large for one is not."""
    if not _is_number(v):
        return False
    try:
        return math.isfinite(v)
    except OverflowError:
        return False


def _plain(v: Any) -> Any:
    """*v* as a float when it is a finite number, else as given (the rule
    sentence then names the value)."""
    return float(v) if _finite(v) else v


def _whole(v: Any) -> Any:
    """A length with an integral value as an int, else as given."""
    return int(v) if _finite(v) and v == int(v) else v


def _same_number(a: Any, b: Any) -> bool:
    """Exact equality of two numbers, with no tolerance: a unit amplitude is
    compared as it was sent, so 0.1 + 0.2 is not the stored 0.3."""
    return _is_number(a) and _is_number(b) and a == b


def real_qubit(merged: dict, ref: Any) -> str | None:
    """The name of the qubit *ref* (a ``#/qubits/<name>`` reference or a
    bare name) lands on: a qubit entry that is a reference to another qubit
    is that other qubit. None when *ref* is no qubit reference; the name as
    written when the entry cannot be followed to a qubit. The plan and the
    stored check both name qubits through this, so two names of one qubit
    are one qubit."""
    name = sgt.qubit_name(ref)
    if name is None:
        return None
    try:
        segs, entry = _qubit_entry(merged, name)
    except Exception:  # noqa: BLE001 -- a broken chain keeps the written name
        return name
    if (tuple(segs[:-1]) == ("qubits",) and isinstance(entry, dict)
            and sgt.qubit_name(segs[-1]) is not None):
        return segs[-1]
    return name


def qubit_identity(merged: dict, ref: Any):
    """The landed object path, keeping plain names for entries under qubits."""
    name = sgt.qubit_name(ref)
    if name is None:
        return None
    segs, entry = _qubit_entry(merged, name)
    if isinstance(entry, dict):
        return segs[-1] if segs[:-1] == ("qubits",) else segs
    return name


def _identity_problem(merged, qubits, control, target):
    if not isinstance(qubits, dict):
        return None
    own = {qubit_identity(merged, control), qubit_identity(merged, target)}
    seen = {}
    for name, ref in qubits.items():
        identity = qubit_identity(merged, ref)
        if identity is None:
            continue
        if identity in own:
            return sgt.OWN_PROBLEM.format(name=name)
        if identity in seen:
            return sgt.DUPLICATE_PROBLEM.format(first=seen[identity], second=name)
        seen[identity] = name
    return None


def rows_problem(merged: dict, qubits, levels, lengths, control, target, units) -> str | None:
    """The sentence of :func:`slot_gate_template.spectator_rows_problem`,
    plus what that function cannot see: a qubit that is not on the chip or
    has no flux line gets that reason instead of "the unit operation is
    missing", a unit operation that exists without an amplitude is said to
    exist, and a length no float can hold is refused. *qubits*, *control*
    and *target* are already named through :func:`real_qubit`. Never raises."""
    try:
        why = _identity_problem(merged, qubits, control, target)
        if why:
            return why
        why = sgt.spectator_rows_problem(qubits, levels, lengths, control, target, units)
        if why is None:
            for name, value in lengths.items():
                if not _finite(value):
                    return sgt.LENGTH_PROBLEM.format(name=name, value=value)
            return None
        for name, ref in qubits.items() if isinstance(qubits, dict) else ():
            if why != sgt.UNIT_PROBLEM.format(name=name):
                continue
            q = sgt.qubit_name(ref)
            ln = cgb.spectator_line(merged, q)
            if isinstance(ln, str):
                return ln
            if _has_unit_op(merged, q):
                return UNIT_NO_AMPLITUDE_PROBLEM.format(name=name)
        return why
    except Exception:  # noqa: BLE001 -- data never raises, it is refused
        return UNREADABLE_PROBLEM


def plan_spectators(merged: dict, pair_id: str, gate_name: str, rows, macro: dict,
                    macro_segs: tuple, planned_units: dict | None = None
                    ) -> tuple[list[tuple[str, Any]], dict[str, Any], list[str]]:
    """Fill *macro*'s spectator dicts from *rows* and plan the unit operations.

    *rows*: ``[{"qubit", "length", "level", "unit"?}]``; ``unit`` is the
    amplitude of a new ``SPECTATOR_UNIT_OP`` and is read only for a qubit
    whose line has no such operation yet (an existing one is reused read
    only). A qubit is named through :func:`real_qubit`: the macro, the rows
    of ``extra`` use the real name. *planned_units* uses the landed
    object path (or its real name under ``qubits``), so two names of one qubit are refused as a duplicate and never get
    two unit operations. *planned_units*: ``{identity: amplitude}`` of unit
    operations this request already plans for another gate; such a qubit
    gets no second create, and on success the map gains this call's new
    units (changed in place). A given unit is the same as a stored or
    planned one only when the two numbers are exactly equal. A name with a
    dot in it (the qubit's or a part of its line's path) is refused: the
    create path would be read as a longer path. A new unit operation on
    a line that sits inside a list is refused too: the create door steps
    through dicts only. Lengths are stored as whole
    numbers. A level
    of 0 is accepted on purpose: the template's rule accepts it, and it is
    the natural value before the level is calibrated.

    Returns ``(creates, extra, refusals)``: on any refusal the macro is
    left as given, creates is empty and extra is empty. A refusal from the
    rows is the sentence of :func:`rows_problem`. Data of any shape is
    refused with one sentence, never raised."""
    try:
        return _plan_spectators(merged, pair_id, rows, macro, planned_units)
    except Exception:  # noqa: BLE001 -- data never raises, it is refused
        return [], {}, [UNREADABLE_PROBLEM]


def _plan_spectators(merged, pair_id, rows, macro, planned_units):
    if planned_units is not None and not isinstance(planned_units, dict):
        return [], {}, [UNREADABLE_PROBLEM]
    planned = planned_units if planned_units is not None else {}
    if rows is None:
        rows = []
    if not isinstance(rows, (list, tuple)):
        return [], {}, [sgt.TYPE_PROBLEM.format(what="rows", kind="a list", value=rows)]
    qubits: dict[str, str] = {}
    levels: dict[str, Any] = {}
    lengths: dict[str, Any] = {}
    units: dict[str, Any] = {}
    new_units: dict[Any, Any] = {}
    written: dict[Any, str] = {}
    lines: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            return [], {}, [ROW_PROBLEM.format(value=row)]
        name = sgt.qubit_name(row.get("qubit"))
        if name is None:
            return [], {}, [ROW_PROBLEM.format(value=row.get("qubit"))]
        ln = cgb.spectator_line(merged, name)
        if isinstance(ln, str):
            return [], {}, [ln]
        q = real_qubit(merged, name)
        if any("." in str(seg) for seg in ln["segs"]):
            return [], {}, [DOT_PROBLEM.format(name=name)]
        identity = qubit_identity(merged, name)
        if identity in written:
            return [], {}, [sgt.DUPLICATE_PROBLEM.format(first=written[identity], second=name)]
        written[identity] = name
        lines[q] = ln
        qubits[q] = cgb._ref("qubits", q)
        levels[q] = _plain(row.get("level"))
        lengths[q] = _whole(row.get("length"))
        given = _plain(row.get("unit"))
        if _has_unit_op(merged, q):
            have = unit_amplitudes(merged, [q])[q]
            if given is not None and _is_number(have) and not _same_number(given, have):
                return [], {}, [UNIT_GIVEN_PROBLEM.format(name=q, have=have)]
            units[q] = have
        elif identity in planned:
            if given is not None and not _same_number(given, planned[identity]):
                return [], {}, [UNIT_PLANNED_PROBLEM.format(name=q, have=planned[identity])]
            units[q] = planned[identity]
        else:
            units[q] = given
            if given is not None:
                new_units[identity] = given
    control = real_qubit(merged, cgb.pair_qubit(merged, pair_id, "qubit_control"))
    target = real_qubit(merged, cgb.pair_qubit(merged, pair_id, "qubit_target"))
    why = rows_problem(merged, qubits, levels, lengths, control, target, units)
    if why:
        return [], {}, [why]
    creates: list[tuple[str, Any]] = []
    out_rows = []
    for q in qubits:
        ln = lines[q]
        unit_path = cgb._dot(ln["segs"], sgt.SPECTATOR_UNIT_OP)
        identity = qubit_identity(merged, q)
        created = identity in new_units
        if created:
            if _through_list(merged, ln["segs"]):
                return [], {}, [LIST_PROBLEM.format(name=q)]
            # the unit pulse is stretched from QUA; the shared floor plays on every QOP
            built = cgb._fields_for(UNIT_CLASS, {"length": LENGTH_FLOOR_ANY_QOP_NS,
                                                 "amplitude": new_units[identity]}, None)
            if isinstance(built, str):
                return [], {}, [built]
            creates.append((unit_path, {"id": sgt.SPECTATOR_UNIT_OP, **built[0]}))
        out_rows.append({"qubit": q, "line": f"{q}.z", "level": levels[q],
                         "length": lengths[q], "unit": units[q],
                         "reachable": sgt.reachable_level(units[q]),
                         "unit_operation": unit_path, "unit_created": created})
    macro[SPECTATOR_QUBITS] = qubits
    macro[SPECTATOR_LEVELS] = levels
    macro[SPECTATOR_LENGTHS] = lengths
    planned.update(new_units)
    return creates, {"spectators": out_rows}, []


def macro_spectator_problem(merged: dict, pair_id: str, macro: Any) -> str | None | bool:
    """For a stored macro: False when it holds no spectator rows (no
    ``spectator_levels`` field, or the three spectator fields all empty),
    else the sentence of :func:`rows_problem` (None when the rows pass),
    read with the unit amplitudes of *merged*; every qubit is named through
    :func:`real_qubit`."""
    if (not isinstance(macro, dict) or SPECTATOR_LEVELS not in macro
            or not any(macro.get(k) for k in SPECTATOR_FIELDS)):
        return False
    qubits = macro.get(SPECTATOR_QUBITS)
    units: dict = {}
    if isinstance(qubits, dict):
        real = {k: real_qubit(merged, v) for k, v in qubits.items()}
        amps = unit_amplitudes(merged, sorted({q for q in real.values() if q}))
        units = {k: amps[q] for k, q in real.items() if q}
        qubits = {k: (cgb._ref("qubits", q) if q else qubits[k]) for k, q in real.items()}
    return rows_problem(
        merged, qubits, macro.get(SPECTATOR_LEVELS), macro.get(SPECTATOR_LENGTHS),
        real_qubit(merged, cgb.pair_qubit(merged, pair_id, "qubit_control")),
        real_qubit(merged, cgb.pair_qubit(merged, pair_id, "qubit_target")), units)


def xy_operations(merged: dict, pair_id: str, role: str) -> list[str] | None:
    """The operation names of the xy channel of the pair's *role* qubit
    (``control`` / ``target``), sorted; None when that qubit has no xy channel
    with an operations dict. The qubit is named through :func:`real_qubit`;
    an xy entry that is an absolute reference is followed one hop."""
    try:
        key = {"control": "qubit_control", "target": "qubit_target"}[role]
        qubit = real_qubit(merged, cgb.pair_qubit(merged, pair_id, key))
        if not qubit:
            return None
        _, entry = _qubit_entry(merged, qubit)
        xy = entry.get("xy") if isinstance(entry, dict) else None
        if isinstance(xy, str):
            hop = cgb._follow(merged, xy)
            xy = hop[1] if hop else None
        ops = xy.get("operations") if isinstance(xy, dict) else None
        return sorted(str(k) for k in ops) if isinstance(ops, dict) else None
    except Exception:  # noqa: BLE001 -- chip data never raises here
        return None


def xy_steps_problem(merged: dict, pair_id: str, spec: Any) -> str | None:
    """None, or the template's one sentence for the first xy step of *spec*
    that plays an operation the pair's qubit of that role does not have."""
    for where in ("before", "after"):
        for number, step in enumerate(getattr(spec, where, ()) or (), 1):
            if not isinstance(step, dict) or step.get("kind") != "xy":
                continue
            role, operation = step.get("qubit"), step.get("operation")
            if operation not in (xy_operations(merged, pair_id, role) or ()):
                return sgt.XY_MISSING_PROBLEM.format(number=number, where=where, role=role,
                                                     operation=operation)
    return None


def steps_sentence(spec: Any) -> str | None:
    """One plain sentence naming the class's sequence steps, or None without steps."""
    before, after = getattr(spec, "before", ()) or (), getattr(spec, "after", ()) or ()
    if not before and not after:
        return None
    try:
        words = [sgt._describe(sgt._plain_steps(s, w)) for w, s in (("before", before), ("after", after))]
    except ValueError as exc:
        return str(exc)
    return f"This class plays steps before the gate pulses: {words[0]}; after them: {words[1]}."


def slot_drafts(instance, scope, versions=False) -> list[tuple]:
    """``[(class name, spec)]`` for every draft in *scope* that parses as a
    slot gate class whose class name is the draft's name, at any version the
    parser reads; ``[(class name, spec, version)]`` with *versions*."""
    from quam_state_manager.core import pulse_class_drafts as drafts
    out = []
    for d in drafts.list_drafts(instance, scope):
        name = d["class_name"]
        data = drafts.load_draft(instance, scope, name)
        if data is None:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        parsed = sgt.parse(text)
        if parsed.ok and parsed.spec is not None and parsed.spec.class_name == name:
            out.append((name, parsed.spec, parsed.version) if versions else (name, parsed.spec))
    return out
