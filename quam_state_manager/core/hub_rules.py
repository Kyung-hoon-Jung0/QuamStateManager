r"""Pure S2 rules for run-by-run saved-state changes (no pointer resolution).

Ordinary holder paths are literal dot-paths. Within dict keys, backslashes
and dots are escaped with a backslash; an empty key is spelled ``\\e``.
Empty containers have no leaves. Inputs are JSON values, including Python's
JSON extensions NaN and Infinity. No file access, logging, or database work.
"""

from __future__ import annotations

import functools
import hashlib
import json
import math
import threading
from collections import OrderedDict
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


@functools.lru_cache(maxsize=1 << 16)
def _segment(key: str) -> str:
    # v1.3.0 (perf): memoized -- a chip's states repeat the same few thousand
    # keys, and this ran ~3.5 million times in one archived chip's build
    if not isinstance(key, str):
        raise TypeError("JSON object keys must be strings")
    return key.replace("\\", "\\\\").replace(".", "\\.") if key else "\\e"


#: v1.3.0 (perf): a long scalar list's S2 hash, memoized by the list's values.
#: Consecutive saved states repeat the same long arrays (integration weights,
#: waveform samples); their canonical JSON + SHA-1 was most of a flatten (21 ms
#: of a 1.6 MB state, 31 arrays) and an archived chip's build flattens ~2,400
#: states. The key is the tuple of values, and only lists of plain int / float /
#: str / None are memoized: for those, Python equality implies the same
#: canonical text (1 == 1.0 -> "1", 0.0 == -0.0 -> "0", NaN never equal), so a
#: hit is exactly the hash a recomputation gives. A bool is never in a key
#: (True == 1, but "true" != "1"). Bounded by entries and by elements held.
_ARRAY_MEMO_TYPES = frozenset({int, float, str, type(None)})
_ARRAY_MEMO_MAX = 1024
_ARRAY_MEMO_MAX_ELEMENTS = 500_000
_ARRAY_MEMO: OrderedDict = OrderedDict()
_ARRAY_MEMO_LOCK = threading.Lock()
_array_memo_elements = 0


#: The element types a JSON scalar list is made of (the plain-type fast path
#: of flatten's "no dict or list inside" test; anything else takes the
#: original isinstance walk).
_SCALAR_TYPES = _ARRAY_MEMO_TYPES | {bool}


def _array_hash(value: list, types: set) -> str:
    """The S2 hash of a long scalar list (canonical JSON of its hash scalars);
    ``types``: the set of its element types."""
    global _array_memo_elements
    key = tuple(value) if types <= _ARRAY_MEMO_TYPES else None
    if key is not None:
        with _ARRAY_MEMO_LOCK:
            got = _ARRAY_MEMO.get(key)
            if got is not None:
                _ARRAY_MEMO.move_to_end(key)
                return got
    digest = hashlib.sha1(_json([_hash_scalar(v) for v in value]).encode("utf-8")).hexdigest()
    if key is not None and len(key) <= _ARRAY_MEMO_MAX_ELEMENTS:
        with _ARRAY_MEMO_LOCK:
            if key not in _ARRAY_MEMO:
                _ARRAY_MEMO[key] = digest
                _array_memo_elements += len(key)
                while (len(_ARRAY_MEMO) > _ARRAY_MEMO_MAX
                       or _array_memo_elements > _ARRAY_MEMO_MAX_ELEMENTS):
                    old, _ = _ARRAY_MEMO.popitem(last=False)
                    _array_memo_elements -= len(old)
    return digest


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
            types = set(map(type, value)) if len(value) > 16 else None
            if types is not None and (types <= _SCALAR_TYPES
                                      or all(not isinstance(v, (dict, list)) for v in value)):
                out[".".join(parts)] = {"_array": len(value), "_hash": _array_hash(value, types)}
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


#: Leaf types for which ``==`` between two values of the SAME type implies
#: ``same`` (diff's fast path; NaN is never ``==`` and takes ``same``).
_PLAIN = frozenset({str, int, float, bool, type(None)})


def diff(before_flat: dict[str, Any], after_flat: dict[str, Any]) -> list[Change]:
    """Sorted add/gone/set rows, or retarget when two pointer strings change.

    v1.3.0 (perf): only the changed paths are sorted (a state's ~4,000 paths
    were sorted on every diff) and an unchanged plain leaf skips ``same``."""
    rows = []
    for path, old in before_flat.items():
        if path in after_flat:
            new = after_flat[path]
            if type(old) is type(new) and type(old) in _PLAIN and old == new:
                continue
            if same(old, new):
                continue
            op = "retarget" if _pointer(old) and _pointer(new) else "set"
            rows.append(Change(path, op, *_encoded(old), *_encoded(new)))
        else:
            rows.append(Change(path, "gone", *_encoded(old), None, None))
    for path, new in after_flat.items():
        if path not in before_flat:
            rows.append(Change(path, "add", None, None, *_encoded(new)))
    rows.sort(key=lambda r: r.path)
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
