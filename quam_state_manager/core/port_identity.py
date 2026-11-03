"""Pure checks for the two addresses stored on each port entity."""
from __future__ import annotations

from collections import defaultdict

IDENTITY_FIELDS = ("controller_id", "fem_id", "port_id")


def normalize_identity(value):
    """Return an integer or literal claim, or None for an unresolved claim."""
    if value is None or isinstance(value, str) and value.startswith("#"):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            pass
    return value


def identity_disagrees(value, key):
    claim = normalize_identity(value)
    return claim is not None and (
        isinstance(claim, bool) or claim != normalize_identity(key))


def _items(value):
    return value.items() if isinstance(value, dict) else ()


def port_identity_mismatches(state: dict) -> list[dict]:
    """Compare literal claims numerically where possible; skip unresolved claims.

    Duplicate identities are scoped to the port kind: an input and an output
    with the same numeric address are distinct hardware resources. Only a
    complete field identity claims a duplicate. Each affected entity appears
    once, with ``differ`` and/or ``duplicate_paths`` explaining the defect.
    """
    records = []
    identities = defaultdict(list)
    for kind, controllers in _items(state.get("ports")):
        for con, fems in _items(controllers):
            for fem, ports in _items(fems):
                for port, entity in _items(ports):
                    if not isinstance(entity, dict):
                        continue
                    key = tuple(map(str, (con, fem, port)))
                    fields = tuple(entity.get(f) for f in IDENTITY_FIELDS)
                    record = {"path": f"ports.{kind}.{con}.{fem}.{port}",
                              "key": key, "fields": fields,
                              "differ": [f for f, k in zip(IDENTITY_FIELDS, key)
                                         if identity_disagrees(entity.get(f), k)],
                              "duplicate_paths": []}
                    records.append(record)
                    claims = tuple(normalize_identity(v) for v in fields)
                    if all(v is not None and not isinstance(v, bool) for v in claims):
                        identities[(kind, *map(str, claims))].append(record)
    for group in identities.values():
        if len(group) > 1:
            for record in group:
                record["duplicate_paths"] = [r["path"] for r in group if r is not record]
    return [r for r in records if r["differ"] or r["duplicate_paths"]]


def identity_message(record: dict) -> str:
    message = (f"{record['path']}: dict key {record['key']} versus identity fields "
               f"{record['fields']} (controller_id, fem_id, port_id)")
    if record["differ"]:
        message += "; disagree: " + ", ".join(record["differ"])
    if record["duplicate_paths"]:
        message += "; duplicate physical identity with " + ", ".join(record["duplicate_paths"])
    return message


def port_users(root: dict, path: str) -> list[str]:
    """Absolute pointer users, including chains through wiring and subfields."""
    from quam_state_manager.core.loader import _walk
    pointers = [(p, v) for p, v, _ in _walk(root)
                if isinstance(v, str) and v.startswith("#/")]
    targets = {"#/" + path.replace(".", "/")}
    users = set()
    while True:
        found = {p for p, v in pointers if p not in users
                 and any(v == t or v.startswith(t + "/") for t in targets)}
        if not found:
            return sorted(users)
        users.update(found)
        targets.update("#/" + p.replace(".", "/") for p in found)
