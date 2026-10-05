"""The XY drive pulses a qubit plays, read for the Qubits component table.

The Qubits page listed frequencies, coherence and the readout pulse, but not
the drive: no x180 / x90 amplitude anywhere on it (docs/286). This module reads
those numbers ONE way -- the way the Live State Edit qubit grid already does:

* The dot-paths come from the grid's own curated columns
  (``param_specs._BULK_COLUMNS_SPEC`` keys ``x180_amplitude`` /
  ``x90_amplitude`` -> ``qubits.{name}.xy.operations.x180.amplitude``), and the
  pulse's length / DRAG ``alpha`` are siblings of that amplitude. The two pages
  therefore name the same leaf, by construction.
* Every value goes through ``pointer_path.resolve_field_target`` -- the
  follower the grid, the fit targets and Diagnostics use. On a builder chip
  ``operations.x180`` is an alias (``"#./x180_DragCosine"``); another lab may
  alias it to a differently named pulse, or store the pulse inline under
  ``x180`` itself. The alias is followed to the pulse actually in force, and
  that pulse's real name is reported, never re-guessed from a naming pattern.
* The physical output is ``physical_units.amp_annotation`` called exactly as
  the grid's ``_build_bulk_cell`` calls it (resolved path + alias path), so the
  dBm a row shows is the dBm the grid cell shows. A blank is a blank with its
  reason; nothing here re-derives ``P = FSP + 20*log10|amp|``.
* The drive IF is ``cr_semantics.channel_effective_rf_if`` -- quam's own
  ``RF - LO`` for the ``#./inferred_intermediate_frequency`` alias, the same
  call the printable chip report makes.

A qubit with no ``x180`` (or ``x90``) operation gets a blank cell; its hint
names the x180-like pulses the qubit DOES carry, because which of them plays is
not something the state says and SM does not pick one.

Read-only and never raises for chip content: anything unreadable is a blank
cell with a stated reason.
"""

from __future__ import annotations

from typing import Any

from quam_state_manager.core import cr_semantics, physical_units
from quam_state_manager.core.param_specs import _BULK_COLUMNS_SPEC
from quam_state_manager.core.pointer_path import _walk, resolve_field_target
from quam_state_manager.core.pointer_resolver import is_pointer

#: The two gate operations the table shows, mapped to the curated grid column
#: whose template names their amplitude leaf.
_OP_COLUMNS = (("x180", "x180_amplitude"), ("x90", "x90_amplitude"))

#: Pulse leaves read per operation (``alpha`` only exists on DRAG classes).
_FIELDS = ("amplitude", "length", "alpha")


def _op_template(column_key: str) -> str:
    """``qubits.{name}.xy.operations.<op>`` from the grid's curated column."""
    for col in _BULK_COLUMNS_SPEC:
        if col["key"] == column_key:
            tmpl = col["tmpl"]
            if not tmpl.endswith(".amplitude"):
                raise ValueError(f"{column_key}: not an amplitude column: {tmpl}")
            return tmpl.rsplit(".", 1)[0]
    raise KeyError(column_key)


OP_TEMPLATES: dict[str, str] = {op: _op_template(key) for op, key in _OP_COLUMNS}


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _leaf(merged: dict, path: str) -> dict:
    """One pulse leaf: ``{"path", "resolved_path", "value", "state", "why"}``.

    ``state``: ``num`` (a number) | ``text`` (a non-pointer string, shown
    quoted the way the page shows any stored-as-text number) | ``blank``
    (absent, null, or a pointer that does not end at a number -- ``why``
    says which). A pointer string is NEVER the value.
    """
    try:
        ft = resolve_field_target(merged, path)
    except Exception:  # noqa: BLE001 -- a cell degrades, never the page
        ft = {}
    resolved = ft.get("resolved_path") or path
    val = ft.get("resolved_value")
    out = {"path": path, "resolved_path": resolved, "value": None,
           "state": "blank", "why": ""}
    if isinstance(val, str) and is_pointer(val):
        out["why"] = ("Not a stored number: the field is a pointer that ends "
                      "in a value computed at run time, or in nothing.")
    elif not ft.get("resolvable"):
        out["why"] = "This pulse has no such field."
    elif val is None:
        out["why"] = "Not set."
    elif _is_num(val):
        out["value"], out["state"] = val, "num"
    elif isinstance(val, str):
        out["value"], out["state"] = val, "text"
    else:
        out["why"] = "Not a number."
    return out


