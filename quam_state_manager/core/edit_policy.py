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

import math
from typing import Any

CAS_REL_TOL = 1e-9      # docs/117: the ONE compare-and-swap tolerance


def cas_equal(a: Any, b: Any) -> bool:
    """Compare-and-swap equality for "is this value still what I wrote?".

    docs/117: a revert must refuse when someone else moved the value since,
    but two floats that went through a JSON round-trip are not bit-equal, so
    an exact == would refuse reverts that are perfectly safe. Numbers compare
    with a relative tolerance; bools are NOT numbers here (True == 1 would
    make a boolean flip invisible); everything else is plain equality.

    Lifted verbatim from ``autofit/writer._values_equal`` (docs/56 §8), which
    now delegates here so the robot path and the user path can never drift.
    """
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) \
            and not isinstance(a, bool) and not isinstance(b, bool):
        return math.isclose(float(a), float(b), rel_tol=CAS_REL_TOL, abs_tol=0.0)
    return a == b


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
    #
    # ONLY to a literal. The promise quoted above — "the generic edit surfaces
    # render these as the resolved NUMBER" — is the whole justification for
    # redirecting the write, and it does not hold when the pointer reaches a
    # CONTAINER. `qubit_pairs.q1-2.qubit_control = "#/qubits/q1"` resolved to
    # `qubits.q1`, so a write aimed at the cell was aimed at the entire qubit
    # object; only the type judge ("Expected dict, got str") stood between a
    # typed qubit name and a chip whose q1 became a string (docs/121). A
    # container target means the cell IS the pointer, so the pointer is what the
    # write must land on.
    if isinstance(current, str) and is_pointer(current):
        ft = resolve_field_target(store.merged, dot_path)
        if (ft["resolvable"] and ft["resolved_path"] != dot_path
                and not isinstance(_container_at(
                    store.merged, str(ft["resolved_path"]).split(".")), (dict, list))):
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


def pointer_cell_refusal(store: Any, dot_path: str, new_value: Any) -> str | None:
    """Why a pointer-valued cell refuses *new_value*, or None to proceed.

    docs/121. The grids now SHOW the pointer whenever there is no scalar behind
    it (``qubit_control = "#/qubits/q1"``, a dangling ``LO_frequency``), which
    is what the customer asked for — but showing an editable pointer without
    this guard is worse than showing nothing. Measured on the real chip: typing
    ``q3`` stored the literal ``"q3"``, and typing ``6.1e9`` over a dangling
    pointer stored the STRING ``"6100000000.0"``. Both succeeded, both silent,
    and the first is a ``Quam.load()`` failure the user would meet days later.

    The refusal is narrow by construction: it fires ONLY where the cell has no
    scalar behind the pointer. A pointer that reaches a real number keeps
    value-mode untouched — typing a number there writes the number at the
    target, which is the long-standing promise and is not this function's
    business. Breaking a link on purpose stays possible; it just has to be
    said out loud, on the Pulses page's explicit 3-mode editor.
    """
    from quam_state_manager.core.pointer_path import resolve_field_target
    from quam_state_manager.core.pointer_resolver import is_pointer
    try:
        current = store.get_value(dot_path)
    except (KeyError, TypeError, ValueError, IndexError):
        return None
    if not (isinstance(current, str) and is_pointer(current)):
        return None
    if isinstance(new_value, str) and is_pointer(new_value.strip()):
        return None                     # re-pointing is the ordinary edit here
    try:
        ft = resolve_field_target(store.merged, dot_path)
    except Exception:                   # noqa: BLE001 — best-effort, never blocks
        return None
    if ft.get("resolvable") and ft.get("resolved_path") != dot_path:
        target = _container_at(store.merged, str(ft["resolved_path"]).split("."))
        if not isinstance(target, (dict, list)):
            return None                 # value-mode: unchanged, always
    return (f"This field is a reference ({current}), not a value. Writing "
            f"{new_value!r} here would replace the link with plain text and "
            f"break it. Enter a pointer (e.g. {current}) to re-point it, or "
            f"use the Pulses page's pointer editor to break the link "
            f"deliberately.")


def sibling_type_refusal(store: Any, dot_path: str, new_value: Any) -> str | None:
    """Why a NULL field refuses prose, using the chip's own evidence, or None.

    A field holding ``null`` carries no type, so with no env schema attached SM
    had nothing to judge against and `qubits.q18.T1 <- "abc"` was stored as a
    string, HTTP 200, no warning. The next `Quam.load()` gets a str where a
    float belongs, and every consumer of T1 breaks.

    But the chip is not silent about it: the SAME leaf on the sibling entities
    usually holds real numbers. That is data-derived evidence, not an invented
    schema — so the refusal only fires when the chip itself demonstrates the
    type, and says which qubits it read. On a chip in early bring-up where the
    leaf is null everywhere (the real customer chip), there is nothing to infer
    and behaviour is byte-identical to before: SM does not know, so it does not
    pretend to.

    Narrow by construction: only a currently-NULL leaf, only when the typed text
    is not itself a number / pointer / null token, and only under an entity
    collection (`qubits.<id>.…`, `qubit_pairs.<id>.…`).
    """
    from quam_state_manager.core.pointer_resolver import is_pointer
    if not isinstance(new_value, str):
        return None                      # already parsed to a real type
    s_new = new_value.strip()
    if not s_new or is_pointer(s_new) or s_new.lower() in ("null", "none"):
        return None
    try:
        float(s_new)
        return None                      # a number is never the problem
    except ValueError:
        pass
    try:
        if store.get_value(dot_path) is not None:
            return None                  # only a NULL field lacks a type
    except (KeyError, TypeError, ValueError, IndexError):
        return None

    segs = dot_path.split(".")
    if len(segs) < 3 or segs[0] not in ("qubits", "qubit_pairs"):
        return None
    coll = (store.merged or {}).get(segs[0])
    if not isinstance(coll, dict):
        return None
    leaf, me = segs[2:], segs[1]
    numeric, other, witnesses = 0, 0, []
    for ent, node in coll.items():
        if ent == me:
            continue
        cur = node
        for k in leaf:
            if not isinstance(cur, dict) or k not in cur:
                cur = None
                break
            cur = cur[k]
        if cur is None:
            continue
        if isinstance(cur, bool):
            other += 1
        elif isinstance(cur, (int, float)):
            numeric += 1
            if len(witnesses) < 3:
                witnesses.append(ent)
        else:
            other += 1
    # Unanimous, and enough of them to mean something.
    if numeric >= 2 and other == 0:
        return (f"{'.'.join(leaf)} is a number on this chip "
                f"({numeric} other {segs[0].rstrip('s')}s, e.g. "
                f"{', '.join(witnesses)}), and {new_value!r} is not one. "
                f"This field is empty, so its type comes from the rest of the "
                f"chip. Type a number, or clear it with 'null'.")
    return None


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
