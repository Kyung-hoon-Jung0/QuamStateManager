"""Pure CZ-bundle decisions; writers consume the returned additions and patch.

Frozen rule schema (v2.1 C7)::

    {id, rev, kind: "pair_gate", source: pair_id | None,
     gate: {name, macro_class, as_default},
     items: [{role, enabled, pulse_class, fields_struct,
              on_choice: "auto" | "control" | "target"}],
     numbers: {"<role>.<field>": value}, source_names: {role: name} | None,
     auto_new: False, excluded: [unordered_key], reviewed: {unordered_key: instanceSig}}

Instances add rule_id, copied_value (field -> value), and optionally
choice_here (control | target). Numeric field values are copied untouched.
"""
from __future__ import annotations

from copy import deepcopy

from quam_state_manager.core import additions
from quam_state_manager.core.add_names import ADDITION_NAME_RE


def pair_members(doc: dict, pair) -> tuple[str, str]:
    if isinstance(pair, (list, tuple)) and len(pair) == 2:
        return tuple(map(str, pair))
    pairs = doc.get("qubit_pairs") or {}
    if isinstance(pairs, dict) and pair in pairs:
        pp = ["qubit_pairs", pair]
        node = pairs[pair]
        return tuple(additions._qubit_of(doc, pp + ["qubit_" + r], node.get("qubit_" + r))
                     for r in ("control", "target"))
    for p in pairs:
        if isinstance(p, (list, tuple)) and (additions._long(p) == pair or additions.quam_pair_id(p) == pair):
            return tuple(map(str, p))
    if isinstance(pair, str) and pair.count("-") == 1:
        return tuple(pair.split("-"))
    raise ValueError("choose a valid pair")


def owner(doc: dict, pair) -> dict:
    c, t = pair_members(doc, pair)
    pid = additions.quam_pair_id((c, t))
    long = additions._long((c, t))
    chip_pair = (doc.get("qubit_pairs") or {}).get(pid, {}) if is_chip(doc) else {}
    return {"target": f"qubit_pairs.{pid}.macros", "label": pid, "kind": "pair",
            "pair": pid, "long": long, "control": c, "target_q": t,
            "coupler": bool(chip_pair.get("coupler")) if is_chip(doc) else any(ln.get("line") == "coupler" and ln.get("element") == long
                           for ln in doc.get("lines") or [] if isinstance(ln, dict))}


def is_chip(doc: dict) -> bool:
    return isinstance(doc.get("qubits"), dict)


def role_input(doc: dict, pair) -> dict:
    """One role input, interpreted at the document's build phase."""
    c, t = pair_members(doc, pair)
    if is_chip(doc):
        values = (doc.get("qubit_pairs") or {}).get(additions.quam_pair_id((c, t)), {})
        freqs = {q: additions._f01(doc, q) for q in (c, t)}
        reason = "by chip f_01"
    else:
        pop = doc.get("populate") or {}
        values = (pop.get("pairs") or {}).get(additions._long((c, t)), {})
        freqs = {q: ((pop.get("qubit") or {}).get(q) or {}).get("RF_freq") for q in (c, t)}
        reason = "by Populate frequency"
    return {"frequencies": freqs, "moving_qubit": values.get("moving_qubit"), "reason": reason}


def role_owner(doc: dict, pair, role: str, on_choice: str = "auto") -> str | None:
    c, t = pair_members(doc, pair)
    if on_choice in ("control", "target"):
        return c if on_choice == "control" else t
    if on_choice != "auto":
        return None
    if role == "coupler":
        return "coupler"
    if role in ("control", "target"):
        return c if role == "control" else t
    inputs = role_input(doc, pair)
    if role in ("moving", "pulse"):
        return {"control": c, "target": t}.get(inputs["moving_qubit"])
    if role in ("high", "low"):
        fc, ft = (inputs["frequencies"][q] for q in (c, t))
        if not all(isinstance(f, (int, float)) and not isinstance(f, bool) and f for f in (fc, ft)):
            return None
        high = c if fc >= ft else t
        return high if role == "high" else (t if high == c else c)
    return None


def display_context(doc: dict) -> dict:
    """Server-derived facts for rendering; the OK request always resolves again."""
    pairs = {}
    for pair in doc.get("qubit_pairs") or []:
        ow = owner(doc, pair)
        pairs[ow["long"]] = dict(ow, builder=additions.builder_gate_names(doc, ow["long"]),
            roles={r: role_owner(doc, pair, r) for r in
                   ("control", "target", "moving", "high", "low", "coupler")})
    return {"pairs": pairs}


def resolve(doc: dict, pair, action: dict) -> dict:
    """Return owner choices, target paths, names, additions, patch and errors.

    A missing automatic owner needs a person's choice; it never picks one.
    Form items may supply a stored/explicit name and collected fields.
    """
    ow = owner(doc, pair)
    gate = action.get("gate") or {}
    name = gate.get("name", "")
    errors, choices, adds = [], {}, []
    fields = deepcopy(action.get("fields") or {})
    existing = doc.get("additions") or []
    moving = None
    for item in action.get("items") or []:
        if not item.get("enabled", True):
            continue
        role = item.get("role", "moving")
        if not item.get("pulse_class"):
            errors.append(f"{item.get('field', role)}: choose a pulse class")
            continue
        q = role_owner(doc, pair, role, item.get("on_choice", "auto"))
        choices[role] = q
        if q is None:
            errors.append(f"{role}: needs a look: choose")
            continue
        if q == "coupler" and not ow["coupler"]:
            errors.append("this pair has no coupler")
            continue
        nm = item.get("name") or name + "_" + ("pulse" if role == "moving" else role)
        if not ADDITION_NAME_RE.fullmatch(nm):
            errors.append(f"the name {nm} is not a valid name")
        target = (f"qubit_pairs.{ow['pair']}.coupler.operations" if q == "coupler"
                  else f"qubits.{q}.z.operations")
        if any(a.get("target") == target and a.get("name") == nm for a in existing + adds):
            errors.append(f"{nm} is already added on {target}")
        pf = deepcopy(item.get("fields", item.get("fields_struct")) or {})
        for key, value in (action.get("numbers") or {}).items():
            prefix = role + "."
            if key.startswith(prefix):
                pf[key[len(prefix):]] = value
        adds.append({"kind": "pulse", "target": target, "name": nm,
                     "class": item.get("pulse_class"), "fields": pf})
        field = item.get("field") or {"moving": "flux_pulse_qubit", "pulse": "flux_pulse_qubit",
                "high": "flux_pulse_high", "low": "flux_pulse_low",
                "control": "flux_pulse_control", "target": "flux_pulse_target",
                "coupler": "coupler_flux_pulse"}.get(role, role)
        fields[field] = "#/" + target.replace(".", "/") + "/" + nm
        if role in ("moving", "pulse"):
            moving = "control" if q == ow["control"] else "target"
    macro = {"kind": "macro", "target": ow["target"], "name": name,
             "class": gate.get("macro_class"), "fields": fields}
    if not ADDITION_NAME_RE.fullmatch(name):
        errors.append(f"the name {name} is not a valid name")
    if any(a.get("target") == ow["target"] and a.get("name") == name for a in existing):
        errors.append(f"{name} is already added on {ow['pair']}")
    if not is_chip(doc):
        before = set(additions.gate_name_errors(doc))
        errors.extend(e for e in additions.gate_name_errors(dict(doc, additions=existing + [macro])) if e not in before)
    adds.append(macro)
    return {"ok": not errors, "owner": ow, "choices": choices, "additions": adds,
            "moving_qubit": moving, "errors": errors, "reason": role_input(doc, pair)["reason"]}