def _family(ops: Any, op: str) -> list[str]:
    """Operation names that look like *op* (``x180_DragCosine``, ``x180_long``
    ...) -- only for the hint on a blank row, never to pick a value."""
    if not isinstance(ops, dict):
        return []
    out = []
    for k in ops:
        if not isinstance(k, str) or k == op or not k.startswith(op):
            continue
        nxt = k[len(op):len(op) + 1]
        if nxt.isdigit():          # x1800 is not x180
            continue
        out.append(k)
    return sorted(out)


def _operation(merged: dict, qid: str, op: str) -> dict:
    """The pulse *op* resolves to on qubit *qid*, and its three leaves."""
    op_path = OP_TEMPLATES[op].format(name=qid)
    found, ops = _walk(merged, op_path.split(".")[:-1])
    rec: dict[str, Any] = {"op": op, "path": op_path, "present": False,
                           "pulse": None, "pulse_class": None,
                           "family": _family(ops if found else None, op),
                           "why": ""}
    raw_found, raw = _walk(merged, op_path.split("."))
    if not raw_found:
        rec["why"] = (f"No '{op}' operation on this qubit"
                      + (f" (it carries {', '.join(rec['family'])}; which one "
                         f"plays is not stated in the state)" if rec["family"]
                         else "") + ".")
        return rec
    try:
        ft = resolve_field_target(merged, op_path)
    except Exception:  # noqa: BLE001
        ft = {}
    rp = ft.get("resolved_path") or op_path
    rfound, node = _walk(merged, rp.split("."))
    if not ft.get("resolvable") or not rfound or not isinstance(node, dict):
        rec["why"] = (f"'{op}' does not resolve to a pulse"
                      + (" (it is a pointer that cannot be followed)."
                         if is_pointer(raw) else "."))
        return rec
    rec["present"] = True
    rec["pulse"] = rp.rsplit(".", 1)[-1]
    cls = node.get("__class__")
    rec["pulse_class"] = cls.rsplit(".", 1)[-1] if isinstance(cls, str) else None
    for field in _FIELDS:
        rec[field] = _leaf(merged, f"{op_path}.{field}")
    amp = rec["amplitude"]
    rec["phys"], rec["phys_why"] = None, "The amplitude is not a stored number."
    if amp["state"] == "num":
        why: list = []
        try:
            rec["phys"] = physical_units.amp_annotation(
                merged, amp["resolved_path"], amp["value"],
                alias_path=amp["path"], why=why)
        except Exception:  # noqa: BLE001 -- blank, never invented
            rec["phys"] = None
        if rec["phys"] is None:
            rec["phys_why"] = (why[0]["text"] if why else
                               "No MW output power to state: the drive chain "
                               "does not resolve to an MW-FEM port with "
                               "full_scale_power_dbm, or the amplitude is 0.")
        elif rec["phys"].get("kind") != "mw":
            # an LF port states volts, which a dBm column must not show
            rec["phys_why"] = ("Not an MW port: the drive's peak output is "
                               + str(rec["phys"].get("text", "")) + ".")
            rec["phys"] = None
        else:
            rec["phys_why"] = ""
    return rec


def summary(store: Any, qid: str) -> dict:
    """The drive row for one qubit: ``{"x180": op, "x90": op, "rf_hz",
    "if_hz"}`` where each op is :func:`_operation`'s record. ``store`` is a
    ``QuamStore`` (its ``merged`` document and pointer cache are read)."""
    merged = store.merged
    out: dict[str, Any] = {op: _operation(merged, qid, op) for op, _ in _OP_COLUMNS}
    rf = if_ = None
    try:
        q = (merged.get("qubits") or {}).get(qid)
        xy = q.get("xy") if isinstance(q, dict) else None
        rf, if_ = cr_semantics.channel_effective_rf_if(
            store, xy, ("qubits", str(qid), "xy"))
    except Exception:  # noqa: BLE001 -- a cell degrades, never the page
        rf = if_ = None
    out["rf_hz"], out["if_hz"] = rf, if_
    return out
