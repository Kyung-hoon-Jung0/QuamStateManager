r"""Pure S2 rules for run-by-run saved-state changes (no pointer resolution).

Ordinary holder paths are literal dot-paths. Within dict keys, backslashes
and dots are escaped with a backslash; an empty key is spelled ``\\e``.
Empty containers have no leaves. Inputs are JSON values, including Python's
JSON extensions NaN and Infinity. No file access, logging, or database work.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any

from quam_state_manager.core.state_merge import merge_state_wiring as merged


def _json(value: Any) -> str:
    """Deterministic compact UTF-8 JSON text, with nonfinite JSON extensions."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _hash_scalar(value: Any) -> Any:
    # Canonicalize float values equal to ints (including -0.0), without
    # rounding large integers through float. bool retains its JSON type.
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    return value


def _segment(key: str) -> str:
    if not isinstance(key, str):
        raise TypeError("JSON object keys must be strings")
    return key.replace("\\", "\\\\").replace(".", "\\.") if key else "\\e"


def flatten(doc: Any) -> dict[str, Any]:
    """Record scalar holder leaves; scalar lists longer than 16 are hashed.

    Nested lists (including matrices and lists of objects) recurse; the
    threshold applies independently to each immediate scalar list. Root
    scalars/long arrays use the empty path. Returned markers are fresh dicts.
    """
    out: dict[str, Any] = {}

    def walk(value: Any, parts: tuple[str, ...]) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                walk(child, parts + (_segment(key),))
        elif isinstance(value, list):
            if len(value) > 16 and all(not isinstance(v, (dict, list)) for v in value):
                payload = _json([_hash_scalar(v) for v in value]).encode("utf-8")
                out[".".join(parts)] = {"_array": len(value), "_hash": hashlib.sha1(payload).hexdigest()}
            else:
                for index, child in enumerate(value):
                    walk(child, parts + (str(index),))
        else:
            out[".".join(parts)] = value

    walk(doc, ())
    return out


def same(a: Any, b: Any) -> bool:
    """Exact numeric equality, NaN agreement, and strict bool/string types.

    Marker dictionaries compare by their content hash and length. Recursive
    handling makes this function safe for JSON containers as well as leaves.
    """
    if _number(a) and _number(b):
        if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
            return True
        return a == b
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(same(a[key], b[key]) for key in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    return a == b


@dataclass(frozen=True, slots=True)
class Change:
    """Ledger-ready row; absent sides have both fields None.

    Stored JSON null is text ``null``, distinct from an absent side. Numbers
    keep their original int/float value in num; bool, strings, and array
    markers use canonical JSON text. NaN/Infinity remain numeric here; S3
    must choose a lossless persistence encoding rather than SQLite REAL NaN.
    """

    path: str
    op: str
    old_num: int | float | None = None
    old_txt: str | None = None
    num: int | float | None = None
    txt: str | None = None


def _encoded(value: Any) -> tuple[int | float | None, str | None]:
    return (value, None) if _number(value) else (None, _json(value))


def _pointer(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(("#/", "#../", "#./"))


def diff(before_flat: dict[str, Any], after_flat: dict[str, Any]) -> list[Change]:
    """Sorted add/gone/set rows, or retarget when two pointer strings change."""
    rows = []
    for path in sorted(before_flat.keys() | after_flat.keys()):
        old_present, new_present = path in before_flat, path in after_flat
        old, new = before_flat.get(path), after_flat.get(path)
        if old_present and new_present and same(old, new):
            continue
        if not old_present:
            op = "add"
        elif not new_present:
            op = "gone"
        elif _pointer(old) and _pointer(new):
            op = "retarget"
        else:
            op = "set"
        old_num, old_txt = _encoded(old) if old_present else (None, None)
        num, txt = _encoded(new) if new_present else (None, None)
        rows.append(Change(path, op, old_num, old_txt, num, txt))
    return rows


def state_hash(state_bytes: bytes, wiring_bytes: bytes) -> str:
    """Hash the original bytes of the pair, separated by a NUL byte.

    S10 walk (perf): the same digest fed piece by piece -- joining a ~1 MB
    pair into one new buffer first cost about 1 ms a run, a third of a moved
    data folder's catch-up."""
    h = hashlib.sha1(state_bytes)
    h.update(b"\0")
    h.update(wiring_bytes)
    return h.hexdigest()
