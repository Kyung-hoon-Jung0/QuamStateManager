"""Shared pair verdicts over existing structural, placement and environment checks.

Ready says only that SM's checks pass. Pointer landing and runtime code on a
spec are checked at Build. New macro classes on a built chip use Re-generate.
Writers remain responsible for their own serialized form and undo grouping.
"""
from __future__ import annotations

from copy import deepcopy
from quam_state_manager.core import additions, gate_action
from quam_state_manager.core.add_names import unordered_key

READY = "Ready"
NEEDS_LOOK = "Needs a look"
CANT = "Can't"
ALREADY = "Already has it"
BUILD_CHECK = "pointer landing and runtime code checked at Build"
NEW_CHIP_CLASS = "A new macro class on a built chip is not created on the Pulses page in v1; use Re-generate."


def _macros(doc, pair):
    ow = gate_action.owner(doc, pair)
    if gate_action.is_chip(doc):
        return (doc.get("qubit_pairs", {}).get(ow["pair"], {}).get("macros") or {})
    return {a.get("name"): a for a in doc.get("additions") or []
            if isinstance(a, dict) and a.get("kind") == "macro" and a.get("target") == ow["target"]}


def current_numbers(doc: dict, rule: dict) -> dict:
    """Copy current source fields at apply time; a source-free rule uses numbers."""
    numbers = deepcopy(rule.get("numbers") or {})
    source = rule.get("source")
    if not source:
        return numbers
    macro = _macros(doc, source).get((rule.get("gate") or {}).get("name"))
    if not isinstance(macro, dict):
        raise ValueError("the source gate is unavailable: choose numbers")
    fields = macro if gate_action.is_chip(doc) else macro.get("fields") or {}
    for item in rule.get("items") or []:
        role = item["role"]
        field = item.get("field") or gate_action.ROLE_FIELDS.get(role, role)
        pulse = fields.get(field)
        holder = ["qubit_pairs", gate_action.owner(doc, source)["pair"], "macros",
                  rule["gate"]["name"], field]
        if isinstance(pulse, str) and pulse.startswith("#/"):
            if gate_action.is_chip(doc):
                ok, holder, pulse = additions._follow(doc, holder, pulse)
                if not ok:
                    raise ValueError("the source pulse is unavailable: choose numbers")
            else:
                path = pulse[2:].replace("/", ".")
                pulse = next((a.get("fields") for a in doc.get("additions") or []
                              if isinstance(a, dict) and a.get("target", "") + "." + a.get("name", "") == path), None)
        if isinstance(pulse, dict):
            for field, value in pulse.items():
                if gate_action.is_chip(doc) and additions.is_pointer(value):
                    ok, _, value = additions._follow(doc, holder + [field], value)
                    if not ok:
                        raise ValueError("the source number is unavailable: choose numbers")
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    numbers[role + "." + field] = value
    return numbers


def judge_pairs(doc: dict, pairs, rule: dict, manifest: dict | None = None, *, lab_check=None, index=None) -> list[dict]:
    """One result per pair; ``lab_check(writes, info=...)`` is called ONCE.

    A chip adapter supplies the existing _lab_write_refusal bound to its store.
    Its refused indices are mapped back to pairs, preserving partial success.
    Without a chip checker the verdict explicitly needs a check.
    """
    results, writes, write_pairs = [], [], []
    pairs = list(pairs)
    chip = gate_action.is_chip(doc)
    try:
        numbers = current_numbers(doc, rule)
        source_error = None
    except ValueError as exc:
        numbers, source_error = {}, str(exc)
    index = None if chip else (index if index is not None else additions.SpecIndex(doc))
    for pair in pairs:
        reason_suffix = gate_action.role_input(doc, pair)["reason"]
        result = {"pair": additions.quam_pair_id(gate_action.pair_members(doc, pair)),
                  "status": READY, "reason": "", "additions": [], "numbers": deepcopy(numbers)}
        def verdict(status, text):
            result.update(status=status, reason=text + "; " + reason_suffix)
        macros = _macros(doc, pair)
        name = (rule.get("gate") or {}).get("name")
        existing = macros.get(name)
        source_here = bool(rule.get("source")) and unordered_key(gate_action.pair_members(doc, pair)) == unordered_key(gate_action.pair_members(doc, rule["source"]))
        if existing is not None:
            if (rule.get("id") and isinstance(existing, dict) and existing.get("rule_id") == rule["id"]) or source_here:
                verdict(ALREADY, "this rule is already on this pair")
            else:
                verdict(CANT, f"a gate named {name} already exists")
        elif source_error:
            verdict(NEEDS_LOOK, source_error)
        else:
            action = dict(rule, numbers=numbers)
            resolved = gate_action.resolve(doc, pair, action)
            result["resolved"] = resolved
            if resolved["errors"]:
                choose = all("needs a look: choose" in e for e in resolved["errors"])
                verdict(NEEDS_LOOK if choose else CANT, "; ".join(resolved["errors"]))
            else:
                moving = gate_action.role_input(doc, pair)["moving_qubit"]
                desired = resolved["moving_qubit"]
                if moving and desired and moving != desired:
                    qubit = gate_action.role_owner(doc, pair, "moving")
                    verdict(CANT, f"another rule sets the moving qubit to {qubit}")
                else:
                    errors, warnings = [], []
                    for add in resolved["additions"]:
                        if chip:
                            why = additions.manifest_reason(add, manifest)
                            ok, target = additions._walk(doc, add["target"].split("."))
                            if not ok or not isinstance(target, dict):
                                why = f"{add['target']} is unavailable"
                            elif add["name"] in target:
                                why = f"{add['name']} already exists on {add['target']}"
                            if why:
                                errors.append(why)
                        else:
                            lint = additions.lint_proposal(doc, add, manifest, structural=False, others=(), index=index)
                            errors.extend(lint["errors"])
                            warnings.extend(lint["warnings"])
                        if not additions._class_entry(manifest, add["class"]):
                            warnings.append("class fields not checked")
                    if errors:
                        verdict(CANT, "; ".join(errors))
                    elif warnings:
                        verdict(NEEDS_LOOK, "; ".join(dict.fromkeys(warnings)) + ("; " + BUILD_CHECK if not chip else "; runtime check needed"))
                    elif chip and lab_check is None:
                        verdict(NEEDS_LOOK, "runtime check needed")
                    else:
                        verdict(READY, "SM's checks pass" + ("; " + BUILD_CHECK if not chip else ""))
                    if not errors:
                        result["additions"] = resolved["additions"]
                        for add in result["additions"]:
                            if rule.get("id"):
                                add["rule_id"] = rule["id"]
                                add["copied_value"] = {k: v for k, v in add["fields"].items()
                                                       if isinstance(v, (int, float)) and not isinstance(v, bool)}
                            if chip:
                                value = dict(deepcopy(add["fields"]), __class__=add["class"])
                                writes.append((add["target"] + "." + add["name"], value))
                                write_pairs.append(len(results))
        results.append(result)
    if chip and writes and lab_check is not None:
        info = {}
        refusal = lab_check(writes, info=info)
        affected = set()
        if refusal:
            message, indices = refusal
            affected = {write_pairs[i] for i in indices}
            for i in affected:
                results[i].update(status=CANT, reason=str(message) + "; " + gate_action.role_input(doc, pairs[i])["reason"])
        if info.get("notes"):
            for i in set(write_pairs) - affected:
                results[i].update(status=NEEDS_LOOK, reason="; ".join(info["notes"]) + "; " + gate_action.role_input(doc, pairs[i])["reason"])
    return results
