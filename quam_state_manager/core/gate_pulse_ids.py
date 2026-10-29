"""Holder-key IDs for pulses referenced by two-qubit gate slots."""

from .pointer_resolver import _compute_resolved_path, is_pointer, resolve_pointer
from .pulse_index import GATE_SLOTS

GATE_ID_SLOTS = GATE_SLOTS + ("flux_pulse_high", "flux_pulse_low")


def _holder(root, pointer, path, seen):
    """Follow the resolver's paths, including pointers crossed mid-path."""
    token = (pointer, path)
    if token in seen or pointer.startswith("#./"):
        return None
    seen.add(token)
    if not pointer.startswith(("#/", "#../")):
        return None
    target = _compute_resolved_path(root, pointer, path)
    node, real = root, ()
    for segment in target:
        if is_pointer(node):
            got = _holder(root, node, real, seen)
            if got is None:
                return None
            real, node = got
        if isinstance(node, dict) and segment in node:
            node = node[segment]
        elif isinstance(node, list):
            try:
                node = node[int(segment)]
            except (ValueError, IndexError):
                return None
        else:
            return None
        real = real + (segment,)
    if is_pointer(node):
        return _holder(root, node, real, seen)
    # Use SM's value resolver as the authority for successful resolution.
    if resolve_pointer(root, pointer, path) is not node:
        return None
    return real, node


def gate_pulse_targets(merged):
    """Yield (operation path, key, slot path, body) in stable slot order."""
    pairs = merged.get("qubit_pairs") or {}
    if not isinstance(pairs, dict):
        return
    for pair_key, pair in pairs.items():
        macros = pair.get("macros") if isinstance(pair, dict) else None
        if not isinstance(macros, dict):
            continue
        for macro_key, macro in macros.items():
            if not isinstance(macro, dict):
                continue
            for slot in GATE_ID_SLOTS:
                value = macro.get(slot)
                if not is_pointer(value):
                    continue
                slot_path = ("qubit_pairs", pair_key, "macros", macro_key, slot)
                got = _holder(merged, value, slot_path, set())
                if got is None:
                    continue
                path, body = got
                if (len(path) >= 2 and path[-2] == "operations"
                        and isinstance(body, dict)):
                    yield ".".join(path), path[-1], ".".join(slot_path), body


def missing_gate_ids(merged: dict) -> list[tuple[str, str, str]]:
    """Return unique pointer-target operations whose ID is null or absent."""
    found, seen = [], set()
    for path, key, slot, body in gate_pulse_targets(merged):
        if path not in seen and body.get("id") is None:
            seen.add(path)
            found.append((path, key, slot))
    return found


def fill_edits(merged: dict) -> list[dict]:
    """Edits for the ordinary atomic field-edit door."""
    return [{"path": path + ".id", "value": key}
            for path, key, _slot in missing_gate_ids(merged)]
