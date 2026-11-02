"""Explicit identity-edit offers and tracked, atomic port moves."""
from __future__ import annotations

import copy

from quam_state_manager.core.loader import _walk
from quam_state_manager.core.port_identity import IDENTITY_FIELDS


def edit_offer(path, value):
    parts = path.split(".")
    if len(parts) != 6 or parts[0] != "ports" or parts[-1] not in IDENTITY_FIELDS:
        return None
    index = IDENTITY_FIELDS.index(parts[-1]) + 2
    if str(value) == parts[index]:
        return None
    new_parts = parts[:5]
    new_parts[index] = str(value)
    return {"path": path, "field": parts[-1], "key_value": parts[index],
            "typed_value": value, "old_path": ".".join(parts[:5]),
            "new_path": ".".join(new_parts),
            "message": (f"{path}: dict key says {parts[index]!r}, but the typed "
                        f"identity is {value!r}. QUAM uses the identity fields for "
                        "the physical port. Fix the dict key and its pointer users "
                        "too, or keep as typed."),
            "choices": ["fix", "keep"]}


def move_port(modifier, offer, value):
    """Caller holds the store lock. All changes share one undo group."""
    store = modifier.store
    old, new = offer["old_path"], offer["new_path"]
    segment = str(value)
    if not segment or any(c in segment for c in ". /#\\"):
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
        offer["path"], payload[offer["field"]], value, coerce=True, enforce=True)
    old_ptr, new_ptr = ("#/" + p.replace(".", "/") for p in (old, new))
    retargets = [(p, new_ptr + v[len(old_ptr):]) for p, v, _ in _walk(store.merged)
                 if isinstance(v, str) and (v == old_ptr or v.startswith(old_ptr + "/"))]
    gid = modifier.new_group_id()
    entries = []
    from quam_state_manager.core.pending_tray import batch
    with batch(store):
        try:
            # A controller/FEM move can require previously absent parent dicts.
            parts = new.split(".")
            for n in range(2, len(parts)):
                parent = ".".join(parts[:n])
                try:
                    store.get_value(parent)
                except KeyError:
                    entries.append(modifier.create_subtree(parent, {}, group_id=gid))
            entries.extend(modifier.rename_subtree(old, new, payload, group_id=gid))
            for path, pointer in retargets:
                if path.startswith(old + "."):
                    path = new + path[len(old):]
                entries.append(modifier.set_value(path, pointer, group_id=gid))
        except Exception:
            modifier._rollback(entries)
            raise
    return entries, new + "." + offer["field"]
