"""Explicit identity-edit offers and tracked, atomic port moves."""
from __future__ import annotations

import copy

from quam_state_manager.core.loader import _walk
from quam_state_manager.core.port_identity import (
    IDENTITY_FIELDS, identity_disagrees, normalize_identity,
    pointer_landing, pointer_landings,
)


def edit_offer(path, value):
    parts = path.split(".")
    if len(parts) != 6 or parts[0] != "ports" or parts[-1] not in IDENTITY_FIELDS:
        return None
    index = IDENTITY_FIELDS.index(parts[-1]) + 2
    if not identity_disagrees(value, parts[index]):
        return None
    normalized = normalize_identity(value)
    if isinstance(normalized, bool):
        return None
    if index != 2 and not isinstance(normalized, int):
        return None
    segment = str(normalized)
    if not segment or any(c in segment for c in ". /#\\"):
        return None
    new_parts = parts[:5]
    new_parts[index] = segment
    return {"path": path, "field": parts[-1], "key_value": parts[index],
            "typed_value": value, "old_path": ".".join(parts[:5]),
            "new_path": ".".join(new_parts),
            "message": (f"{path}: dict key says {parts[index]!r}, but the typed "
                        f"identity is {value!r}. QUAM uses the identity fields for "
                        "the physical port. Fix the dict key and its pointer users "
                        "too, or keep as typed."),
            "choices": ["fix", "keep"]}


def edit_offers(path, value):
    """Include identity leaves supplied through whole-object write doors."""
    if isinstance(value, dict):
        return [offer for key, child in value.items()
                for offer in edit_offers(path + "." + str(key), child)]
    offer = edit_offer(path, value)
    if offer:
        return [offer]
    parts = path.split('.')
    if (len(parts) == 6 and parts[0] == 'ports' and parts[-1] in IDENTITY_FIELDS
            and identity_disagrees(value, parts[IDENTITY_FIELDS.index(parts[-1]) + 2])):
        return [{"path": path, "field": parts[-1], "typed_value": value,
                 "old_path": '.'.join(parts[:5]), "new_path": None,
                 "message": f"{path}: the typed identity {value!r} disagrees with its key. "
                            "This value cannot form a dictionary key; keep as typed or cancel.",
                 "choices": ["fix", "keep"]}]
    return []


def pointer_retargets(entries):
    """JSON move-result metadata for pointer edits in tracked entries."""
    return [{"path": e.dot_path, "pointer": e.new_value} for e in entries
            if isinstance(e.new_value, str) and e.new_value.startswith("#")]


def stage_write(modifier, path, value, *, create=False, group_id=None, **kwargs):
    """Write a container/created leaf and fix its claimed keys atomically."""
    offers = edit_offers(path, value)
    if any(offer['new_path'] is None for offer in offers):
        raise ValueError("This identity cannot form a port dictionary key; keep as typed or choose a valid key")
    gid = group_id or modifier.new_group_id()
    entries, moves = [], []
    with modifier.store._lock:
        try:
            writer = modifier.create_subtree if create else modifier.set_value
            entries.append(writer(path, value, group_id=gid, **kwargs))
            for original in offers:
                leaf = original['path']
                for old, new in moves:
                    if leaf.startswith(old + '.'):
                        leaf = new + leaf[len(old):]
                typed = modifier.store.get_value(leaf)
                offer = edit_offer(leaf, typed)
                if offer:
                    added, _target = move_port(modifier, offer, typed, group_id=gid)
                    entries.extend(added)
                    moves.append((offer['old_path'], offer['new_path']))
        except Exception:
            modifier._rollback(entries)
            raise
    return entries, moves


def move_port(modifier, offer, value, *, group_id=None):
    """Caller holds the store lock. All changes share one undo group."""
    store = modifier.store
    old, new = offer["old_path"], offer["new_path"]
    normalized = normalize_identity(value)
    segment = str(normalized)
    if (normalized is None or isinstance(normalized, bool)
            or offer["field"] != "controller_id" and not isinstance(normalized, int)
            or not segment or any(c in segment for c in ". /#\\")):
        raise ValueError("This value cannot be used as a port dict key; keep as typed or choose a valid key")
    try:
        store.get_value(new)
    except KeyError:
        pass
    else:
        raise ValueError(f"Cannot move port: {new} already exists; no changes made")

    payload = copy.deepcopy(store.get_value(old))
    # Validate/coerce exactly as a normal field edit, before changing structure.
    payload[offer["field"]] = modifier._checked_value(
        offer["path"], payload.get(offer["field"]), value, coerce=True, enforce=True)
    before = pointer_landings(store.merged)
    pointers = {p: v for p, v, _ in _walk(store.merged)
                if isinstance(v, str) and v.startswith('#')}
    def moved(path):
        return new + path[len(old):] if path == old or path.startswith(old + '.') else path

    # Plan against a private post-move tree. Resolve each original pointer at
    # its own holder, so chains and pointers crossing aliases are included.
    planned = copy.deepcopy(store.merged)
    def parent(root, path):
        parts = path.split('.')
        for part in parts[:-1]:
            root = root[int(part)] if isinstance(root, list) else root.setdefault(part, {})
        return root, parts[-1]
    source, leaf = parent(planned, old)
    del source[leaf]
    dest, leaf = parent(planned, new)
    dest[leaf] = copy.deepcopy(payload)
    candidates = {p: moved(t) for p, t in before.items()
                  if t == old or t.startswith(old + '.')
                  or p == old or p.startswith(old + '.')}
    # Set all affected users first; then retain a relative spelling only when
    # it still resolves to the intended target in the complete post-move tree.
    for path, target in candidates.items():
        dest, leaf = parent(planned, moved(path))
        dest[int(leaf) if isinstance(dest, list) else leaf] = '#/' + target.replace('.', '/')
    retargets = []
    for path, target in candidates.items():
        holder = moved(path)
        dest, leaf = parent(planned, holder)
        key = int(leaf) if isinstance(dest, list) else leaf
        absolute = dest[key]
        dest[key] = pointers[path]
        if pointer_landing(planned, holder) != target:
            dest[key] = absolute
        if dest[key] != pointers[path]:
            retargets.append((holder, dest[key]))
    gid = group_id or modifier.new_group_id()
    entries = []
    from quam_state_manager.core.pending_tray import batch
    with batch(store):
        try:
            # A controller/FEM move can require previously absent parent dicts.
            parts = new.split(".")
            for n in range(2, len(parts)):
                parent_path = ".".join(parts[:n])
                try:
                    store.get_value(parent_path)
                except KeyError:
                    entries.append(modifier.create_subtree(parent_path, {}, group_id=gid))
            entries.extend(modifier.rename_subtree(old, new, payload, group_id=gid))
            for path, pointer in retargets:
                entries.append(modifier.set_value(path, pointer, group_id=gid))
            changed = [moved(p) for p in pointers
                       if pointer_landing(store.merged, moved(p)) != (
                           moved(before[p]) if p in before else None)]
            if changed:
                raise ValueError("Port move would change pointer landings: " + ", ".join(changed))
            offer['retargets'] = [{'path': p, 'pointer': v} for p, v in retargets]
        except Exception:
            modifier._rollback(entries)
            raise
    return entries, new + "." + offer["field"]
