"""Single source of truth for classifying a flattened state/wiring leaf.

The "All values" completeness surface (``core/all_values.py``) and its regression
tests classify EVERY leaf produced by walking ``store.merged`` into exactly one
*policy kind*, so the kinds partition the file and the coverage counter can never
silently drop a leaf (``sum(by_kind) == len(flatten(merged))`` is a tested
invariant — that equality is the completeness proof shown to the user).

Editability POLICY is per-surface, NOT encoded here. The shipped pair grid
(``pair_columns.py``) value-edits cross-ref pointers; the flat "All values" list
shows them read-only with a deep-link (user decision, 2026-06-19). This module
only NAMES the kind — each surface decides what to do with it. (We deliberately
do NOT rewire the shipped pair grid onto this module: it is already tested and
correct, and a customer ship is imminent.)

Why a structural ``in_list`` flag instead of a "numeric path segment" heuristic:
the ``ports`` tree keys FEM/port objects by NUMBER strings
(``ports.mw_outputs.con1.1.2.band``) — those are editable scalars, not list
elements. Only a leaf reached by descending through an actual JSON *list*
(``confusion_matrix``, ``gef_centers``, ``integration_weights``, filter taps) is
a list element. The walker tracks that; the classifier trusts it.
"""

from __future__ import annotations

from typing import Any

from quam_state_manager.core.pointer_resolver import is_pointer, is_self_ref

# Structural / identity leaves — shown read-only, never an editable input.
SKIP_LEAVES = frozenset({"__class__", "id", "digital_marker"})

# Chip-membership arrays — read-only with a warning badge (user decision,
# 2026-06-19): editing them re-scopes what generate_config + every downstream
# tool considers active, with no validation. The most dangerous leaves to
# fat-finger, so they are visible (counted) but never editable here.
MEMBERSHIP_TOPS = frozenset(
    {"active_qubit_names", "active_qubit_pair_names", "active_twpa_names"}
)

# The mutually-exclusive policy kinds.
KIND_SCALAR = "scalar"          # editable
KIND_XREF = "xref"              # cross-ref pointer (#/ or #../) — read-only + deep-link
KIND_SELFREF = "selfref"        # self-ref (#./inferred_*) — read-only ⟳ (config-time)
KIND_LIST = "list"              # list/matrix element — read-only badge + deep-link
KIND_SKIP = "skip"              # __class__/id/digital_marker — read-only identity/type
KIND_MEMBERSHIP = "membership"  # active_* arrays — read-only + warning badge

ALL_KINDS = (
    KIND_SCALAR, KIND_XREF, KIND_SELFREF, KIND_LIST, KIND_SKIP, KIND_MEMBERSHIP,
)
READONLY_KINDS = frozenset(
    {KIND_XREF, KIND_SELFREF, KIND_LIST, KIND_SKIP, KIND_MEMBERSHIP}
)


def classify_leaf(top: str, leaf_name: str, value: Any, in_list: bool) -> str:
    """Classify one flattened leaf into a policy kind.

    ``top``       — the first dot-path segment (top-level container key).
    ``leaf_name`` — the final path segment (a dict key, or a stringified list index).
    ``value``     — the scalar/str leaf value (``_walk`` never yields dict/list here).
    ``in_list``   — True iff the leaf was reached by descending through a JSON list.

    Order matters: membership arrays win over the list rule (they ARE lists but get
    a distinct policy); identity keys win over pointer/scalar; list elements are
    read-only regardless of value; then self-ref vs cross-ref vs plain scalar.
    """
    if top in MEMBERSHIP_TOPS:
        return KIND_MEMBERSHIP
    if leaf_name in SKIP_LEAVES:
        return KIND_SKIP
    if in_list:
        return KIND_LIST
    if is_self_ref(value):
        return KIND_SELFREF
    if is_pointer(value):
        return KIND_XREF
    return KIND_SCALAR


def is_editable(kind: str) -> bool:
    """Only plain scalars are editable in the flat list (per the safety policy)."""
    return kind == KIND_SCALAR
