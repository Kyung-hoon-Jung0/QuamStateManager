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


# -- by-name plays -----------------------------------------------------------
#
# quam_builder's CZGate plays ``moving_qubit.z.play(get_pulse_name(pulse))``;
# ``get_pulse_name`` returns ``pulse.id`` when it is set. So an INLINE slot
# pulse whose ``id`` names a channel operation makes the gate play THAT
# operation by name -- no pointer names it, and a rename/delete of the op
# leaves the gate playing a name that no longer exists (a KeyError at
# apply() time; generate_config() still passes).

#: the label the Pulses page shows beside such a reference
BY_NAME_LABEL = "plays by name"


def _resolved_dict_path(merged, path):
    """The resolved path of the dict at (or pointed to by) *path*, or None."""
    from .pointer_path import resolve_field_target
    from .pointer_path import _walk
    ok, raw = _walk(merged, path.split("."))
    if not ok:
        return None
    if isinstance(raw, dict):
        return path
    if not is_pointer(raw):
        return None
    rt = resolve_field_target(merged, path)
    rp = rt.get("resolved_path") if rt.get("resolvable") else None
    if not rp:
        return None
    ok, val = _walk(merged, rp.split("."))
    return rp if ok and isinstance(val, dict) else None


def _slot_name(value, slot_path):
    """``(holder, name)`` a slot plays by name: an inline pulse dict's literal
    ``id`` (holder ``<slot>.id``), or a plain operation name string (holder
    the slot itself). A pointer slot, an inferred id, or a dict without an
    id is not counted here (the pointer path handles pointers)."""
    if isinstance(value, dict):
        if not isinstance(value.get("__class__"), str):
            return None, None
        pid = value.get("id")
        if isinstance(pid, str) and pid and not is_pointer(pid):
            return f"{slot_path}.id", pid
        return None, None
    if isinstance(value, str) and value and not is_pointer(value):
        return slot_path, value
    return None, None


def _named_slot_targets(merged):
    """Yield ``(channels, holder, name, slot_path, strict)`` for every
    pair-gate slot that names an operation (see :func:`named_slot_plays`).
    ``strict``: the gate plays this slot on exactly these channels, so the
    name must exist on one of them (any other id-carrying slot may be played
    on any of the pair's channels, or not at all)."""
    pairs = merged.get("qubit_pairs") if isinstance(merged, dict) else None
    if not isinstance(pairs, dict):
        return
    for pk, pair in pairs.items():
        if not isinstance(pair, dict) or not isinstance(pair.get("macros"), dict):
            continue
        base = f"qubit_pairs.{pk}"
        ctl = _resolved_dict_path(merged, f"{base}.qubit_control") if "qubit_control" in pair else None
        tgt = _resolved_dict_path(merged, f"{base}.qubit_target") if "qubit_target" in pair else None
        moving, other = (tgt, ctl) if pair.get("moving_qubit") == "target" else (ctl, tgt)
        cpl = (_resolved_dict_path(merged, f"{base}.coupler")
               if pair.get("coupler") is not None else None)
        chans = {
            "flux_pulse_qubit": [f"{moving}.z"] if moving else [],
            "flux_pulse_target": [f"{other}.z"] if other else [],
            "coupler_flux_pulse": [cpl] if cpl else [],
        }
        anywhere = [c for c in (chans["flux_pulse_qubit"] + chans["flux_pulse_target"]
                                + chans["coupler_flux_pulse"])]
        for mk, mac in pair["macros"].items():
            if not isinstance(mac, dict):
                continue
            mp = f"{base}.macros.{mk}"
            for slot, value in mac.items():
                if slot in ("__class__", "id", "spectator_qubits"):
                    continue
                if slot == "spectator_qubits_control" and isinstance(value, dict):
                    for qk, pv in value.items():
                        sp = f"{mp}.{slot}.{qk}"
                        holder, name = _slot_name(pv, sp)
                        spec = mac.get("spectator_qubits")
                        host = (_resolved_dict_path(merged, f"{mp}.spectator_qubits.{qk}")
                                if isinstance(spec, dict) and qk in spec else None)
                        if name and host:
                            yield [f"{host}.z"], holder, name, sp, True
                    continue
                if slot in chans:
                    hosts, strict = chans[slot], True
                elif isinstance(value, dict):
                    hosts, strict = anywhere, False   # another id-carrying pulse slot
                else:
                    continue                  # a plain field (phase, extras...)
                holder, name = _slot_name(value, f"{mp}.{slot}")
                if name:
                    yield hosts, holder, name, f"{mp}.{slot}", strict


def named_slot_plays(merged):
    """Yield ``(op_path, holder_path, slot_path)`` for every pair-gate slot
    that plays a channel operation BY NAME, on the channel the gate plays
    that slot on: ``flux_pulse_qubit`` on the moving qubit's ``z``,
    ``flux_pulse_target`` on the other qubit's ``z``, ``coupler_flux_pulse``
    on the pair's coupler, a ``spectator_qubits_control`` entry on that
    spectator's ``z``, and any other id-carrying slot on one of those. An op
    of the same name on any other channel is not played by the slot."""
    for hosts, holder, name, slot_path, _strict in _named_slot_targets(merged):
        yield from _ops_named(merged, hosts, name, holder, slot_path)


def named_slot_missing(merged) -> list[tuple[str, str, list[str]]]:
    """``(holder, name, channels)`` for every slot the gate plays BY NAME on
    known channels where no operation of that name exists: the gate's
    ``apply()`` would look the name up and fail (``generate_config()`` still
    passes, so nothing else says so)."""
    out = []
    for hosts, holder, name, _sp, strict in _named_slot_targets(merged):
        if strict and hosts and not any(True for _ in _ops_named(
                merged, hosts, name, holder, _sp)):
            out.append((holder, name, list(hosts)))
    return out


def _ops_named(merged, channels, name, holder, slot_path):
    from .pointer_path import _walk
    for ch in channels:
        ok, ops = _walk(merged, f"{ch}.operations".split("."))
        if ok and isinstance(ops, dict) and name in ops:
            yield f"{ch}.operations.{name}", holder, slot_path


def named_slot_players(merged, op_path: str) -> list[str]:
    """The slot holders (``<slot>.id`` or a plain-name slot) that play the
    operation at *op_path* by name -- see :func:`named_slot_plays`."""
    return sorted({h for op, h, _s in named_slot_plays(merged) if op == op_path})


def named_slots_cut(merged, paths) -> list[str]:
    """The slot holders left playing a missing name when every path in
    *paths* is deleted: the op a slot plays by name is at or under one of
    them, and the slot itself is not (a slot deleted with its op loses
    nothing)."""
    paths = [p for p in paths if isinstance(p, str) and p]

    def gone(x):
        return any(x == p or x.startswith(p + ".") for p in paths)

    return sorted({h for op, h, slot in named_slot_plays(merged)
                   if gone(op) and not gone(slot)})
