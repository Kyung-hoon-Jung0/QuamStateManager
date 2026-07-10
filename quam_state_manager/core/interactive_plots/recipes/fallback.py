"""Fallback recipe for experiments without a tailored interactive reproduction.

Returns an empty menu so the UI shows "No interactive reproduction available
for this experiment type" and points the user at the Raw Data tab.
"""
from __future__ import annotations

FAMILY: tuple[str, ...] = ()


def menu(bundle):
    return []


def build(bundle, key):
    return None
