"""Shared edit-path resolution + read-only policy for the state editors.

Both the web routes and the CLI mutate chip values, and they MUST apply the same
two safety rules or they diverge (they did — the CLI wrote a stringified number
onto a pointer leaf and overwrote identity keys, both fixed by routing through
these functions):

* ``resolve_edit_path`` — follow a pointer-valued leaf to its resolved literal
  target so a numeric edit writes the number THERE (value-mode) instead of
  replacing the pointer with a stringified number (wrong JSON type, severed link).
* ``editability_reason`` — the durable read-only policy: chip-membership arrays
  and identity/type keys are not directly editable. (List/matrix ELEMENTS became
  editable with the dot-form numeric path grammar — ``confusion_matrix.0.1`` —
  the modifier pins the element's type via ``_type_coerce`` against the old
  element value, and the type-policy layer enforces the schema type when known.)

Kept in core (not web) so the CLI can import them without pulling in Flask.
"""

from __future__ import annotations

from typing import Any


def resolve_edit_path(store: Any, dot_path: str) -> str:
    """Resolve a write path through QUAM pointers when needed.

    A path that navigates directly is normally returned unchanged. The ONE
    exception: when the leaf it lands on is ITSELF a QUAM pointer string
    (e.g. ``x90.amplitude = "#../x180/amplitude"`` — real customer states hold
    thousands). The generic edit surfaces render these as the resolved NUMBER and
    promise value-mode ("editing writes the resolved target"), so we follow the
    pointer to the literal it references and write THERE. Without this,
    modifier._type_coerce's str-branch would coerce the typed number to a *string*,
    replacing the pointer with e.g. ``"0.09"`` — a wrong JSON type that breaks
    Quam.load()/generate_config() and silently severs the shared-value link. (To
    deliberately break a link, use the Pulses page's explicit 3-mode pointer editor,
    which writes with coerce=False.)
    """
    from quam_state_manager.core.pointer_path import resolve_field_target
    from quam_state_manager.core.pointer_resolver import is_pointer
    try:
        current = store.get_value(dot_path)
    except (KeyError, TypeError, ValueError, IndexError):
        ft = resolve_field_target(store.merged, dot_path)
        if ft["resolvable"] and ft["resolved_path"] != dot_path:
            return ft["resolved_path"]
        return dot_path
    # Navigable as-is. Follow a leaf-pointer to its literal target (value-mode).
    if isinstance(current, str) and is_pointer(current):
        ft = resolve_field_target(store.merged, dot_path)
        if ft["resolvable"] and ft["resolved_path"] != dot_path:
            return ft["resolved_path"]
    return dot_path


def _container_at(merged: Any, segs: list[str]) -> Any:
    """Walk ``merged`` by string segments (dict keys or list indices). Returns the
    value at the path, or None if any segment is missing/out of range."""
    cur = merged
    for s in segs:
        if isinstance(cur, dict):
            if s not in cur:
                return None
            cur = cur[s]
        elif isinstance(cur, list):
            try:
                cur = cur[int(s)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return cur


def editability_reason(store: Any, target_path: str) -> str | None:
    """The durable read-only safety policy — chip-membership arrays (active_*)
    and identity/type keys (__class__/id) are not directly editable. Returns a
    rejection reason for a non-editable resolved target, else None.

    Deliberately does NOT reject POINTERS here: the bulk grid edits pointer-aliases
    in value-mode (they resolve THROUGH to a scalar target) and the Explorer
    live-diff accept legitimately writes a pointer string. List/matrix ELEMENTS
    are editable (dot-form numeric segments; the modifier's structural traversal
    distinguishes true list indices from ``ports.*.<num>.*`` number-keyed DICT
    keys). ``digital_marker`` is a real per-pulse value (null / "ON" / pointer on
    real chips), not an identity key — it is editable.
    """
    from quam_state_manager.core.leaf_classify import MEMBERSHIP_TOPS, SKIP_LEAVES
    segs = target_path.split(".")
    if not segs:
        return None
    if segs[0] in MEMBERSHIP_TOPS:
        return "chip-membership array — edit via the chip add/remove controls, not here"
    if segs[-1] in SKIP_LEAVES:
        return "identity / type key — read-only"
    return None
