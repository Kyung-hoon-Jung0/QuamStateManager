"""Plan reviewed field moves and one-hop pointer rewrites over a working copy."""
from __future__ import annotations

import copy

from quam_state_manager.core.env_upgrade import objects
from quam_state_manager.core.pulse_index import (
    rewrite_referrer_pointer, pointer_to_abs, _derive_pointer, _flavor)
from quam_state_manager.core.state_env_baseline import _hash


def _leaves(node, path=""):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _leaves(value, f"{path}.{key}" if path else key)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _leaves(value, f"{path}.{i}" if path else str(i))
    else:
        yield path, node


def _parent(node, path):
    parts = path.split(".")
    for part in parts[:-1]:
        node = node[int(part)] if isinstance(node, list) else node[part]
    return node, parts[-1]


def plan(state, wiring, manifest, selections, reference=None):
    """Collect moves, rewrite one-hop referrers, then move leaves on copies."""
    classes = manifest.get("classes") or {}
    moved, skipped = [], []
    for selection in selections:
        if not selection.get("confirmed") or not selection.get("meaning_unchanged"):
            raise ValueError("confirm each row and that meaning and unit are unchanged")
        owner, old, new = (selection[k] for k in ("class", "old", "new"))
        if not old.isidentifier() or not new.isidentifier() or old == new:
            raise ValueError("invalid field move")
        for path, obj in objects(state):
            cls = obj["__class__"]
            entry = classes.get(cls) or {}
            if owner not in [cls, *(entry.get("bases") or [])] or old not in obj:
                continue
            spec = ((entry.get("fields") or {}).get(old) or
                    ((((reference or {}).get("classes") or {}).get(cls) or {}).get("fields") or {}).get(old) or {})
            declared = spec.get("defining_class")
            # In the current schema the old key may already be unknown. The
            # owner request was verified against the reference by the caller.
            if declared and declared != owner:
                continue
            if not entry.get("importable") or new in obj:
                skipped.append({"path": path, "reason": "class not importable" if not entry.get("importable")
                                else "new key already exists"})
                continue
            old_path = f"{path}.{old}" if path else old
            new_path = f"{path}.{new}" if path else new
            if any(m["old"] == old_path for m in moved):
                raise ValueError("the same leaf was selected twice")
            moved.append({"old": old_path, "new": new_path})
    state_copy, wiring_copy = copy.deepcopy(state), copy.deepcopy(wiring)
    rewrites = []
    for tree in (state_copy, wiring_copy):
        for holder, value in list(_leaves(tree)):
            if not isinstance(value, str) or not value.startswith(("#/", "#./", "#../")):
                continue
            updated = value
            for move in moved:
                rewritten = rewrite_referrer_pointer(value, holder, move["old"], move["new"])
                if rewritten is not None:
                    updated = rewritten
                    break
            for move in moved:
                if holder == move["old"] or holder.startswith(move["old"] + "."):
                    target = pointer_to_abs(updated, holder.split("."),
                                            {**state_copy, **wiring_copy})
                    if target:
                        new_holder = move["new"] + holder[len(move["old"]):]
                        updated = _derive_pointer(_flavor(updated), new_holder.split("."), target)
                    break
            if updated != value:
                parent, leaf = _parent(tree, holder)
                parent[int(leaf) if isinstance(parent, list) else leaf] = updated
                rewrites.append({"path": holder, "old": value, "new": updated})
    for move in moved:
        parent, leaf = _parent(state_copy, move["old"])
        new_parent, new_leaf = _parent(state_copy, move["new"])
        new_parent[new_leaf] = parent.pop(leaf)
    result = {"state": state_copy, "wiring": wiring_copy, "moves": moved,
              "rewrites": rewrites, "skipped": skipped, "selections": selections}
    result["signature"] = _hash(result)
    return result


def add_removals(planned, removals):
    """Fold reviewed removals of fields the environment no longer declares into
    a plan. Each removal names its leaf paths; a leaf another value points at is
    skipped with the referrer named, never removed."""
    state_copy, wiring_copy = planned["state"], planned["wiring"]
    referrers = []
    for tree in (state_copy, wiring_copy):
        for holder, value in _leaves(tree):
            if isinstance(value, str) and value.startswith(("#/", "#./", "#../")):
                target = pointer_to_abs(value, holder.split("."),
                                        {**state_copy, **wiring_copy})
                if target:
                    referrers.append((holder, ".".join(str(p) for p in target)))
    removed = []
    for removal in removals:
        for path in removal["paths"]:
            try:
                parent, leaf = _parent(state_copy, path)
                key = int(leaf) if isinstance(parent, list) else leaf
                value = parent[key]
            except (KeyError, IndexError, ValueError, TypeError):
                planned["skipped"].append({"path": path, "reason": "no longer present"})
                continue
            using = [holder for holder, target in referrers
                     if (target == path or target.startswith(path + "."))
                     and not (holder == path or holder.startswith(path + "."))]
            if using:
                planned["skipped"].append({"path": path, "reason": "referenced by " + using[0]})
                continue
            removed.append({"path": path, "value": copy.deepcopy(value)})
            del parent[key]
    planned["removals"] = removed
    planned["signature"] = _hash({k: v for k, v in planned.items() if k != "signature"})
    return planned


def replay(state, wiring, manifest, reviewed, reference=None):
    """Apply exactly the person-reviewed renames and removals to another copy of
    the same chip (the reference inputs), and nothing else."""
    planned = plan(state, wiring, manifest, list(reviewed.get("renames") or []), reference)
    return add_removals(planned, [{"paths": list(paths)} for paths in reviewed.get("removals") or []])


def reviewed_note(reviewed):
    """One phrase naming what was applied to the reference inputs."""
    renames = len(reviewed.get("renames") or [])
    removals = len(reviewed.get("removals") or [])
    parts = []
    if renames:
        parts.append(f"{renames} reviewed rename{'s' if renames != 1 else ''}")
    if removals:
        parts.append(f"{removals} reviewed removal{'s' if removals != 1 else ''}")
    return " and ".join(parts)


def apply(modifier, planned):
    """One modifier group for pointer edits and every leaf move; rollback on error."""
    with modifier.store._lock:
        group = modifier.new_group_id()
        entries = []
        try:
            for rewrite in planned["rewrites"]:
                entries.append(modifier.set_value(rewrite["path"], rewrite["new"], coerce=False,
                                                   group_id=group))
            for move in planned["moves"]:
                entries.extend(modifier.rename_subtree(move["old"], move["new"], group_id=group))
            for removal in planned.get("removals") or []:
                entries.append(modifier.delete_subtree(removal["path"], group_id=group))
        except Exception:
            modifier._rollback(entries)
            raise
    return entries
