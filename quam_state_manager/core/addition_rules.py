"""Read-only rule expansion and explicit, signature-checked spec transactions."""
from __future__ import annotations

from copy import deepcopy
from quam_state_manager.core import additions, add_verdict, gate_action
from quam_state_manager.core.add_names import unordered_key


def bundle(spec, rule, pair):
    """Find a gate and its directly referenced pulse additions, preserving names."""
    target = gate_action.owner(spec, pair)["target"]
    gate = next((a for a in spec.get("additions") or [] if a.get("kind") == "macro"
                 and a.get("target") == target and a.get("name") == rule["gate"]["name"]), None)
    if gate is None:
        return []
    refs = set(v for v in (gate.get("fields") or {}).values() if isinstance(v, str))
    return [a for a in spec.get("additions") or [] if a is gate or
            (a.get("kind") == "pulse" and "#/" + a["target"].replace(".", "/") + "/" + a["name"] in refs)]


def diff_and_signature(resolved, current):
    """Structural field overrides and their exact pair-local input identity."""
    def fields(rows):
        return {(a["kind"], a["name"], k): v for a in rows
                for k, v in dict(a.get("fields") or {}, target=a["target"], class_=a.get("class")).items()
                if not isinstance(v, (int, float)) or isinstance(v, bool)}
    old, new = fields(current), fields(resolved["additions"])
    delta = [{"kind": key[0], "name": key[1], "field": key[2], "old": old.get(key), "new": new.get(key)}
             for key in sorted(old.keys() | new.keys()) if old.get(key) != new.get(key)]
    return delta, gate_action.input_signature(resolved["inputs"])


def role_swap_reason(delta, pair, basis):
    """``control and target swapped by ...`` when the gate's role pointers moved
    between the pair's two qubits (a CZ orientation flip), else None. [derived]"""
    c, t = map(str, pair)
    moved = set()
    for d in delta:
        if d["kind"] != "macro":
            continue
        qo, qn = (_qubit_of_pointer(d.get(k)) for k in ("old", "new"))
        if qo in (c, t) and qn in (c, t) and qo != qn:
            moved.add((qo, qn))
    return f"control and target swapped {basis}" if moved else None


def _qubit_of_pointer(v):
    if isinstance(v, str) and v.startswith("#/qubits/"):
        return v.split("/")[2]
    return None


def expand(spec, rule, manifest=None, *, choices=None, pending=False):
    """Plan every pair without mutating inputs or generating any files."""
    choices = choices or {}
    own = {id(a) for p in spec.get("qubit_pairs") or [] for a in bundle(spec, rule, p)
           if a.get("rule_id") == rule.get("id") or
           (rule.get("source") and unordered_key(gate_action.pair_members(spec, rule["source"])) == unordered_key(p))}
    clean = dict(spec, additions=[a for a in spec.get("additions") or [] if id(a) not in own])
    # Read source numbers before excluding its gate from collision checks.
    try:
        action = dict(rule, source=None, numbers=add_verdict.current_numbers(spec, rule))
        source_error = None
    except ValueError as exc:
        action, source_error = dict(rule, source=None), str(exc)
    action["source"] = rule.get("source")
    rows = []
    index = additions.SpecIndex(clean)
    for pair in spec.get("qubit_pairs") or []:
        pid = additions.quam_pair_id(pair)
        key = unordered_key(pair)
        local = action
        if choices.get(pid) in ("control", "target"):
            local = deepcopy(action)
            for item in local.get("items") or []:
                if item.get("on_choice", "auto") == "auto" and item["role"] != "coupler":
                    item["on_choice"] = choices[pid] if item["role"] != "low" else ("target" if choices[pid] == "control" else "control")
        # Source numbers have already been captured; judge must not read the removed source.
        judged = add_verdict.judge_pairs(clean, [pair], dict(local, source=None), manifest, index=index)[0]
        resolved = gate_action.resolve(clean, pair, local)
        current = bundle(spec, rule, pair)
        delta, instance_sig = diff_and_signature(resolved, current)
        existing = bool(current) and (any(a.get("rule_id") == rule.get("id") for a in current) or
                    bool(rule.get("source")) and key == unordered_key(gate_action.pair_members(spec, rule["source"])))
        edited = bool(existing and delta)
        if existing:
            look = delta and (rule.get("reviewed") or {}).get(key) != instance_sig
            why = "this rule is already on this pair"
            if delta:
                why = role_swap_reason(delta, pair, gate_action.role_input(clean, pair)["reason"]) or "pair facts changed"
            judged.update(status=add_verdict.NEEDS_LOOK if look else add_verdict.ALREADY, reason=why)
        elif source_error:
            judged.update(status=add_verdict.NEEDS_LOOK, reason=source_error)
        if key in (rule.get("excluded") or []):
            judged.update(status=add_verdict.CANT, reason="excluded from this rule")
        planned = resolved["additions"] if not resolved["errors"] else judged.get("additions", [])
        for a in planned:
            if rule.get("id"):
                a["rule_id"] = rule["id"]
            a["copied_value"] = {k: v for k, v in (a.get("fields") or {}).items()
                                 if isinstance(v, (int, float)) and not isinstance(v, bool)}
            if choices.get(pid):
                a["choice_here"] = choices[pid]
        judged.update(additions=planned, instance_sig=instance_sig, diff_vs_current=delta if existing else [],
                      edited=edited, existing=existing, key=key, choices=resolved["choices"],
                      errors=list(resolved["errors"]),
                      moving_qubit=resolved["moving_qubit"], long=additions._long(pair),
                      control=str(pair[0]))
        rows.append(judged)
    groups = {s: [r for r in rows if r["status"] == s] for s in
              (add_verdict.READY, add_verdict.NEEDS_LOOK, add_verdict.CANT, add_verdict.ALREADY)}
    # Include all stale-sensitive facts, even those that don't alter today's verdict.
    inputs = {k: spec.get(k) for k in ("qubit_pairs", "populate", "lines", "qubit_class", "qubit_class_overrides",
              "quam_class", "pair_gate", "qubits")}
    inputs.update(rule=rule, manifest=manifest, pending=pending, choices=choices,
                  additions=spec.get("additions"), result=rows)
    return dict(ok=True, pending=pending, groups=groups, pairs=rows,
                rule_sig=gate_action.input_signature(inputs))


