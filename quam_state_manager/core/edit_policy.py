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
        # docs/88: the chain can resolve THROUGH pointers into a real container
        # and dead-end only on the FINAL key — a port that doesn't carry
        # ``lo_mode`` while its siblings do, which is exactly why the grid shows
        # the column at all. Returning dot_path here handed the modifier a path
        # whose parent is a POINTER STRING, producing the unactionable
        # "Parent at 'qubits.qC4.resonator.opx_input.lo_mode' is str, not dict
        # or list" and making the cell permanently uneditable. The honest write
        # target is <resolved parent>.<leaf>; whether that key may be CREATED is
        # the caller's decision (create_subtree still type-checks it).
        target = resolve_missing_leaf_path(store, dot_path)
        if target is not None:
            return target
        return dot_path
    # Navigable as-is. Follow a leaf-pointer to its literal target (value-mode).
    if isinstance(current, str) and is_pointer(current):
        ft = resolve_field_target(store.merged, dot_path)
        if ft["resolvable"] and ft["resolved_path"] != dot_path:
            return ft["resolved_path"]
    return dot_path


def resolve_missing_leaf_path(store: Any, dot_path: str) -> str | None:
    """``<resolved parent>.<leaf>`` when only the FINAL key is absent, else None.

    Answers one question: does everything up to the last segment resolve to a
    real dict, with just the leaf missing? That is the shape produced by a
    pointer-valued channel node (``qubits.q.resonator.opx_input`` →
    ``ports.mw_inputs.con1.3.1``) whose port simply does not carry the field
    yet. Returning the parent's RESOLVED path keeps the write on the real
    object instead of on a path that runs through a pointer string.

    A list parent returns None: appending to a list is not a "fill in the
    missing field" operation and must never be inferred from a cell edit.
    """
    if "." not in dot_path:
        return None
    from quam_state_manager.core.pointer_path import resolve_field_target
    parent, leaf = dot_path.rsplit(".", 1)
    try:
        pf = resolve_field_target(store.merged, parent)
    except Exception:       # noqa: BLE001 — resolution is best-effort here
        return None
    if not pf.get("resolvable"):
        return None
    container = _container_at(store.merged,
                             (pf.get("resolved_path") or parent).split("."))
    if not isinstance(container, dict) or leaf in container:
        return None
    return f"{pf['resolved_path']}.{leaf}"


def leaf_is_absent(store: Any, dot_path: str) -> bool:
    """True when *dot_path*'s parent is a real dict but the final key is absent.

    The single predicate both edit choke points use to decide ``set_value`` vs
    ``create_subtree``, so they cannot disagree about what "this field isn't
    there yet" means.
    """
    if "." not in dot_path:
        return False
    parent, leaf = dot_path.rsplit(".", 1)
    container = _container_at(store.merged, parent.split("."))
    return isinstance(container, dict) and leaf not in container


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


# ----------------------------------------------------------------------
# extras — the user's own corner of the state
# ----------------------------------------------------------------------

_FREE_FORM_SEGMENT = "extras"


def is_free_form_path(dot_path: str) -> bool:
    """Is this leaf inside an ``extras`` block — i.e. user-declared free form?

    ``extras`` is where a chip carries things QUAM itself does not model:
    ``extras.chip_name``, ``extras.data_folder``, a lab's own labels. Nothing
    there has a schema, and the value is whatever the user says it is. SM must
    therefore not form opinions about its TYPE — most sharply, it must not
    read a numeric-looking string as a number that got string-ified by mistake.

    A real report from the field made the cost concrete: a CZ branch label
    stored as ``extras.cz_branch = "02"`` was flagged as a stored-as-text
    anomaly (it is a label, and ``"02"`` is exactly how a label is written),
    and — worse — changing it to ``"03"`` was intercepted by the type-repair
    409 offering to convert it to a number. On that chip the two labels were
    100% of the alarm, and the field could not be edited at all without
    knowing the undocumented quoting escape hatch.

    Matched at ANY depth, because that is where ``extras`` lives on real
    chips: ``qubit_pairs.<pair>.extras.<key>``, not just the root. One
    definition, used by both the detector
    (:func:`diagnostics.numeric_string_leaves`) and the edit-path offer, so
    the warning and the repair can never disagree about what counts as text.
    """
    return _FREE_FORM_SEGMENT in (dot_path or "").split(".")
