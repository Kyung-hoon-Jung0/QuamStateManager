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


def diff_and_signature(resolved, current, numeric_changes=()):
    """Structural field overrides and their exact pair-local input identity."""
    def fields(rows):
        return {(a["kind"], a["name"], k): v for a in rows
                for k, v in dict(a.get("fields") or {}, target=a["target"], class_=a.get("class")).items()
                if a["kind"] == "macro" or not isinstance(v, (int, float)) or isinstance(v, bool)}
    old, new = fields(current), fields(resolved["additions"])
    delta = [{"kind": key[0], "name": key[1], "field": key[2], "old": old.get(key), "new": new.get(key)}
             for key in sorted(old.keys() | new.keys()) if old.get(key) != new.get(key)]
    return delta + list(numeric_changes), gate_action.input_signature(resolved["inputs"])


def _role_pulses(rows, rule):
    """Recover role identity from stored gate references, including old names."""
    macro = next((a for a in rows if a.get("kind") == "macro"), {})
    by_pointer = {"#/" + a["target"].replace(".", "/") + "/" + a["name"]: a
                  for a in rows if a.get("kind") == "pulse"}
    out = {}
    for item in rule.get("items") or []:
        field = item.get("field") or gate_action.ROLE_FIELDS.get(item["role"], item["role"])
        ptr = (macro.get("fields") or {}).get(field)
        if isinstance(ptr, str) and ptr in by_pointer:
            out[item["role"]] = by_pointer[ptr]
    return out


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
           if (rule.get("id") and a.get("rule_id") == rule["id"]) or
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
        current = bundle(spec, rule, pair)
        existing = bool(current) and (bool(rule.get("id")) and any(a.get("rule_id") == rule["id"] for a in current) or
                    bool(rule.get("source")) and key == unordered_key(gate_action.pair_members(spec, rule["source"])))
        previous = _role_pulses(current, rule) if existing else {}
        local = action
        if previous:
            local = deepcopy(action)
            # Stored names never change, also after a role move or a pair-order
            # flip: Regenerate carries values by path.
            local["keep_names"] = {role: pulse["name"] for role, pulse in previous.items()}
            # A role move carries this pair's calibration. Source recalibration
            # supplies NEW instances only; it never retunes existing pairs.
            for role, pulse in previous.items():
                for field, value in (pulse.get("fields") or {}).items():
                    baseline = (pulse.get("copied_value") or {}).get(field, value)
                    if isinstance(value, (int, float)) and not isinstance(value, bool) and (rule.get("source") or value != baseline):
                        local.setdefault("numbers", {})[role + "." + field] = value
        choice = choices.get(pid) or next((a.get("choice_here") for a in current
                     if rule.get("id") and a.get("rule_id") == rule["id"] and a.get("choice_here")), None)
        if choice in ("control", "target"):
            local = deepcopy(local)
            for item in local.get("items") or []:
                if item.get("on_choice", "auto") == "auto" and item["role"] != "coupler":
                    item["on_choice"] = choice if item["role"] != "low" else ("target" if choice == "control" else "control")
        # Source numbers have already been captured; judge must not read the removed source.
        judged = add_verdict.judge_pairs(clean, [pair], dict(local, source=None), manifest, index=index)[0]
        resolved = gate_action.resolve(clean, pair, local)
        numeric_changes = []
        for role, pulse in _role_pulses(resolved["additions"], rule).items():
            old = previous.get(role)
            if not old:
                continue
            for field, value in (pulse.get("fields") or {}).items():
                before = (old.get("fields") or {}).get(field)
                if isinstance(value, (int, float)) and not isinstance(value, bool) and before != value:
                    numeric_changes.append(dict(kind="pulse", name=pulse["name"], field=field, old=before, new=value))
        delta, instance_sig = diff_and_signature(resolved, current, numeric_changes)
        # A role move is a changed pair fact, not a local class/structure edit.
        # Compare by role so an orientation-dependent name change is harmless.
        next_pulses = _role_pulses(resolved["additions"], rule)
        def structure(pulse):
            return dict(class_=pulse.get("class"), fields={k: v for k, v in (pulse.get("fields") or {}).items()
                        if not isinstance(v, (int, float)) or isinstance(v, bool)})
        source_here = bool(rule.get("source")) and key == unordered_key(gate_action.pair_members(spec, rule["source"]))
        role_fields = {item.get("field") or gate_action.ROLE_FIELDS.get(item["role"], item["role"])
                       for item in rule.get("items") or []}
        gate_edit = any(d["kind"] == "macro" and d["field"] not in role_fields and d["field"] != "target" for d in delta)
        edited = bool(existing and not source_here and any(
            structure(old) != structure(next_pulses.get(role, {})) for role, old in previous.items()))
        edited = edited or bool(existing and not source_here and gate_edit)
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
            if choice:
                a["choice_here"] = choice
        for role, pulse in _role_pulses(planned, rule).items():
            old = previous.get(role)
            if old:
                for field, value in (old.get("copied_value") or {}).items():
                    if (pulse.get("fields") or {}).get(field) == (old.get("fields") or {}).get(field):
                        pulse["copied_value"][field] = value
        # What apply() will actually write for this row, so a tick never promises more.
        needs_choice = bool(resolved["errors"]) and all(e.endswith("needs a look: choose") for e in resolved["errors"])
        if judged["status"] == add_verdict.READY:
            writable = True
        elif judged["status"] != add_verdict.NEEDS_LOOK:
            writable = False
        elif existing:
            writable = not resolved["errors"] and bool(planned)
        elif source_error:
            writable = False
        else:
            writable = needs_choice or (choice in ("control", "target") and not resolved["errors"] and bool(planned))
        judged.update(writable=writable, needs_choice=needs_choice and not existing)
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
    Apply the resolved moving choice and requested default with the bundle.
    Returns ``{spec, added, skipped, rule}``; the input spec is not changed.
    """
    # The signature covers the rule exactly as the person checked it; an id
    # for a new rule is assigned only after the check passed.
    plan = expand(spec, rule, manifest, choices=choices)
    if plan["rule_sig"] != rule_sig:
        raise StaleRule(plan["rule_sig"])
    rule = deepcopy(rule)
    rule.setdefault("id", "r" + __import__("uuid").uuid4().hex[:8])
    if not rule.get("source") or not any(r.get("id") == rule["id"] for r in spec.get("addition_rules") or []):
        rule["numbers"] = add_verdict.current_numbers(spec, rule)
    by_pid = {additions.quam_pair_id(p): row for p, row in zip(spec.get("qubit_pairs") or [], plan["pairs"])}
    choices = choices or {}
    new, added, skipped = deepcopy(spec), [], []
    for pid in dict.fromkeys(pairs or []):
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
    for pid in added:
        row = by_pid[pid]
        if row.get("moving_qubit"):
            new.setdefault("populate", {}).setdefault("pairs", {}).setdefault(row["long"], {})["moving_qubit"] = row["moving_qubit"]
        if rule.get("gate", {}).get("as_default"):
            target = gate_action.owner(new, pid)["target"]
            new["additions"] = [a for a in new["additions"] if not (a.get("kind") == "default_gate" and a.get("target") == target)]
            new["additions"].append(dict(kind="default_gate", target=target, name="cz", to=rule["gate"]["name"], rule_id=rule["id"]))
    # Adopting a hand gate as the source adds provenance, never renames it.
    if rule.get("source"):
        for a in bundle(new, rule, rule["source"]):
            a["rule_id"] = rule["id"]
            a.setdefault("copied_value", {k: v for k, v in (a.get("fields") or {}).items()
                                         if isinstance(v, (int, float)) and not isinstance(v, bool)})
    return {"spec": new, "added": added, "skipped": skipped, "rule": _store_rule(new, rule)}