class StaleRule(Exception):
    """The chip changed since the rule was checked; the caller answers 409."""

    def __init__(self, rule_sig):
        super().__init__("The chip changed since this was checked — check again.")
        self.rule_sig = rule_sig


def _store_rule(spec, rule):
    """*rule* with an id and revision, stored in ``spec['addition_rules']``."""
    import uuid
    rule = deepcopy(rule)
    rule.setdefault("id", "r" + uuid.uuid4().hex[:8])
    rule.setdefault("rev", 1)
    rules = [r for r in spec.get("addition_rules") or [] if r.get("id") != rule["id"]]
    spec["addition_rules"] = rules + [rule]
    return rule


def apply(spec, rule, rule_sig, pairs, manifest, choices=None):
    """Write the planned additions of the listed pairs; refuse a stale check.

    Only a Ready pair, or a Needs-a-look pair a ``choices`` entry resolves
    cleanly, is written. Anything else listed is skipped with its reason.
    Pair defaults (``gate.as_default``) are left untouched here.
    Returns ``{spec, added, skipped, rule}``; the input spec is not changed.
    """
    # The signature covers the rule exactly as the person checked it; an id
    # for a new rule is assigned only after the check passed.
    plan = expand(spec, rule, manifest, choices=choices)
    if plan["rule_sig"] != rule_sig:
        raise StaleRule(plan["rule_sig"])
    rule = deepcopy(rule)
    rule.setdefault("id", "r" + __import__("uuid").uuid4().hex[:8])
    by_pid = {additions.quam_pair_id(p): row for p, row in zip(spec.get("qubit_pairs") or [], plan["pairs"])}
    choices = choices or {}
    new, added, skipped = deepcopy(spec), [], []
    for pid in pairs or []:
        row = by_pid.get(pid)
        if row is None:
            skipped.append({"pair": pid, "reason": "no such pair"})
        elif (row.get("existing") and not row.get("errors") and row.get("additions")
              and row["status"] in (add_verdict.NEEDS_LOOK, add_verdict.ALREADY)):
            # Reset to rule / Apply change: the rule's own instance on this
            # pair is replaced by the re-planned one (the person pressed it).
            pair = next(p for p in spec.get("qubit_pairs") or [] if additions.quam_pair_id(p) == pid)
            gone = {id(a) for a in bundle(new, rule, pair)}
            new["additions"] = [a for a in new.get("additions") or [] if id(a) not in gone]
            rows = deepcopy(row["additions"])
            for a in rows:
                a["rule_id"] = rule["id"]
            new["additions"].extend(rows)
            added.append(pid)
        elif row["status"] == add_verdict.READY or (
                row["status"] == add_verdict.NEEDS_LOOK and choices.get(pid) in ("control", "target")
                and not row.get("errors") and row.get("additions")):
            rows = deepcopy(row["additions"])
            for a in rows:
                a["rule_id"] = rule["id"]
            new.setdefault("additions", []).extend(rows)
            added.append(pid)
        else:
            skipped.append({"pair": pid, "reason": row.get("reason") or row["status"]})
    return {"spec": new, "added": added, "skipped": skipped, "rule": _store_rule(new, rule)}
