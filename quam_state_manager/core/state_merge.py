"""Pure display merge shared by the loader and the state-tracking rules."""

from __future__ import annotations

_SENTINEL = object()


def merge_state_wiring(state: dict, wiring: dict) -> dict:
    """Deep-merge colliding dicts; wiring shadows every other collision.

    Neither input is mutated. Untouched branches retain their existing
    references, as in the loader's original shallow-copy merge.
    """
    merged = {**state}
    for key, value in wiring.items():
        existing = merged.get(key, _SENTINEL)
        if existing is _SENTINEL:
            merged[key] = value
        elif isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(existing, value)
        else:
            merged[key] = value
    return merged


def _deep_merge(a: dict, b: dict) -> dict:
    """Recursively merge b into a shallow copy of a, with b winning."""
    out = dict(a)
    for k, v in b.items():
        cur = out.get(k, _SENTINEL)
        if cur is not _SENTINEL and isinstance(cur, dict) and isinstance(v, dict):
            out[k] = _deep_merge(cur, v)
        else:
            out[k] = v
    return out
