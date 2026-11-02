"""Value-preserving merge for the Re-generate Config flow.

When a user re-generates a chip's config (rebuild from an edited spec via the
generator subprocess), the fresh ``build_quam`` output carries only defaults +
populate -- every calibrated value and every user-added operation/macro would be
silently lost. This module merges the OLD (calibrated) state onto the NEW
(rebuilt) structure so nothing is lost:

- **tier 1 (carry)** -- where a leaf PATH survives in NEW, the OLD scalar VALUE
  wins (the calibration). NEW keeps the structure and every JSON pointer (the
  freshly-built wiring), so structural edits the user made in the wizard hold.
- **tier 2 (graft)** -- OLD-only subtrees (user-added pulse operations / gate
  macros that the rebuild's single ``pair_gate`` choice didn't recreate) are
  copied wholesale, then their absolute pointers are validated against the merged
  tree (a graft referencing something the rebuild dropped is flagged, not kept
  blindly).

Pure functions over plain dicts -- no ``quam`` / ``quam_builder`` imports (the
State Manager process never loads the heavy QM stack). Verified on real
calibrated data (P2 fidelity probe): residual loss 0, and the merged state
compiles to a valid QUA config that is a superset of the original. See
``docs/51_regenerate_config.md``.
"""

from __future__ import annotations
from quam_state_manager.core.loader import natural_key

import copy
import re
from dataclasses import dataclass, field
from typing import Any

from quam_state_manager.core import regen_spec


# Top-level dicts whose keys ARE structural entities: the rebuilt spec owns
# their membership, so an OLD-only entity here was intentionally removed by the
# user and must NOT be resurrected by the additive graft (its values fall to
# ``residual_lost`` for transparency). Additive graft still applies WITHIN a
# surviving entity (operations / macros / extras / custom fields).
#
# NOTE: ``twpas`` is deliberately NOT here. The installed ``quam_builder`` has no
# TWPA support in its wiring registry, so EVERY rebuild emits an empty ``twpas``
# dict regardless of the source — a missing TWPA is a builder gap, never a user
# removal. Blocking the graft would silently drop the chip's real TWPAs (156
# leaves lost on lab-A); leaving ``twpas`` graftable preserves them wholesale
# (residual loss 0). See docs/51_regenerate_config.md.
ENTITY_COLLECTIONS = {"qubits", "qubit_pairs", "ports", "octaves", "mixers"}

# Of those, the hardware collections whose ENTIRE subtree is rebuild-authoritative
# (their entities live several levels deep and carry no user-addable leaves) — so
# an OLD-only key at ANY depth under them is a removed port/octave/mixer and must
# NOT be grafted back. qubits/qubit_pairs are intentionally excluded: below the
# direct entity level they hold user-added operations/macros that DO graft.
_HW_ENTITY_COLLECTIONS = {"ports", "octaves", "mixers"}

# Leaf keys that encode structural membership -> always take the NEW value so a
# structural add/remove in the rebuild is reflected, never overwritten by OLD.
STRUCTURAL_LEAF_KEYS = {
    "active_qubit_names", "active_qubit_pair_names", "active_twpa_names",
}
# ...except the user's own choice of which of them are ACTIVE: the wizard has
# no control for it, so the build writes its default (every qubit, no pair) and
# a rebuild used to wipe the chip's active pairs (and re-activate every
# qubit). Each active list is kept for the entities the rebuild still has
# (renamed ones under their new ids -- the source is re-expressed first); an
# entity the source did not have takes the build's default.
_ACTIVE_OF = {"active_qubit_names": "qubits", "active_qubit_pair_names": "qubit_pairs",
              "active_twpa_names": "twpas"}


def _active_list(key: str, old_root: dict, new_root: dict, nv: Any) -> Any:
    """The merged value of a root active-membership list (see _ACTIVE_OF)."""
    ov = old_root.get(key)
    coll = _ACTIVE_OF[key]
    old_all = old_root.get(coll) if isinstance(old_root.get(coll), dict) else {}
    new_all = new_root.get(coll) if isinstance(new_root.get(coll), dict) else {}
    if not isinstance(ov, list) or not isinstance(nv, list):
        return copy.deepcopy(nv)
    if not new_all:
        # v1.6.0 review: an EMPTY rebuilt `twpas` is the builder gap (see the
        # ENTITY_COLLECTIONS note) -- the old TWPAs are grafted back wholesale,
        # so their active membership comes back with them, not the build's [].
        if coll not in ENTITY_COLLECTIONS and old_all:
            return [x for x in ov if isinstance(x, str) and x in old_all]
        return copy.deepcopy(nv)
    out = [x for x in ov if isinstance(x, str) and x in new_all]
    out += [x for x in nv if isinstance(x, str) and x not in old_all and x not in out]
    return out


def is_pointer(v: Any) -> bool:
    """A QUAM JSON pointer leaf (absolute ``#/``, self ``#./``, parent ``#../``)."""
    return isinstance(v, str) and v.startswith(("#/", "#./", "#../"))


def _count_leaves(obj: Any) -> int:
    if isinstance(obj, dict):
        return sum(_count_leaves(v) for k, v in obj.items() if k != "__class__")
    return 1


def _iter_leaves(obj: Any, prefix: str = ""):
    """Yield ``(dot_path, value)`` for every leaf; lists are leaf values."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "__class__":
                continue
            p = f"{prefix}.{k}" if prefix else k
            yield from _iter_leaves(v, p)
    else:
        yield prefix, obj


def _path_value(root: dict, dot_path: str) -> Any:
    node: Any = root
    for k in dot_path.split("."):
        if isinstance(node, dict) and k in node:
            node = node[k]
        elif isinstance(node, dict) and k.isdigit() and int(k) in node:
            node = node[int(k)]
        elif isinstance(node, list) and k.isdigit() and int(k) < len(node):
            node = node[int(k)]
        else:
            return None
    return node


def _resolves(root: dict, pointer: str) -> bool:
    """Does an ABSOLUTE ``#/a/b/c`` pointer land on a node in ``root``?

    Relative pointers (``#./`` / ``#../``) are context-dependent and stay valid
    structurally after a graft, so they are treated as resolvable here.
    """
    if not pointer.startswith("#/"):
        return True
    node: Any = root
    for seg in pointer[2:].split("/"):
        if not seg:
            continue
        if isinstance(node, dict) and seg in node:
            node = node[seg]
        elif isinstance(node, dict) and seg.isdigit() and int(seg) in node:
            node = node[int(seg)]
        elif isinstance(node, list) and seg.isdigit() and int(seg) < len(node):
            node = node[int(seg)]
        else:
            return False
    return True


def _has_pointer_ancestor(root: dict, dot_path: str) -> bool:
    """Walking ``dot_path`` from ``root``, is any ANCESTOR node a pointer string?
    If so the OLD inline value at ``dot_path`` was replaced by a reference and its
    value lives at the target (superseded, not lost)."""
    node: Any = root
    for seg in dot_path.split("."):
        if isinstance(node, str) and node.startswith(("#/", "#./", "#../")):
            return True
        if isinstance(node, dict) and seg in node:
            node = node[seg]
        else:
            return False
    return False


@dataclass
class MergeStats:
    """Transparency counters -- surfaced to the user so nothing is silent."""

    id_filled: int = 0                     # null operation IDs filled by the build
    carried: int = 0                       # tier1: OLD scalar values kept
    grafted: int = 0                       # tier2: OLD-only leaves copied in
    kept_new_pointer: int = 0              # NEW pointers / structure kept
    kept_new_only: int = 0                 # NEW-only leaves (fresh defaults)
    graft_subtrees: list[tuple[str, int]] = field(default_factory=list)
    superseded: list[str] = field(default_factory=list)      # value lives at a NEW pointer target
    residual_lost: list[str] = field(default_factory=list)   # OLD scalars TRULY with no home
    dangling_grafts: list[str] = field(default_factory=list)  # grafts w/ broken abs pointer (after prune)
    pruned_ops: list[str] = field(default_factory=list)      # redundant old ops removed by prune
    schema_dropped: list[str] = field(default_factory=list)  # OLD-only fields the NEW env's class schema doesn't know (cross-generation rename/removal)
    class_changed: list[tuple[str, str, str]] = field(default_factory=list)  # (path, OLD class, NEW class) -- the rebuild typed this object differently
    class_kept: list[tuple[str, str]] = field(default_factory=list)  # (path, OLD class) -- a lab subclass the build env can hold, kept (docs/202 §15)
    ports_carried: list[str] = field(default_factory=list)  # declared ports nothing referenced, carried onto a FEM the rebuild still uses (docs/202 §17)
    populate_protected: list[str] = field(default_factory=list)  # user populate edits kept as NEW over tier-1 (docs/72)
    populate_conflicts: list[str] = field(default_factory=list)  # hand-tuned OLD values kept where a populate edit implied a derived change (z delay)
    deferred_grafted: list[str] = field(default_factory=list)  # OLD typed objects grafted over a NEW null -- judged in merge_states
    rebuild_removed: list[tuple[str, str]] = field(default_factory=list)  # (path, why) an OLD typed object the rebuild left null, NOT grafted back
    ports_moved: list[tuple[str, str, str]] = field(default_factory=list)  # (old port, new port, owner) -- the port's calibration followed its line (QA F1)
    crosstalk_remapped: list[tuple[str, str]] = field(default_factory=list)  # old/new coefficient paths
    crosstalk_unresolved: list[str] = field(default_factory=list)  # retained coefficients naming absent ports
    crosstalk_dropped: list[dict] = field(default_factory=list)
    ports_conflict: list[dict] = field(default_factory=list)
    pointers_reaimed: list[dict] = field(default_factory=list)
    mw_band_conflicts: list[dict] = field(default_factory=list)
    upconverter_remapped: list[dict] = field(default_factory=list)
    port_fields_dropped: list[dict] = field(default_factory=list)
    ports_fresh: list[tuple[str, str, str]] = field(default_factory=list)  # (port, old owner, new owner) -- old values left with their line; rebuild defaults stand (QA F1)
    pairs_reversed: list[tuple[str, str]] = field(default_factory=list)  # (old id, new id) -- the rebuild has the pair only with control/target swapped (QA r2-09)
    ports_inherited: list[tuple[str, str, str]] = field(default_factory=list)  # (port, old owner, new owner) -- kept by port number from a line the rebuild removed (QA review of F1)
    twpas_removed: list[str] = field(default_factory=list)  # OLD TWPAs the step-4 list no longer carries (renamed / deleted) -- not grafted back (QA r2-10)
    twpas_renamed: list[tuple[str, str]] = field(default_factory=list)  # (old id, new id) -- a TWPA renamed on step 4; its calibration carried under the new id (QA review of r2-10)
    qubits_renamed: list[tuple[str, str]] = field(default_factory=list)  # (source id, new id) -- a qubit renamed on the wizard; its calibration carried under the new id
    pairs_renamed: list[tuple[str, str]] = field(default_factory=list)  # (source id, new id) -- a pair whose member was renamed, matched by (control, target)
    rename_record: str | None = None  # docs/296: id of the rename record written into extras (history follows the renamed qubits)
    active_kept: list[str] = field(default_factory=list)  # active-membership lists kept from the source (the build writes its defaults)
    rename_unmarked: bool = False  # docs/296: renamed, but the root class keeps no extras -- history cannot follow
    lab_objects_kept: list[tuple[str, str]] = field(default_factory=list)  # (path, class) -- v1.6.0 review: a lab object the env imports, kept under the lab's own class although the rebuild wrote no such object
    old_pointers_kept: list[str] = field(default_factory=list)  # v1.6.0 review: the source's pointer kept where the rebuild wrote the very literal it resolves to
    links_not_taken: list[str] = field(default_factory=list)  # a source literal kept where the rebuild linked the field to another holding a different value (x90's own DRAG alpha)
    wiring_values_carried: list[str] = field(default_factory=list)  # a lab's plain wiring values under a line the rebuild also made (a QDAC dc_channel)
    source_forms_restored: list[str] = field(default_factory=list)  # a lab's own pointer/object form put back where every value reads the same
    pointers_inlined: list[str] = field(default_factory=list)  # v1.6.0 review: a grafted source pointer whose direct target is no literal in the rebuilt state, written as the value it resolved to in the source


@dataclass
class MergeResult:
    merged: dict
    stats: MergeStats


def _kept_class(old: dict, new: dict, keep: dict | None, path: str = "") -> str | None:
    """The OLD object's class, when the merge may keep it (docs/202 §15).

    A re-generate writes the builder's stock class where the source chip had
    the lab's own -- one customer chip's `ComplexWeightsReadoutPulse` came back
    a `SquareReadoutPulse`, and every field only the lab's class declares (its
    optimized integration weights) dropped out. Keeping the old class is safe
    exactly when BOTH hold, each measured in the build's own env:

    - the env imports it (it is in `keep` at all -- `Quam.load()` will find it);
    - the class the rebuild wrote is in its MRO -- a SUBCLASS goes wherever
      its base went, so no parent field's declared type can be violated.

    The second rule is what keeps this from being docs/136's poison (an old
    `QdacBiasLine` grafted onto a `z` typed `FluxLine`): an unrelated class is
    never kept, it is reported as a substitution instead.
    """
    if not keep:
        return None
    o_cls, n_cls = old.get("__class__"), new.get("__class__")
    if not (isinstance(o_cls, str) and isinstance(n_cls, str)) or o_cls == n_cls:
        return None
    rec = keep.get(o_cls)
    if not rec:
        return None
    bases = rec.get("bases") or ()
    if n_cls in bases:
        return o_cls
    # v1.6.0 review: under an UNTAGGED container whose element type is one universal
    # base (a channel's `operations` is `Dict[str, Pulse]`), a lab class that subclasses
    # that base is admissible whatever stock class the builder wrote there -- a lab's
    # own SNZ pulse that subclasses `Pulse` directly used to come back as the stock
    # SNZPulse with every lab-only knob dropped. Its fields are gated by its own env
    # field list, and a NEW default the lab class cannot hold is dropped (see _merge).
    container = path.split(".")[-2] if path.count(".") >= 1 else ""
    base = _CONTAINER_BASES.get(container)
    if base and base in bases:
        return o_cls
    return None


# an untagged container -> the one base every element of it must subclass
_CONTAINER_BASES = {"operations": "quam.components.pulses.Pulse"}


def _lab_object_admissible(cls: str, old: dict, new: dict, kept_cls: str | None,
                           keep: dict | None) -> bool:
    """v1.6.0 review: may an OLD object of a class the rebuild never wrote be grafted
    under this parent? Yes when BOTH hold: the build env imports the class (it is in
    `keep`), and the parent is the LAB's own class -- the kept class, or the same lab
    class on both sides (a lab root the spec carried) -- whose declared field this is
    (the caller checked `k in legal`). The lab wrote both the parent's declaration and
    the object, and the source chip is their own serialisation. A stock parent of the
    same name is NOT enough: another stack generation may declare the field with
    another type, which is the docs/136 poison this gate exists for."""
    if not keep or not keep.get(cls):
        return False
    if kept_cls:
        return True
    parent = old.get("__class__")
    return (isinstance(parent, str) and parent == new.get("__class__") and bool(keep.get(parent))
            and not parent.startswith(("quam.", "quam_builder.")))


_MISSING = object()


def _one_hop(doc: dict, pointer: str, field_path: tuple[str, ...]) -> Any:
    """The value ONE hop down a QUAM pointer, read syntactically from ``doc`` -- no
    further pointer is followed. ``#/a/b`` is absolute; ``#./x`` names a sibling in the
    object holding the field; ``#../x`` an attribute of that object's parent.
    ``_MISSING`` when the path is not there. One hop on purpose: a chain that passes
    back through the holder's own object is a cycle in the merged document, and the
    merge decides pointer by pointer, so only a direct literal is ever trusted."""
    if pointer.startswith("#/"):
        parts = [p for p in pointer[2:].split("/") if p]
    else:
        base = list(field_path[:-1])
        for seg in pointer[1:].split("/"):
            if seg == "..":
                base = base[:-1]
            elif seg and seg != ".":
                base.append(seg)
        parts = base
    node: Any = doc
    for p in parts:
        if isinstance(node, dict) and p in node:
            node = node[p]
        elif isinstance(node, list) and p.isdigit() and int(p) < len(node):
            node = node[int(p)]
        else:
            return _MISSING
    return node


def _is_literal(v: Any) -> bool:
    return v is not _MISSING and v is not None and not is_pointer(v) and not isinstance(v, (dict, list))


def _pointer_decisions(old: dict, new_doc: dict) -> tuple[set[str], dict[str, Any]]:
    """v1.6.0 review: two decisions about SOURCE pointers, taken up front on whole documents.

    ``keep``: dot paths where the source holds a pointer, the rebuild wrote a literal, and
    the pointer's direct target in the rebuilt document is a literal equal to it. A lab's
    single-source link (both pump channels' ``RF_frequency = "#../pump_frequency"``) used
    to be unlinked by every rebuild; keeping it changes no value. A pointer whose target
    is another pointer, something else (the Populate step changed the literal) or nothing
    is NOT kept -- the literal stands.

    ``inline``: dot paths of source pointer leaves the rebuild has NO value for (they
    will be grafted) whose direct target in the rebuilt document is not a literal, mapped
    to the value the pointer resolved to in the SOURCE. Grafting the pointer itself would
    dangle or -- when the rebuild inverted a link's direction, values on the z-line op
    and the macro pointing at it where the lab had the reverse -- close a cycle, which
    is how a kept lab pulse's ``inferred_length`` came back as nothing and
    ``generate_config`` asserted. The lab's VALUE is what the physics needs; the link
    is reported as inlined."""
    from quam_state_manager.core.differ import compare_equal
    from quam_state_manager.core.pointer_resolver import resolve_pointer
    keep: set[str] = set()
    inline: dict[str, Any] = {}
    unlink: dict[str, Any] = {}

    def walk(o: Any, n: Any, path: tuple[str, ...]) -> None:
        if isinstance(o, dict):
            for k, ov in o.items():
                walk(ov, n.get(k, _MISSING) if isinstance(n, dict) else _MISSING, path + (k,))
            return
        if not is_pointer(o):
            # the mirror case: the SOURCE holds its own literal (a lab that calibrated
            # x90's DRAG alpha apart from x180's) where the rebuild links the field to
            # another one holding a DIFFERENT value -- the link would silently replace
            # the calibration with its target's, so the literal is carried
            if (is_pointer(n) and _is_literal(o) and not isinstance(o, bool)
                    and isinstance(o, (int, float, str))):
                try:
                    nv = resolve_pointer(new_doc, n, path)
                except Exception:  # noqa: BLE001 -- unresolvable: the rebuild's link stands
                    return
                # ...but only where the SOURCE itself held the two apart: its value at
                # the link's target differs from its own literal. A source that held
                # them equal agreed with the link (a readout downconverter equal to the
                # upconverter it shares an LO with), so the link stands and follows a
                # value the user changed in Populate.
                try:
                    src_tgt = resolve_pointer(old_root, n, path)
                except Exception:  # noqa: BLE001
                    src_tgt = _MISSING
                if (_is_literal(nv) and type(nv) is not bool
                        and isinstance(nv, (int, float)) == isinstance(o, (int, float))
                        and not compare_equal(nv, o)
                        and _is_literal(src_tgt) and not compare_equal(src_tgt, o)):
                    unlink[".".join(path)] = o
            return
        tgt = _one_hop(new_doc, o, path)
        if n is _MISSING:
            # an old-only leaf: grafted -- as the pointer when its direct target is a
            # literal in the rebuilt document, else as the source's resolved value
            if _is_literal(tgt):
                return
            try:
                val = resolve_pointer(old, o, path)
            except Exception:  # noqa: BLE001 -- unresolvable in the source too: graft as is
                return
            if _is_literal(val):
                inline[".".join(path)] = val
            return
        if _is_literal(n) and _is_literal(tgt) and compare_equal(tgt, n):
            keep.add(".".join(path))
            return
        if _is_literal(n) and not _is_literal(tgt):
            # the rebuild wrote a literal and inverted or dropped the link's target:
            # the builder's literal is a seed, the source's resolved value is the
            # calibration (a lab whose CZ knobs live on the macro and the z op points
            # at them lost amplitude/flat_length/padding to Populate seeds here)
            try:
                val = resolve_pointer(old, o, path)
            except Exception:  # noqa: BLE001 -- unresolvable in the source: the literal stands
                return
            if _is_literal(val):
                inline[".".join(path)] = val

    old_root = old
    walk(old, new_doc, ())
    # an unlinked literal is a value carried at a leaf the rebuild made a pointer:
    # reuse the inline channel (the leaf branch carries it unless Populate holds it)
    inline.update(unlink)
    return keep, inline


_DROPPED = object()   # _gate_graft: the whole object is dropped


def _gate_graft(obj: Any, path: str, schemas: dict | None,
                env_fields: dict, stats: MergeStats,
                inline: dict[str, Any] | None = None) -> Any:
    """A deep copy of the OLD subtree ``obj`` about to be grafted at ``path``,
    gated at EVERY depth by the build env (QA regenerate-r2-15).

    The tier-2 gate checks a graft ROOT against its immediate parent; a
    subtree grafted under an untagged container (a TWPA under ``twpas``, a
    lab op under ``operations``) was copied whole, so a rebuild in an
    older-generation env reported "unsafe fields dropped" and still wrote a
    chip ``Quam.load()`` refused (``extras is not a valid attr of
    ...twpas["twpa1"].pump``). ``env_fields`` is the build env's own probe of
    the SOURCE chip's classes: ``{cls: [field, ...]}`` for a dataclass it
    imports, ``{cls: None}`` for one it cannot import. Here:

    - an object whose class the env cannot import is dropped whole (quam
      would fall back to the declared base type, whose fields then kill the
      load) -- its path lands in ``stats.schema_dropped``;
    - a key outside its class's fields is dropped (``schema_dropped``);
    - a class with no answer is copied as-is (the legacy graft).

    Only ever NARROWS what is copied; returns ``_DROPPED`` for a dropped root.
    """
    if not isinstance(obj, dict):
        if inline and path in inline and is_pointer(obj):
            # v1.6.0 review: see _pointer_decisions -- the source's resolved value
            stats.pointers_inlined.append(path)
            return copy.deepcopy(inline[path])
        return copy.deepcopy(obj)
    cls = obj.get("__class__")
    legal = None
    if isinstance(cls, str):
        if cls in env_fields and env_fields[cls] is None:
            stats.schema_dropped.append(path)
            return _DROPPED
        legal = env_fields.get(cls)
        if legal is None and schemas:
            legal = schemas.get(cls)
    out: dict = {}
    for k, v in obj.items():
        if k in ("__class__", "__package_versions__"):
            out[k] = copy.deepcopy(v)
            continue
        sub = f"{path}.{k}"
        if legal is not None and k not in legal:
            stats.schema_dropped.append(sub)
            continue
        g = _gate_graft(v, sub, schemas, env_fields, stats, inline)
        if g is not _DROPPED:
            out[k] = g
    return out


def _merge(old: Any, new: Any, path: str, stats: MergeStats,
           schemas: dict[str, list[str]] | None = None,
           protect: set[str] | None = None,
           keep: dict | None = None,
           carry_ports: set[str] | None = None,
           env_fields: dict | None = None,
           keep_ptr: set[str] | None = None,
           inline: dict[str, Any] | None = None) -> Any:
    if isinstance(old, dict) and isinstance(new, dict):
        out: dict = {}
        # Keys whose OLD typed object met a NEW null: decided by the tier-2
        # graft loop below (schema gate + dangling check), never tier-1.
        deferred: set = set()
        # Never at the root: the root has its own slot in the build spec
        # (docs/176 `spec.quam_class`, the Review step's "Chip root class"),
        # and the build wrote exactly the class the user named there. The
        # docs/202 §15 keep is for objects below it the spec cannot name.
        kept_cls = _kept_class(old, new, keep, path) if path else None
        # the kept class's own fields: a NEW default the lab class does not declare
        # (a stock class the builder wrote where the lab's own is kept) must not ride
        # into the object -- Quam.load() refuses an unexpected attribute
        kept_fields = set(keep[kept_cls].get("fields") or ()) if kept_cls else None
        for k, nv in new.items():
            if (kept_fields is not None and k not in kept_fields
                    and k not in ("__class__", "__package_versions__")):
                stats.schema_dropped.append(f"{path}.{k}" if path else k)
                continue
            if k in ("__class__", "__package_versions__"):
                # Always the NEW build's. Tier-1-carrying an OLD
                # __package_versions__ stamp would lie about which stack wrote
                # the rebuilt state, and an OLD __class__ would type the object
                # for a class this build never wrote.
                #
                # But __class__ is NOT an artifact the way the version stamp
                # is: it says what this object IS. When the rebuild types an
                # object differently from the source chip -- a lab's own
                # subclass replaced by the stock one the builder knows -- the
                # object loses every field only the lab's class declares, and
                # those drop out through the schema gate below as
                # `schema_dropped` paths. That reports the CONSEQUENCE
                # (`...readout.weights_real` was dropped) and never the CAUSE
                # (your ComplexWeightsReadoutPulse was rebuilt as a
                # SquareReadoutPulse), which is the thing a reader needs in
                # order to know what to do about it.
                if k == "__class__" and kept_cls:
                    out[k] = kept_cls
                    stats.class_kept.append((path or "(root)", kept_cls))
                    continue
                if (k == "__class__" and isinstance(nv, str)
                        and isinstance(old.get(k), str) and old[k] != nv):
                    stats.class_changed.append((path or "(root)", old[k], nv))
                out[k] = copy.deepcopy(nv)
                continue
            if k in STRUCTURAL_LEAF_KEYS:               # membership -> always NEW
                if not path and k in _ACTIVE_OF:
                    out[k] = _active_list(k, old, new, nv)
                    if out[k] != nv:
                        stats.active_kept.append(k)
                    continue
                out[k] = copy.deepcopy(nv)
                stats.kept_new_pointer += 1
                continue
            if k in old:
                ov = old[k]
                # An object and a null never meet as tier-1 scalars. The leaf
                # branch below would carry the whole OLD object onto a field
                # the rebuild left EMPTY (a CR rebuild of a flux chip wrote
                # `z: null`; the old FluxLine came back pointing at wiring the
                # rebuild never made -- generate_config() crashed with the
                # build reporting success), skipping the schema gate and the
                # dangling check; or carry an OLD null over a channel the
                # rebuild CREATED (every CR/ZZ channel of that chip erased).
                if (nv is None and isinstance(ov, dict)
                        and isinstance(ov.get("__class__"), str)):
                    out[k] = None
                    deferred.add(k)
                    continue
                if ov is None and isinstance(nv, dict):
                    out[k] = copy.deepcopy(nv)          # NEW keeps structure
                    stats.kept_new_only += _count_leaves(nv)
                    continue
                out[k] = _merge(ov, nv, f"{path}.{k}" if path else k,
                                stats, schemas, protect, keep, carry_ports,
                                env_fields, keep_ptr, inline)
            else:
                out[k] = copy.deepcopy(nv)
                stats.kept_new_only += _count_leaves(nv)
        # tier 2: additive graft -- but NEVER resurrect a removed structural
        # entity. For qubits/qubit_pairs the entity is a DIRECT child, and keys
        # DEEPER than that are user-added operations/macros we DO want to graft,
        # so block only the direct-child level. For ports/octaves/mixers the
        # whole subtree is rebuild-authoritative hardware config (a port entity
        # lives 3-4 levels deep, e.g. ports.mw_outputs.con1.1.2, with no
        # user-addable leaves), so block the graft at EVERY depth — otherwise a
        # removed qubit's now-unallocated port was resurrected wholesale.
        top = path.split(".", 1)[0] if path else ""
        graftable_here = not (path in ENTITY_COLLECTIONS
                              or top in _HW_ENTITY_COLLECTIONS)
        # Cross-generation schema gate: a dict carrying __class__ IS a quam
        # object, so its keys are dataclass attributes — quam raises
        # AttributeError('Unexpected attribute') on any it doesn't know. When
        # the build reported the NEW env's field schema for this class, an
        # OLD-only key outside it is a field the new stack renamed/removed
        # (e.g. CZGate.duration_control -> duration_qubit in quam_builder
        # 0.4.0) — grafting it would poison Quam.load(). Drop it VISIBLY via
        # stats.schema_dropped. Untagged container dicts (operations / macros /
        # extras) have no schema and keep grafting user-added subtrees.
        legal: list[str] | None = None
        if kept_cls:
            # This object IS the kept class now, so its own declared fields are
            # what is legal -- and only here: `keep` never widens the global
            # `schemas`, which the value gate below also reads, so a lab class
            # kept at one path cannot become graftable anywhere else.
            legal = list(keep[kept_cls].get("fields") or ())
        elif schemas:
            cls = new.get("__class__")
            if isinstance(cls, str):
                legal = schemas.get(cls)
        # Attributes of an existing port are distinct from hardware entities.
        # Only the destination class's declared fields may be grafted.
        if top == "ports" and "port_id" in new:
            graftable_here = True
        for k, ov in old.items():
            if ((k in new and k not in deferred)
                    or k in ("__class__", "__package_versions__")
                    or k in STRUCTURAL_LEAF_KEYS):
                # __package_versions__ is quam's serialization stamp, not user
                # data — never carry a stale one onto a rebuilt state.
                continue
            if not graftable_here:
                # docs/202 §17: the one exception under `ports` -- a port the
                # source chip DECLARED but nothing referenced cannot be a
                # removed qubit's port, so it is carried (decided up front by
                # _carryable_ports, which also requires its FEM to still be
                # in the rebuild).
                sub = f"{path}.{k}" if path else k
                if carry_ports and top == "ports":
                    kept = _only_carried(ov, sub, carry_ports)
                    if kept is not None and env_fields is not None:
                        kept = _gate_graft(kept, sub, schemas, env_fields, stats)
                        if kept is _DROPPED:
                            kept = None
                    if kept is not None:
                        out[k] = kept
                        n = _count_leaves(kept)
                        stats.grafted += n
                        stats.graft_subtrees.append((sub, n))
                        stats.ports_carried.extend(
                            p for p in carry_ports
                            if p == sub or p.startswith(sub + "."))
                continue                                # removed entity -> residual_lost
            if legal is not None and k not in legal:
                stats.schema_dropped.append(f"{path}.{k}" if path else k)
                continue
            # docs/136 — the same gate, one level down: the FIELD is legal but
            # its old VALUE is an object of a class this build never wrote.
            # The case that found it: a QDAC chip re-generated in an env
            # without quam_config degrades to a qubit with no `z` at all, and
            # grafting the old `QdacBiasLine` back onto it produces a state
            # whose `z` is typed for something else — Quam.load() fails on the
            # first such qubit, with the build reporting success.
            #
            # Only under a TAGGED parent (legal is not None). An untagged
            # container — `operations`, `macros`, `extras` — has no declared
            # types to violate, and that is where a user's own added pulse
            # class legitimately lives; gating there would break the tier-2
            # graft this whole branch exists for.
            unwritten_class = (legal is not None and schemas and isinstance(ov, dict)
                               and isinstance(ov.get("__class__"), str)
                               and ov["__class__"] not in schemas)
            if unwritten_class and _lab_object_admissible(ov["__class__"], old, new, kept_cls, keep):
                # v1.6.0 review: the env imports the class and the parent is the
                # lab's own class declaring this field -- a lab TWPA at a lab root,
                # a lab target pulse on a kept lab gate. Grafted, gated below by the
                # class's own env fields (never a legacy deep copy).
                stats.lab_objects_kept.append((f"{path}.{k}" if path else k, ov["__class__"]))
                view = env_fields if env_fields is not None else {
                    c: (r or {}).get("fields") for c, r in keep.items()}
                gv = _gate_graft(ov, f"{path}.{k}" if path else k, schemas, view, stats, inline)
                if gv is _DROPPED:
                    continue
                out[k] = gv
                n = _count_leaves(gv)
                stats.grafted += n
                stats.graft_subtrees.append((f"{path}.{k}" if path else k, n))
                if k in deferred:
                    stats.deferred_grafted.append(f"{path}.{k}" if path else k)
                continue
            if unwritten_class:
                if k in deferred:
                    # QA review of r2-14: the FIELD is known; the rebuild left
                    # it empty and writes no such object anywhere -- not an
                    # "old-stack field this env doesn't know".
                    cls = ov["__class__"].rsplit(".", 1)[-1]
                    stats.rebuild_removed.append((
                        f"{path}.{k}" if path else k,
                        f"old {cls} not put back — this build writes no {cls}"))
                    continue
                stats.schema_dropped.append(f"{path}.{k}" if path else k)
                continue
            sub = f"{path}.{k}" if path else k
            if env_fields is not None:
                # QA regenerate-r2-15: the checks above look at the graft ROOT
                # only; the subtree under it is gated here, at every depth,
                # by the build env's own answer for every class in it.
                gv = _gate_graft(ov, sub, schemas, env_fields, stats, inline)
                if gv is _DROPPED:
                    continue
            elif inline and sub in inline:
                # v1.6.0 review: a source pointer the rebuilt state gives no literal
                # target -- grafted as the value it resolved to in the source
                gv = copy.deepcopy(inline[sub])
                stats.pointers_inlined.append(sub)
            else:
                gv = copy.deepcopy(ov)
            out[k] = gv
            n = _count_leaves(gv)
            stats.grafted += n
            stats.graft_subtrees.append((f"{path}.{k}" if path else k, n))
            if k in deferred:
                stats.deferred_grafted.append(f"{path}.{k}" if path else k)
        return out
    # leaves ---------------------------------------------------------------
    if is_pointer(new) or is_pointer(old):              # structure/pointer -> NEW
        if keep_ptr and path in keep_ptr:
            # ...except a SOURCE pointer the rebuild replaced with the very literal
            # it resolves to (decided up front by _old_pointers_to_keep): the lab's
            # link is kept, no value changes (v1.6.0 review)
            stats.old_pointers_kept.append(path)
            return old
        if inline and path in inline and not (protect and path in protect):
            # a source pointer whose link the rebuild inverted, or a source literal the
            # rebuild linked to a different value: carry the source's value (see
            # _pointer_decisions)
            (stats.links_not_taken if is_pointer(new) else stats.pointers_inlined).append(path)
            stats.carried += 1
            return copy.deepcopy(inline[path])
        stats.kept_new_pointer += 1
        return new
    if protect and path in protect:
        # Populate-protect (docs/72): this leaf carries a value the user
        # DELIBERATELY changed in the re-generate wizard's Populate step —
        # the fresh build already applied it; tier-1 carrying the OLD value
        # here would silently revert the user's edit (the r16 report: "the
        # whole point of re-gen is changing existing values").
        stats.populate_protected.append(path)
        return copy.deepcopy(new)
    parts = path.split(".")
    if (old is None and len(parts) >= 3 and parts[-3] == "operations"
            and parts[-1] == "id" and new == parts[-2]):
        stats.id_filled += 1
        return new
    stats.carried += 1                                   # tier 1: carry calibration
    return copy.deepcopy(old)


def _pair_membership(pair: Any, root: dict | None = None) -> tuple[str, str] | None:
    """(control_qubit_name, target_qubit_name) for a pair, from its refs.

    docs/118: the refs can be TWO-hop pointers (`#/wiring/qubit_pairs/<p>/c/
    control_qubit` -> `#/qubits/qX`). Reading only the last path segment gave
    the literal field name, so pair-id reconciliation matched nothing and every
    pair's calibration was reported lost. `root` is optional so the pure
    last-segment behaviour still applies to a one-hop chip when no document is
    available to resolve against.
    """
    if not isinstance(pair, dict):
        return None
    c, t = pair.get("qubit_control"), pair.get("qubit_target")
    if not (isinstance(c, str) and isinstance(t, str)):
        return None
    if root is not None:
        cn = regen_spec.qubit_ref_name(root, c)
        tn = regen_spec.qubit_ref_name(root, t)
        if cn and tn:
            return (cn, tn)
    return (c.split("/")[-1], t.split("/")[-1])


def _merged_doc(state: dict, wiring: dict | None) -> dict:
    """state + its wiring, the shape an absolute `#/...` pointer resolves in."""
    doc = dict(state)
    if isinstance(wiring, dict):
        doc["wiring"] = wiring.get("wiring", wiring)
    return doc


def _reconcile_pair_ids(old_state: dict, new_state: dict,
                        old_wiring: dict | None = None,
                        new_wiring: dict | None = None) -> dict:
    """Rename NEW ``qubit_pairs`` keys to the OLD ids where the (control, target)
    membership matches.

    The builder may name a pair differently from the source chip (e.g. it emits
    ``qA2-A1`` where the source has ``qA2-qA1`` -- the target's ``q`` prefix
    convention drifts). Both still reference the same qubits, so we align on that
    and adopt the source id, otherwise the id-keyed merge orphans EVERY pair's
    calibration. Safe to rename without pointer rewriting: nothing in a QUAM state
    references a pair by ``#/qubit_pairs/<id>`` (verified on real archives).
    Returns ``new_state`` (a shallow copy when a rename was needed).
    """
    # docs/118: resolve each side's refs against ITS OWN document — a two-hop
    # pointer only means something inside the state it came from.
    old_by_mem: dict[tuple[str, str], str] = {}
    for oid, op in (old_state.get("qubit_pairs") or {}).items():
        m = _pair_membership(op, _merged_doc(old_state, old_wiring))
        if m is not None:
            old_by_mem.setdefault(m, oid)
    if not old_by_mem:
        return new_state

    new_pairs = new_state.get("qubit_pairs") or {}
    remapped: dict[str, Any] = {}
    changed = False
    for nid, npair in new_pairs.items():
        target = old_by_mem.get(
            _pair_membership(npair, _merged_doc(new_state, new_wiring)))
        key = target if (target and target not in remapped) else nid
        if key != nid:
            changed = True
        remapped[key] = npair
    if not changed:
        return new_state
    out = dict(new_state)
    out["qubit_pairs"] = remapped
    return out


def _reversed_pairs(old_state: dict, new_state: dict,
                    old_wiring: dict | None = None,
                    new_wiring: dict | None = None) -> list[tuple[str, str]]:
    """``(old id, new id)`` for each OLD pair the rebuild has only with its
    control and target swapped (QA regenerate-r2-09).

    Detection only -- the merge still matches pairs by ORDERED membership (a
    CR chip keeps q0-4 and q4-0 as two objects, and phase shifts, the 4x4
    confusion basis and the moving role all depend on orientation), so the
    old pair's values are not carried. This names the pair in the report
    instead of leaving only its raw ``qubit_pairs.<id>.*`` paths.
    ``new_state`` is the post-:func:`_reconcile_pair_ids` state.
    """
    new_pairs = new_state.get("qubit_pairs") or {}
    new_doc = _merged_doc(new_state, new_wiring)
    new_by_mem: dict[tuple[str, str], str] = {}
    for nid, npair in new_pairs.items():
        m = _pair_membership(npair, new_doc)
        if m is not None:
            new_by_mem.setdefault(m, nid)
    old_doc = _merged_doc(old_state, old_wiring)
    out: list[tuple[str, str]] = []
    for oid, opair in (old_state.get("qubit_pairs") or {}).items():
        if oid in new_pairs:
            continue
        m = _pair_membership(opair, old_doc)
        if m is None or m in new_by_mem:
            continue
        rev = new_by_mem.get((m[1], m[0]))
        if rev is not None:
            out.append((oid, rev))
    return sorted(out, key=lambda t: natural_key(t[0]))


def _enclosing_op(dot_path: str) -> str | None:
    """The ``…operations.<name>`` prefix of ``dot_path`` (a single operation
    subtree), or None if the path isn't inside an ``operations`` dict."""
    segs = dot_path.split(".")
    if "operations" in segs:
        i = segs.index("operations")
        if i + 1 < len(segs):
            return ".".join(segs[: i + 2])
    return None


def _del_path(root: dict, dot_path: str) -> bool:
    """Delete the node at ``dot_path``; True if removed. Best-effort."""
    segs = dot_path.split(".")
    node: Any = root
    for seg in segs[:-1]:
        if isinstance(node, dict) and seg in node:
            node = node[seg]
        else:
            return False
    if isinstance(node, dict) and segs[-1] in node:
        del node[segs[-1]]
        return True
    return False


def _prune_redundant_graft_ops(merged: dict, dangling: list[str]) -> tuple[list[str], list[str]]:
    """Remove OLD operation subtrees that the rebuild superseded and left broken.

    A merged chip can carry an OLD-representation pulse operation (e.g. the old
    ``…z.operations.cz_unipolar_pulse_qA1``) that the fresh build re-expressed
    under a new name (``cz_unipolar_flux_pulse_qA2_qA1``) — the old copy is an
    unreferenced orphan whose internal pointers no longer resolve. Such an op is
    provably safe to drop when **(a)** it lives under an ``operations`` dict,
    **(b)** every one of its absolute pointer leaves is dangling (none resolve),
    and **(c)** nothing in the merged tree points at it. Entity-collection leaves
    that are *not* inside an ``operations`` dict (e.g. a preserved TWPA's
    ``pump.opx_output`` wiring pointer) never match (a) and are kept.

    Returns ``(pruned_op_paths, remaining_dangling)``.
    """
    # candidate ops: enclosing operation of each dangling leaf
    cand: dict[str, list[str]] = {}
    for p in dangling:
        op = _enclosing_op(p)
        if op is not None:
            cand.setdefault(op, []).append(p)
    if not cand:
        return [], list(dangling)

    # every absolute pointer VALUE present in the merged tree (for the ref check)
    referenced = {v for _, v in _iter_leaves(merged)
                  if isinstance(v, str) and v.startswith("#/")}

    prunable: list[str] = []
    for op in cand:
        op_ptr = "#/" + op.replace(".", "/")
        if op_ptr in referenced:                    # (c) something still points at it
            continue
        node = merged
        for seg in op.split("."):
            node = node.get(seg) if isinstance(node, dict) else None
            if node is None:
                break
        if not isinstance(node, dict):
            continue
        ptr_leaves = [v for _, v in _iter_leaves(node)
                      if isinstance(v, str) and v.startswith("#/")]
        # (b) has broken pointers AND none that still resolve
        if ptr_leaves and all(not _resolves(merged, v) for v in ptr_leaves):
            prunable.append(op)

    for op in prunable:
        _del_path(merged, op)
    pruned = set(prunable)
    remaining = [p for p in dangling if _enclosing_op(p) not in pruned]
    return sorted(prunable, key=natural_key), remaining


def _norm_twpa_id(tid: str) -> str:
    """``twpaA``/``twpa1`` and ``A``/``1`` are the same TWPA across builder
    generations (qualang_tools renders elements as ``f"twpa{id}"``, so
    run_build strips the redundant prefix before add_twpa_lines)."""
    return tid[4:] if tid.lower().startswith("twpa") and len(tid) > 4 else tid


def reconcile_twpa_ids(new_state: dict, new_wiring: dict,
                       old_state: dict) -> dict[str, str]:
    """Rename the NEW build's TWPA ids onto the OLD chip's when they differ
    only by the ``twpa`` prefix (a builder generation that does NOT re-prepend
    the prefix would otherwise leave a zombie ``twpas.A`` beside the grafted
    ``twpas.twpaA`` — dangling wiring pointers, double entries). Mirrors the
    pair-id reconciliation. Rewrites the state/wiring keys AND every internal
    ``#/twpas/<id>/…`` / ``#/wiring/twpas/<id>/…`` pointer plus
    ``active_twpa_names`` entries. Mutates in place; returns the applied
    ``{new_id: old_id}`` map (empty = ids already agree, the common case)."""
    new_t = new_state.get("twpas")
    old_ids = list((old_state.get("twpas") or {}).keys())
    if not isinstance(new_t, dict) or not new_t or not old_ids:
        return {}
    by_norm = {_norm_twpa_id(o).lower(): o for o in old_ids}
    mapping = {}
    for nid in list(new_t.keys()):
        oid = by_norm.get(_norm_twpa_id(nid).lower())
        if oid and oid != nid and oid not in new_t:
            mapping[nid] = oid
    if not mapping:
        return {}

    for nid, oid in mapping.items():
        new_t[oid] = new_t.pop(nid)
    wt = (new_wiring.get("wiring") or {}).get("twpas")
    if isinstance(wt, dict):
        for nid, oid in mapping.items():
            if nid in wt and oid not in wt:
                wt[oid] = wt.pop(nid)
    names = new_state.get("active_twpa_names")
    if isinstance(names, list):
        new_state["active_twpa_names"] = [mapping.get(n, n) for n in names]

    prefixes = {}
    for nid, oid in mapping.items():
        prefixes[f"#/twpas/{nid}/"] = f"#/twpas/{oid}/"
        prefixes[f"#/wiring/twpas/{nid}/"] = f"#/wiring/twpas/{oid}/"

    def rewrite(node):
        if isinstance(node, dict):
            for k, v in node.items():
                node[k] = rewrite(v)
            return node
        if isinstance(node, list):
            return [rewrite(v) for v in node]
        if isinstance(node, str):
            for np, op in prefixes.items():
                if node.startswith(np):
                    return op + node[len(np):]
        return node

    rewrite(new_state)
    rewrite(new_wiring)
    return mapping


def _twpas_the_spec_removed(old_state: dict, new_state: dict,
                           old_wiring: dict | None,
                           twpa_ids: Any) -> set[str]:
    """OLD TWPAs the user took off the step-4 list -- renamed or deleted
    (QA regenerate-r2-10).

    ``twpas`` is graftable on purpose: a builder with no TWPA support emits
    none, and a missing TWPA then is a builder gap, not a removal. But the
    wizard OFFERED every TWPA the source wiring declares (reconstruct_spec
    builds the step-4 rows from exactly those); one that is no longer in
    ``twpa_ids`` and that the rebuild did not build was removed by the user,
    and grafting it back built two TWPAs on one port -- the old one whole and
    the renamed one uncalibrated. ``twpa_ids`` None (no spec information)
    means graft all, exactly as before; ids compare through the same
    ``twpa`` prefix rule :func:`reconcile_twpa_ids` uses.
    """
    if twpa_ids is None:
        return set()
    old_t = old_state.get("twpas")
    if not isinstance(old_t, dict) or not old_t:
        return set()
    w = old_wiring.get("wiring", old_wiring) if isinstance(old_wiring, dict) else {}
    wt = w.get("twpas") if isinstance(w, dict) else None
    offered = {k for k, v in (wt.items() if isinstance(wt, dict) else ())
               if isinstance(v, dict)}
    new_t = new_state.get("twpas")
    built = set(new_t) if isinstance(new_t, dict) else set()
    requested = {_norm_twpa_id(str(i)).lower() for i in twpa_ids if i}
    return {k for k in old_t
            if k in offered and k not in built
            and _norm_twpa_id(k).lower() not in requested}


def _twpa_port_refs(wiring: dict | None, tid: str) -> set[str]:
    """Every ``#/ports/...`` pointer a TWPA's wiring entry holds (its pump
    line, its isolation line)."""
    w = wiring.get("wiring", wiring) if isinstance(wiring, dict) else {}
    wt = w.get("twpas") if isinstance(w, dict) else None
    out: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, str) and node.startswith("#/ports/"):
            out.add(node)

    walk(wt.get(tid) if isinstance(wt, dict) else None)
    return out


def _twpas_the_spec_renamed(old_state: dict, new_state: dict,
                           old_wiring: dict | None, new_wiring: dict | None,
                           removed: set[str]) -> dict[str, str]:
    """``{old id: new id}`` -- the removed TWPAs the user only RENAMED on
    step 4 (QA review of regenerate-r2-10).

    A rename is a removal plus a TWPA the source never had; what makes them
    ONE device is the line: the rebuilt TWPA's wiring lands on a port the
    removed one's wiring used (the wizard re-keys the pump pin with the id).
    Matched one-to-one only -- two candidates either way is not a rename, and
    a TWPA the rebuild put on other ports stays a removal plus a new one.
    """
    new_t = new_state.get("twpas")
    old_t = old_state.get("twpas") or {}
    if not removed or not isinstance(new_t, dict):
        return {}
    fresh = [k for k in new_t if k not in old_t]
    cand: dict[str, list[str]] = {}
    for o in removed:
        o_ports = _twpa_port_refs(old_wiring, o)
        cand[o] = [n for n in fresh
                   if o_ports & _twpa_port_refs(new_wiring, n)] if o_ports else []
    out: dict[str, str] = {}
    for o, ns in cand.items():
        if len(ns) == 1 and sum(ns[0] in v for v in cand.values()) == 1:
            out[o] = ns[0]
    return out


def _renamed_twpa_view(old_twpa: Any, old_id: str, new_id: str,
                       new_twpa: Any) -> Any:
    """The OLD TWPA as the merge should see it under its NEW id: its own
    ``#/twpas/<old>/`` / ``#/wiring/twpas/<old>/`` pointers re-keyed, and its
    ``id`` the rebuild's (tier-1 would otherwise carry the old name back)."""
    prefixes = {f"#/twpas/{old_id}/": f"#/twpas/{new_id}/",
                f"#/wiring/twpas/{old_id}/": f"#/wiring/twpas/{new_id}/"}

    def rewrite(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: rewrite(v) for k, v in node.items()}
        if isinstance(node, list):
            return [rewrite(v) for v in node]
        if isinstance(node, str):
            for op, np in prefixes.items():
                if node.startswith(op):
                    return np + node[len(op):]
        return node

    view = rewrite(copy.deepcopy(old_twpa))
    if isinstance(view, dict) and "id" in view:
        view["id"] = (copy.deepcopy(new_twpa["id"])
                      if isinstance(new_twpa, dict) and "id" in new_twpa
                      else new_id)
    return view


def graft_twpa_wiring(merged_state: dict, old_state: dict,
                      old_wiring: dict, new_wiring: dict,
                      env_fields: dict | None = None,
                      schemas: dict | None = None,
                      stats: MergeStats | None = None) -> int:
    """Carry a preserved TWPA's wiring + ports from OLD into the rebuilt config.

    The state merge grafts the OLD ``twpas`` back (the builder can't rebuild
    them), but each TWPA channel points through ``#/wiring/twpas/… → #/ports/…``
    — neither of which the fresh build produced, so ``generate_config()`` crashes
    on the unresolved channel (``'str' has no attribute 'port_tuple'``). This
    copies the OLD ``wiring.twpas`` entries + the OLD ports they reference into
    the rebuilt wiring/state, filling only ABSENT keys (a builder-allocated port
    is never overwritten — verified no collision: TWPAs sit on dedicated ports).
    Mutates ``merged_state`` and ``new_wiring`` in place; returns the number of
    TWPA wiring entries carried (0 when there are no grafted TWPAs). See
    docs/51_regenerate_config.md.
    """
    twpas = merged_state.get("twpas") or {}
    old_wt = (old_wiring.get("wiring") or {}).get("twpas") or {}
    if not twpas or not old_wt:
        return 0
    new_wt = new_wiring.setdefault("wiring", {}).setdefault("twpas", {})
    carried = 0
    for tid in twpas:
        if tid in old_wt and tid not in new_wt:
            new_wt[tid] = copy.deepcopy(old_wt[tid])
            carried += 1
    # graft the ports the now-present TWPA wiring references, if absent in NEW.
    old_ports = old_state.get("ports") or {}
    merged_ports = merged_state.setdefault("ports", {})
    for ch in new_wt.values():
        for slot in (ch or {}).values():
            ptr = slot.get("opx_output") if isinstance(slot, dict) else None
            if not (isinstance(ptr, str) and ptr.startswith("#/ports/")):
                continue
            segs = ptr[2:].split("/")[1:]          # drop the leading 'ports'
            src: Any = old_ports
            dst = merged_ports
            ok = True
            for seg in segs[:-1]:
                src = src.get(seg) if isinstance(src, dict) else None
                if src is None:
                    ok = False
                    break
                dst = dst.setdefault(seg, {})
            if ok and isinstance(src, dict) and segs[-1] in src and segs[-1] not in dst:
                if env_fields is not None:
                    # QA regenerate-r2-15: gated like every other graft.
                    g = _gate_graft(src[segs[-1]], "ports." + ".".join(segs),
                                    schemas, env_fields,
                                    stats if stats is not None else MergeStats())
                    if g is not _DROPPED:
                        dst[segs[-1]] = g
                else:
                    dst[segs[-1]] = copy.deepcopy(src[segs[-1]])
    return carried


def _plain_value(v: Any) -> bool:
    if isinstance(v, list):
        return all(_plain_value(x) for x in v)
    return isinstance(v, (int, float, bool)) or (isinstance(v, str) and not is_pointer(v))


def graft_wiring_values(old_wiring: dict | None, new_wiring: dict) -> list[str]:
    """Carry the SOURCE's own plain values in ``wiring.wiring`` that the rebuild has no
    key for, under a line the rebuild also made (a lab's QDAC ``dc_channel: 1`` beside
    the flux line's ``opx_output``). Only plain values -- a pointer into ``ports`` is the
    rebuild's cabling and stays the rebuild's -- and only where the line still exists
    with the same set of port-reference keys. A change of line kind must not bring
    back settings from the old kind. Returns the dot paths carried."""
    if not isinstance(old_wiring, dict) or not isinstance(new_wiring, dict):
        return []
    carried: list[str] = []

    def walk(o: dict, n: dict, path: str) -> None:
        def port_keys(line: dict) -> set[str]:
            return {k for k, v in line.items()
                    if isinstance(v, str) and v.startswith("#/ports/")}

        if port_keys(o) != port_keys(n):
            return
        for k, ov in o.items():
            sub = f"{path}.{k}"
            if k in n:
                if isinstance(ov, dict) and isinstance(n[k], dict):
                    walk(ov, n[k], sub)
                continue
            if ov is not None and _plain_value(ov):
                n[k] = copy.deepcopy(ov)
                carried.append(sub)

    ow, nw = old_wiring.get("wiring"), new_wiring.get("wiring")
    if isinstance(ow, dict) and isinstance(nw, dict):
        for k, ov in ow.items():
            if k != "network" and isinstance(ov, dict) and isinstance(nw.get(k), dict):
                walk(ov, nw[k], f"wiring.{k}")
    return sorted(carried, key=natural_key)


def graft_network_settings(old_wiring: dict | None, new_wiring: dict,
                           held: list[str] | None = None,
                           requested: dict | None = None) -> list[str]:
    """Carry the source chip's ``wiring.network`` keys the build did not write.

    QA regenerate-r2-17: the wizard edits only ``host`` / ``cluster_name`` /
    ``port`` and the build writes a fresh ``network`` block from them, so every
    other key -- a custom/cloud QMM's ``qmm_class`` / ``qmm_settings`` /
    ``use_custom_qmm`` -- silently vanished and the rebuilt chip could not reach
    its backend. Fills ABSENT keys only: what the build wrote (the user's
    step-2 values, a ``port: None`` included) is never overwritten. Mutates
    ``new_wiring`` in place; returns the carried keys, naturally sorted.

    Only to the SAME place (QA review of r2-17): those keys route the QM
    connection (a cloud QMM ignores ``host``), so when step 2 moved the chip
    -- ``requested`` (the build spec's ``network``) names another ``host`` or
    ``cluster_name`` than the source -- carrying them would keep connecting
    to the old backend. Then nothing is carried and ``held`` (optional,
    filled in place) names every key held back. Compared against the SPEC,
    not the written block: the build writes ``0.0.0.0`` / ``Cluster`` for a
    blank one, which is no move -- nor is a value typed where the source had
    none (step 2 requires a cluster name). ``requested`` None carries as
    before.
    """
    old_net = (old_wiring or {}).get("network") if isinstance(old_wiring, dict) else None
    if not isinstance(old_net, dict) or not old_net:
        return []
    new_net = new_wiring.get("network")
    if not isinstance(new_net, dict):
        new_net = new_wiring["network"] = {}
    def norm(v: Any) -> str:
        return "" if v is None else str(v).strip()
    moved = isinstance(requested, dict) and any(
        norm(old_net.get(k)) and norm(old_net.get(k)) != norm(requested.get(k))
        for k in ("host", "cluster_name"))
    carried = []
    for k, v in old_net.items():
        if k not in new_net:
            if moved:
                if held is not None:
                    held.append(k)
                continue
            new_net[k] = copy.deepcopy(v)
            carried.append(k)
    if held is not None:
        held.sort(key=natural_key)
    return sorted(carried, key=natural_key)


# ---------------------------------------------------------------------------
# docs/202 §17 -- a declared port nothing references
#
# The merge never grafts under `ports`: a removed qubit's port must not come
# back. But a chip can DECLARE a port nothing uses (a spare line, a second pump
# kept for later), and that rule dropped it too -- on one customer chip the ten
# values still reported "not carried" after §15 were exactly one such port.
# Such a port cannot be a removed qubit's: in the SOURCE, nothing pointed at it.
# So it is carried, when all of these hold:
#   - nothing outside `ports` in the source (state or wiring) references it --
#     by absolute pointer, relative pointer, or a [con, fem, port] list;
#   - the rebuild has no port at that path;
#   - its FEM (controller + slot) still holds a port in the rebuild -- a port
#     on a FEM the new chip no longer uses would ask the config to program a
#     slot that may not be in the rack;
#   - every pointer inside it lands in the rebuild or in another carried port.
# ---------------------------------------------------------------------------

def _port_entities(ports: Any, prefix: str = "ports") -> dict[str, dict]:
    """Every port ENTITY -- a dict carrying ``__class__`` below the container
    itself -- keyed by dot-path. Descent stops at an entity."""
    out: dict[str, dict] = {}
    if not isinstance(ports, dict):
        return out
    for k, v in ports.items():
        if not isinstance(v, dict):
            continue
        p = f"{prefix}.{k}"
        if isinstance(v.get("__class__"), str):
            out[p] = v
        else:
            out.update(_port_entities(v, p))
    return out


def _port_hw(path: str, ent: dict) -> tuple:
    """(controller, fem) of a port entity -- its own ids first, the path second."""
    parts = path.split(".")
    con = ent.get("controller_id", parts[2] if len(parts) > 2 else None)
    fem = ent.get("fem_id", parts[3] if len(parts) > 4 else None)
    return (str(con), None if fem is None else str(fem))


def _pointer_target(src_path: str, pointer: str) -> str | None:
    """The dot-path a pointer lands on, for the pointer stored at ``src_path``.

    ``#/a/b`` is absolute. ``#./x`` is relative to the object holding the
    attribute, and each ``../`` climbs one more level (QUAM's own reading).
    """
    if pointer.startswith("#/"):
        return ".".join(s for s in pointer[2:].split("/") if s)
    base = src_path.split(".")[:-1]              # the object holding the leaf
    for seg in pointer[1:].split("/"):
        if seg == "..":
            if not base:
                return None
            base.pop()
        elif seg and seg != ".":
            base.append(seg)
    return ".".join(base)


def _walk_refs(obj: Any, path: str = ""):
    """Yield ``(src_path, kind, value)`` for every pointer string and every
    short ``[con, (fem,) port]`` list, lists descended."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk_refs(v, f"{path}.{k}" if path else str(k))
    elif isinstance(obj, list):
        if (2 <= len(obj) <= 3 and isinstance(obj[0], str)
                and all(isinstance(x, int) and not isinstance(x, bool)
                        for x in obj[1:])):
            yield path, "tuple", obj
        for i, v in enumerate(obj):
            yield from _walk_refs(v, f"{path}.{i}")
    elif is_pointer(obj):
        yield path, "pointer", obj


def _hits(target: str, entity: str) -> bool:
    """A target touches an entity when it is the entity, inside it, or a
    container above it (a pointer at a whole slot references every port in it)."""
    return (target == entity or target.startswith(entity + ".")
            or entity.startswith(target + "."))


def _carryable_ports(old_state: dict, new_state: dict,
                     old_wiring: dict | None = None,
                     new_wiring: dict | None = None,
                     declared_fems: dict | None = None) -> set[str]:
    old_ents = _port_entities(old_state.get("ports"))
    if not old_ents:
        return set()
    new_ents = _port_entities(new_state.get("ports"))
    new_hw = {_port_hw(p, e) for p, e in new_ents.items()}
    new_doc = _merged_doc(new_state, new_wiring)
    # (a port the rebuild has is merged normally -- it never reaches the graft)
    # ...or on a FEM the build spec still declares (one the rebuild drew no line on:
    # a lab's spare flux outputs keep their measured delay and filters), of the
    # matching kind -- an analog port on an LF FEM, an MW port on an MW FEM
    def declared(p: str, e: dict) -> bool:
        kind = (declared_fems or {}).get(_port_hw(p, e))
        if kind is None:
            return False
        group = p.split(".")[1] if p.count(".") else ""
        if group.startswith("mw_"):
            return kind == "mw"
        if group.startswith("analog_"):
            return kind == "lf"
        return True
    cand = {p for p, e in old_ents.items()
            if p not in new_ents and (_port_hw(p, e) in new_hw or declared(p, e))}
    if not cand:
        return set()

    internal: dict[str, list[str]] = {p: [] for p in cand}
    # A lab's own top-level object the rebuild writes nothing for (a QDAC `dc_bias`
    # whose triggers use OPX digital outputs) is grafted back by the merge when the env
    # holds its class; a port only IT references is its cabling, not a removed qubit's,
    # and is carried with it (the graft is still undone if anything fails to land).
    lab_tops = {k for k, v in old_state.items()
                if isinstance(v, dict) and isinstance(v.get("__class__"), str)
                and not v["__class__"].startswith(("quam.", "quam_builder."))
                and new_state.get(k) is None}
    for src, kind, val in _walk_refs(_merged_doc(old_state, old_wiring)):
        if src.split(".", 1)[0] in lab_tops:
            continue
        inside = src == "ports" or src.startswith("ports.")
        if kind == "tuple":
            if inside:
                continue
            con, *rest = val
            key = (str(con), str(rest[0]) if len(rest) == 2 else None)
            port = rest[-1]
            cand = {p for p in cand
                    if not (_port_hw(p, old_ents[p]) == key
                            and str(old_ents[p].get("port_id", p.rsplit(".", 1)[-1]))
                            == str(port))}
            continue
        target = _pointer_target(src, val)
        if target is None:
            continue
        if not inside:
            cand = {p for p in cand if not _hits(target, p)}
            continue
        owner = next((p for p in internal if src.startswith(p + ".")), None)
        if owner is not None:
            internal[owner].append(target)

    # every pointer a carried port holds must still land somewhere
    changed = True
    while changed:
        changed = False
        for p in sorted(cand):
            for t in internal.get(p, ()):
                if (_resolves(new_doc, "#/" + t.replace(".", "/"))
                        or any(t == c or t.startswith(c + ".") for c in cand)):
                    continue
                cand.discard(p)
                changed = True
                break
    return cand


def _only_carried(obj: Any, path: str, carry: set[str]) -> Any:
    """``obj`` pruned to the carried ports at or under ``path``; None if none."""
    if path in carry:
        return copy.deepcopy(obj)
    if not isinstance(obj, dict) or not any(c.startswith(path + ".") for c in carry):
        return None
    out = {}
    for k, v in obj.items():
        sub = _only_carried(v, f"{path}.{k}", carry)
        if sub is not None:
            out[k] = sub
    return out or None


# ---------------------------------------------------------------------------
# QA F1 -- a port's calibration follows the line that owns it
#
# Tier-1 matches leaves by PATH, and under `ports` the path is the port
# NUMBER. An LF port's delay / FIR / exponential filters (an MW port's band /
# LO / power) describe the line the port drives: moving q1's flux line from
# out1 to out6 left its 48-tap FIR on out1 -- dropped as 15 anonymous paths,
# or inherited whole by whatever new qubit the allocator put there -- and gave
# q1 fresh defaults on out6. A port's OWNERS are the channels in the STATE
# whose pointer chain lands on it; each rebuilt port takes the OLD values of
# the one old port its (surviving) owners came from. The port number stays the
# fallback only when no line identity is known on either side.
# ---------------------------------------------------------------------------

_PORT_IDENTITY_KEYS = ("controller_id", "fem_id", "port_id", "__class__")


def _land(doc: dict, dot_path: str, _depth: int = 0) -> str | None:
    """The dot-path ``dot_path`` really lands on in ``doc``, following every
    pointer met on the way (``qubits.q1.z.opx_output`` -> ``#/wiring/...`` ->
    ``ports.analog_outputs.con1.5.1``). None when it does not resolve."""
    if _depth > 8:
        return None
    node: Any = doc
    segs = [s for s in dot_path.split(".") if s]
    for i, seg in enumerate(segs):
        if is_pointer(node):
            tgt = _pointer_target(".".join(segs[:i]), node)
            return (None if tgt is None
                    else _land(doc, ".".join([tgt, *segs[i:]]), _depth + 1))
        if isinstance(node, dict) and seg in node:
            node = node[seg]
        elif isinstance(node, dict) and seg.isdigit() and int(seg) in node:
            node = node[int(seg)]
        elif isinstance(node, list) and seg.isdigit() and int(seg) < len(node):
            node = node[int(seg)]
        else:
            return None
    if is_pointer(node):
        tgt = _pointer_target(".".join(segs), node)
        return None if tgt is None else _land(doc, tgt, _depth + 1)
    return ".".join(segs)


def _port_owners(state: dict, wiring: dict | None) -> dict[str, set[str]]:
    """``{port entity path: {owner path}}`` -- every pointer in the STATE,
    outside ``ports`` / ``wiring``, whose chain lands on or inside a port
    entity (a pointer at a whole slot or container owns nothing)."""
    ents = _port_entities(state.get("ports"))
    if not ents:
        return {}
    doc = _merged_doc(state, wiring)
    out: dict[str, set[str]] = {}
    for src, kind, val in _walk_refs(state):
        if kind != "pointer" or src.split(".", 1)[0] in ("ports", "wiring"):
            continue
        tgt = _pointer_target(src, val)
        land = _land(doc, tgt) if tgt is not None else None
        if land is None:
            continue
        ent = next((p for p in ents if land == p or land.startswith(p + ".")),
                   None)
        if ent is not None:
            out.setdefault(ent, set()).add(src)
    return out


def _owner_label(owners: Any) -> str:
    """``qubits.q1.z.opx_output`` -> ``q1.z`` (the line, as a reader calls it);
    several lines -> the first plus a count."""
    labels = set()
    for src in owners:
        segs = src.split(".")
        if segs and segs[0] in ("qubits", "qubit_pairs", "twpas"):
            segs = segs[1:]
        labels.add(".".join(segs[:-1]) or src)
    ordered = sorted(labels, key=natural_key)
    if not ordered:
        return ""
    return ordered[0] + (f" (+{len(ordered) - 1})" if len(ordered) > 1 else "")


def _port_moves(old_state: dict, new_state: dict,
                old_wiring: dict | None = None,
                new_wiring: dict | None = None,
                inherited: dict[str, tuple[str, str]] | None = None
                ) -> tuple[dict[str, tuple[str, str]], dict[str, tuple[str, str]]]:
    """Match calibration by surviving endpoint identity.

    A destination with one source follows it. A destination with several
    sources is handled by field consensus. If only new endpoints occupy a
    formerly owned port, defaults stand, even when the former owner was
    removed. Renames must be applied to source identities before this pass.
    ``inherited`` remains an accepted argument for existing internal callers.
    """
    old_owners = _port_owners(old_state, old_wiring)
    if not old_owners:
        return {}, {}
    new_owners = _port_owners(new_state, new_wiring)
    old_port_of = {o: p for p, os_ in old_owners.items() for o in os_}
    old_ents = _port_entities(old_state.get("ports"))
    moves: dict[str, tuple[str, str]] = {}
    for n, owners in new_owners.items():
        shared = [o for o in owners if o in old_port_of]
        known = {old_port_of[o] for o in shared}
        if len(known) != 1:
            continue
        (o_port,) = known
        if o_port != n and o_port in old_ents:
            moves[n] = (o_port, _owner_label(shared))
    fresh: dict[str, tuple[str, str]] = {}
    for n, owners in new_owners.items():
        if n in moves or n not in old_owners:
            continue
        if any(o in old_port_of for o in owners):
            continue
        fresh[n] = (_owner_label(old_owners.get(n) or ()), _owner_label(owners))
    return moves, fresh


def _ports_view(old_state: dict, new_state: dict,
                moves: dict[str, tuple[str, str]],
                fresh: dict[str, tuple[str, str]]) -> dict:
    """``old_state`` as the merge should see it: each moved-to port holds the
    OLD values of the port its line came from, with the rebuilt port's
    identity (controller / FEM / port id, class) so port 6 is never written
    back as port 1; a ``fresh`` port holds nothing, so the rebuild's values
    stand. Only ``ports`` is copied -- the rest is shared, and the residual
    accounting keeps reading the REAL old state."""
    view = dict(old_state)
    ports = copy.deepcopy(old_state.get("ports") or {})
    view["ports"] = ports
    for n, (o, _) in moves.items():
        val: Any = old_state
        for seg in o.split("."):
            val = val.get(seg) if isinstance(val, dict) else None
        new_ent: Any = new_state
        for seg in n.split("."):
            new_ent = new_ent.get(seg) if isinstance(new_ent, dict) else None
        if not (isinstance(val, dict) and isinstance(new_ent, dict)):
            continue
        val = copy.deepcopy(val)
        for k in _PORT_IDENTITY_KEYS:
            if k in new_ent:
                val[k] = copy.deepcopy(new_ent[k])
        segs = n.split(".")[1:]                      # below `ports`
        node = ports
        for seg in segs[:-1]:
            if not isinstance(node.get(seg), dict):
                node[seg] = {}
            node = node[seg]
        node[segs[-1]] = val
    for n in fresh:
        _del_path(view, n)
    return view


def _port_sources(old, new, ow, nw):
    """Destination -> source ports -> surviving endpoint owners."""
    old_of = {o: p for p, owners in _port_owners(old, ow).items() for o in owners}
    return {p: {src: {o for o in owners if old_of.get(o) == src}
                for src in sorted({old_of[o] for o in owners if o in old_of}, key=natural_key)}
            for p, owners in _port_owners(new, nw).items()}


def _set_path(root, path, value):
    parts = path.split(".")
    node = root
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = copy.deepcopy(value)


def _port_calibration_value(doc, path, seen=()):
    """Resolve a port value in its original document, including object aliases."""
    landed = _land(doc, path)
    if landed is None or landed in seen:
        return _MISSING
    value = _path_value(doc, landed)
    if isinstance(value, dict):
        resolved = {k: _port_calibration_value(doc, f"{landed}.{k}", (*seen, landed)) for k in value}
        return _MISSING if any(v is _MISSING for v in resolved.values()) else resolved
    if isinstance(value, list):
        resolved = [_port_calibration_value(doc, f"{landed}.{i}", (*seen, landed)) for i in range(len(value))]
        return _MISSING if any(v is _MISSING for v in resolved) else resolved
    return value


def _port_consensus(view, old, sources, stats, wiring=None):
    doc = _merged_doc(old, wiring)
    for dest, srcs in sources.items():
        if len(srcs) < 2:
            continue
        entries = {src: _path_value(old, src) for src in srcs}
        agreed = {}
        for key in sorted(set().union(*(entry.keys() for entry in entries.values())) - set(_PORT_IDENTITY_KEYS), key=natural_key):
            vals = {src: _port_calibration_value(doc, f"{src}.{key}") if key in entry else _MISSING
                    for src, entry in entries.items()}
            first = next(iter(vals.values()))
            if first is not _MISSING and all(v == first for v in vals.values()):
                agreed[key] = copy.deepcopy(first)
                if any(is_pointer(entry.get(key)) for entry in entries.values()):
                    stats.pointers_reaimed.append({"path": dest + "." + key,
                        "reason": "agreed source values inlined"})
            else:
                stats.ports_conflict.append({"port": dest, "field": key,
                    "values": {_owner_label(srcs[src]): (v if v is not _MISSING else "<absent or unresolved>")
                               for src, v in vals.items()}})
        _set_path(view, dest, agreed)


def _finish_port_values(old, new, merged, ow, nw, sources, stats, protect):
    """Repair references using original context, after simultaneous value carry."""
    old_doc = _merged_doc(old, ow)
    old_entities = _port_entities(old.get("ports"))
    destinations = {}
    for dest, srcs in sources.items():
        for src in srcs:
            destinations.setdefault(src, set()).add(dest)
    pending = []
    for dest, srcs in sources.items():
        port = _path_value(merged, dest)
        if not isinstance(port, dict):
            continue
        for src, owners in srcs.items():
            for key in _path_value(old, src):
                if key not in port and key not in _PORT_IDENTITY_KEYS:
                    stats.port_fields_dropped.append({"port": dest, "field": key,
                        "owner": _owner_label(owners)})
        if len(srcs) != 1:
            continue
        src = next(iter(srcs))
        original = _path_value(old, src)
        for suffix, val in _iter_leaves(original):
            path = dest + "." + suffix
            if not is_pointer(val) or suffix.startswith("crosstalk") or path in (protect or ()):
                continue
            if _path_value(merged, path) is None:
                continue
            original_path = src + "." + suffix
            target = _pointer_target(original_path, val)
            landed = _land(old_doc, original_path)
            if landed is None:
                rebuilt_value = _path_value(new, path)
                if rebuilt_value is not None:
                    _set_path(merged, path, rebuilt_value)
                stats.pointers_reaimed.append({"path": path, "reason": "source target unresolved"})
                continue
            value = _path_value(old_doc, landed)
            target_port = next((p for p in old_entities if target and
                                (target == p or target.startswith(p + "."))), None)
            if target_port:
                choices = destinations.get(target_port, set())
                if len(choices) == 1:
                    target = next(iter(choices)) + target[len(target_port):]
                else:
                    target = None
            pending.append((path, target, value, val))
    # Inline first, so cycles introduced by swaps never affect validation.
    for path, target, value, val in pending:
        _set_path(merged, path, value)
    for path, target, value, val in pending:
        doc = _merged_doc(merged, nw)
        landed = _land(doc, target) if target else None
        aim = "#/" + target.replace(".", "/") if target else None
        if landed and landed != path and _path_value(doc, landed) == value:
            _set_path(merged, path, aim)
        else:
            aim = None
        if aim != val:
            stats.pointers_reaimed.append({"path": path, "from": val,
                "to": aim, "reason": "line target moved" if aim else "old value inlined"})


def _remap_upconverters(old, new, merged, sources, stats, protect, wiring=None):
    old_doc = _merged_doc(old, wiring)
    for dest, srcs in sources.items():
        port = _path_value(merged, dest)
        built = _path_value(new, dest)
        lines = []
        for src, owners in srcs.items():
            original = _path_value(old, src)
            table = original.get("upconverters")
            if not isinstance(table, dict):
                continue
            for owner in sorted(owners):
                line = owner.rsplit(".", 1)[0]
                channel = _path_value(old, line)
                if not isinstance(channel, dict) or "upconverter" not in channel:
                    continue
                if src == dest and "upconverters" in built:
                    continue
                index = channel["upconverter"]
                frequency = _port_calibration_value(old_doc, f"{src}.upconverters.{index}.frequency")
                lines.append((line + ".upconverter", index, frequency))
        if not lines:
            continue
        single = "upconverter_frequency" in built
        if single:
            port.pop("upconverters", None)
        else:
            port["upconverters"] = copy.deepcopy(built.get("upconverters", {}))
        frequencies = [frequency for _, _, frequency in lines]
        conflict = single and any(f != frequencies[0] for f in frequencies)
        if conflict:
            port["upconverter_frequency"] = built["upconverter_frequency"]
            stats.ports_conflict.append({"port": dest, "field": "upconverter_frequency",
                "values": {path.rsplit(".", 1)[0]: (f if f is not _MISSING else "<unresolved>")
                           for path, _, f in lines}})
        for path, previous, frequency in lines:
            if path in (protect or ()):
                continue
            matches = [k for k, v in built.get("upconverters", {}).items()
                       if v.get("frequency") == frequency]
            if frequency is _MISSING or conflict or (not single and len(matches) != 1):
                rebuilt_index = _path_value(new, path)
                if rebuilt_index is not None:
                    _set_path(merged, path, rebuilt_index)
                stats.upconverter_remapped.append({"path": path,
                    "reason": "no unique matching frequency",
                    "frequency": frequency if frequency is not _MISSING else "<unresolved>"})
                continue
            index = 1 if single else int(matches[0])
            if single and dest + ".upconverter_frequency" not in (protect or ()):
                port["upconverter_frequency"] = frequency
            _set_path(merged, path, index)
            stats.upconverter_remapped.append({"path": path, "from": previous,
                "to": index, "frequency": frequency})


def _check_mw_bands(merged, stats):
    ents = _port_entities(merged.get("ports"))
    for path, port in ents.items():
        if not path.startswith("ports.mw_outputs."):
            continue
        prefix, number = path.rsplit(".", 1)
        peer = {"1": ("mw_inputs", "1"), "2": ("mw_outputs", "3"),
                "4": ("mw_outputs", "5"), "6": ("mw_outputs", "7"),
                "8": ("mw_inputs", "2")}.get(number)
        if peer is None:
            continue
        other = prefix.replace("ports.mw_outputs.", "ports." + peer[0] + ".") + "." + peer[1]
        bands = [port.get("band"), ents.get(other, {}).get("band")]
        if None not in bands and bands[0] != bands[1] and set(bands) != {1, 3}:
            stats.mw_band_conflicts.append({"ports": [path, other], "bands": bands})


def _carry_crosstalk(old_state: dict, merged: dict,
                     moves: dict[str, tuple[str, str]],
                     fresh: dict[str, tuple[str, str]], stats: MergeStats,
                     old_wiring: dict | None, new_wiring: dict | None,
                     rebuilt: dict | None = None) -> None:
    """Carry whole calibrated rows, renumbering columns simultaneously.

    A coefficient names another output in the same controller/FEM. Port rows
    already followed their owners in _ports_view; their columns must follow
    the same permutation, including explicit self terms. Resolve source pointers
    in their original context, including dictionaries held by custom objects.
    Inline a row if its old pointer can no longer express the remapped value;
    never mutate a shared pointer target on behalf of one hardware port.
    """
    old_doc = _merged_doc(old_state, old_wiring)
    new_doc = _merged_doc(merged, new_wiring)
    old_owners = _port_owners(old_state, old_wiring)
    new_owners = _port_owners(merged, new_wiring)
    new_port_of = {o: p for p, owners in new_owners.items() for o in owners}
    sources = _port_sources(old_state, merged, old_wiring, new_wiring)

    def carry_row(dest, src):
        source_path = src + ".crosstalk"
        landed = _land(old_doc, source_path)
        row = _path_value(old_doc, landed) if landed else None
        if not isinstance(row, dict):
            return None
        scope = src.rsplit(".", 1)[0]
        dest_scope = dest.rsplit(".", 1)[0]
        remapped = {}
        for key, value in row.items():
            column = f"{scope}.{key}"
            owners = old_owners.get(column, set())
            targets = {new_port_of[o] for o in owners if o in new_port_of}
            original_path = f"{src}.crosstalk.{key}"
            reason = None
            if column == src:
                target = dest  # Explicit self terms stay self, including a split.
            elif len(targets) == 1:
                target, = targets
                if target.rsplit(".", 1)[0] != dest_scope:
                    reason = "lines on different FEMs"
            elif targets:
                reason = "column port now drives another line"
                target = column
            else:
                target = f"{dest_scope}.{key}"
                current = new_owners.get(target, set())
                if current and current != owners:
                    reason = "column port now drives another line"
                else:
                    stats.crosstalk_unresolved.append(f"{dest}.crosstalk.{key}")
            if reason:
                stats.crosstalk_dropped.append({"path": original_path, "reason": reason})
                continue
            number = target.rsplit(".", 1)[1]
            renamed = int(number) if isinstance(key, int) else number
            target_path = f"{dest}.crosstalk.{renamed}"
            if renamed in remapped:
                stats.crosstalk_dropped.append({"path": original_path,
                    "reason": "column port now drives another line"})
                continue
            remapped[renamed] = copy.deepcopy(value)
            if str(key) != str(renamed):
                stats.crosstalk_remapped.append((original_path, target_path))
        return remapped

    for dest, port in _port_entities(merged.get("ports")).items():
        if not dest.startswith("ports.analog_outputs.") or dest in fresh:
            continue
        srcs = sources.get(dest) or {moves.get(dest, (dest, ""))[0]: set()}
        remap_start = len(stats.crosstalk_remapped)
        unresolved_start = len(stats.crosstalk_unresolved)
        rows = {src: carry_row(dest, src) for src in srcs}
        remapped = next(iter(rows.values()))
        if len(rows) > 1 and any(row != remapped for row in rows.values()):
            del stats.crosstalk_remapped[remap_start:]
            del stats.crosstalk_unresolved[unresolved_start:]
            # Compare line relations after remapping, never raw port-number keys.
            baseline = _path_value(rebuilt or {}, dest + ".crosstalk")
            port["crosstalk"] = copy.deepcopy(baseline if baseline is not None else {})
            stats.ports_conflict.append({"port": dest, "field": "crosstalk",
                "values": {_owner_label(srcs[src]): row for src, row in rows.items()}})
            continue
        if remapped is None:
            continue
        src = next(iter(srcs))
        source_path = src + ".crosstalk"
        # Keep an existing reference only if it still denotes the exact row.
        current = port.get("crosstalk")
        current_path = dest + ".crosstalk"
        target = _land(new_doc, current_path) if is_pointer(current) else None
        # A port target can itself change later in this simultaneous carry.
        if (target and not (moves and target.startswith("ports."))
                and _path_value(new_doc, target) == remapped):
            continue
        if is_pointer(current) or is_pointer(_path_value(old_doc, source_path)):
            stats.pointers_inlined.append(current_path)
        port["crosstalk"] = remapped
    stats.crosstalk_remapped.sort(key=lambda pair: natural_key(pair[0]))
    stats.crosstalk_unresolved.sort(key=natural_key)


def _node_is_dict(root: dict, dot_path: str) -> bool:
    node: Any = root
    for seg in dot_path.split("."):
        if not isinstance(node, dict) or seg not in node:
            return False
        node = node[seg]
    return isinstance(node, dict)


# Collections whose entities another object may LIST by reference (a TWPA's
# ``qubits`` is the one such list on real chips: 360 of 400 real states).
_LISTED_ENTITY_COLLECTIONS = ("qubits", "qubit_pairs")


def _prune_removed_entity_refs(merged: dict, old_state: dict) -> list[str]:
    """Drop, from every LIST leaf of ``merged``, each absolute reference to a
    qubit / qubit pair the rebuild removed; return one report line per drop.

    Lists are merge leaves (tier-1 carries them whole, tier-2 grafts them
    whole), and ``is_pointer`` only knows a STRING -- so a list of pointers
    slipped past both the "pointer keeps NEW" rule and the dangling scan: a
    TWPA kept ``'#/qubits/q5'`` after q5 was removed, QUAM warned "Could not
    resolve reference" and handed back the raw string. Only a reference that
    resolved in the SOURCE and no longer resolves is dropped (the rebuild
    removed its entity); every other element, and a pre-existing broken one,
    is left exactly as it was. Mutates ``merged`` in place.
    """
    dropped: list[str] = []

    def removed(x: Any) -> bool:
        if not (isinstance(x, str) and x.startswith("#/")):
            return False
        if x[2:].split("/", 1)[0] not in _LISTED_ENTITY_COLLECTIONS:
            return False
        return _resolves(old_state, x) and not _resolves(merged, x)

    def walk(node: Any, prefix: str) -> None:
        if not isinstance(node, dict):
            return
        for k, v in node.items():
            p = f"{prefix}.{k}" if prefix else k
            if isinstance(v, list):
                gone = [x for x in v if removed(x)]
                if gone:
                    node[k] = [x for x in v if not removed(x)]
                    dropped.extend(
                        f"{p} -> {x} (removed {x[2:].split('/', 1)[0].rstrip('s')};"
                        " reference dropped)" for x in gone)
            else:
                walk(v, p)

    walk(merged, "")
    return dropped


def _prune_removed_entries(obj: dict, sub: str, merged: dict, new_wiring: dict | None,
                           removed_ids: set[str], stats: MergeStats) -> bool:
    """Drop entries keyed by, or pointing at, a removed entity even if all their
    values are literal or their pointers still land. False (without mutation)
    when a missing target outside those entries requires dropping the object."""
    def lands(v: str) -> bool:
        wiring_side = v[2:].split("/", 1)[0] in ("wiring", "network")
        if wiring_side and new_wiring is None:
            return True
        return _resolves(new_wiring if wiring_side else merged, v)

    cut: set[tuple] = set()

    def keyed(node: Any, path: tuple) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k in removed_ids:
                    cut.add(path + (k,))
                else:
                    keyed(v, path + (k,))

    keyed(obj, ())
    pointers = []
    for p, v in _iter_leaves(obj):
        if not (isinstance(v, str) and v.startswith("#/")):
            continue
        path = tuple(p.split("."))
        pointers.append((path, v))
        if removed_ids.intersection(v[2:].split("/")) and len(path) > 1:
            cut.add(path[:-1])
    for path, v in pointers:
        if not lands(v) and not any(path[:len(c)] == c for c in cut):
            return False
    for path in sorted(cut, key=len):
        if any(path[:len(c)] == c for c in cut if len(c) < len(path)):
            continue
        node = obj
        for k in path[:-1]:
            node = node[k]
        gone = node.pop(path[-1])
        stats.grafted -= _count_leaves(gone)    # its leaves are reported as not carried
    return True


def _ungraft_unlanded(merged: dict, stats: MergeStats,
                      new_wiring: dict | None, removed_ids: set[str] | None = None) -> None:
    """Put the NEW null back where an OLD typed object grafted over it points
    at something the rebuild does not have (QA review of regenerate-r2-14).

    The class gate only catches a class the rebuild writes NOWHERE. A rebuild
    that drops one qubit's flux line while the others keep theirs still
    writes FluxLine, so the old line was grafted back pointing at wiring the
    rebuild never made -- reported as dangling, shipped anyway, and
    ``generate_config()`` crashed with the build reporting success. The
    rebuild removed it: an absolute pointer inside it that does not land in
    the rebuilt chip means the NEW null stands and the object is reported in
    ``stats.rebuild_removed``. A ``#/wiring`` / ``#/network`` pointer is
    judged only when the new wiring is known, and ``twpas`` is left alone
    (:func:`graft_twpa_wiring` carries its wiring after the merge). A graft
    with every pointer landing is kept: a user-added object on a field the
    builder leaves empty. Mutates ``merged`` and ``stats``.
    """
    for sub in stats.deferred_grafted:
        segs = sub.split(".")
        if segs[0] == "twpas":
            continue
        parent: Any = merged
        for s in segs[:-1]:
            parent = parent.get(s) if isinstance(parent, dict) else None
        obj = parent.get(segs[-1]) if isinstance(parent, dict) else None
        if not isinstance(obj, dict):
            continue
        if removed_ids and _prune_removed_entries(obj, sub, merged, new_wiring,
                                                   removed_ids, stats):
            continue
        miss = None
        for _, v in _iter_leaves(obj):
            if not (isinstance(v, str) and v.startswith("#/")):
                continue
            wiring_side = v[2:].split("/", 1)[0] in ("wiring", "network")
            if wiring_side and new_wiring is None:
                continue
            if not _resolves(new_wiring if wiring_side else merged, v):
                miss = v
                break
        if miss is None:
            continue
        parent[segs[-1]] = None
        n = _count_leaves(obj)
        stats.grafted -= n
        stats.graft_subtrees = [g for g in stats.graft_subtrees if g != (sub, n)]
        cls = str(obj.get("__class__") or "object").rsplit(".", 1)[-1]
        stats.rebuild_removed.append(
            (sub, f"old {cls} not put back — {miss} is not in the rebuild"))


# ---------------------------------------------------------------------------
# A qubit renamed on the wizard is the same qubit under a new id
# ---------------------------------------------------------------------------

# A removed source qubit / pair whose id a rename now uses is held under this
# suffix, so its values stay reported as not carried instead of landing on the
# renamed one -- and an operation named after it ("cz_flattop_pulse_q1") cannot
# take the name of the renamed qubit's own.
_DISPLACED_SUFFIX = "_removed"


def source_renames(qubit_sources: Any, old_state: dict,
                   new_ids: Any = None) -> dict[str, str]:
    """``{source id: new id}`` for each qubit the wizard renamed.

    ``qubit_sources`` is the wizard's ``{current id: source id}`` record: the
    rename and naming-scheme actions carry each source qubit's row to its new
    id, so the row, not the name, is the qubit. Only a real rename is kept: the
    source id must be on the source chip, the new id in ``new_ids`` (the build's
    qubits) when given, and no source may be claimed twice -- a claim that
    cannot be checked is dropped, which leaves that qubit matched by id exactly
    as before this record existed.
    """
    if not isinstance(qubit_sources, dict):
        return {}
    old_q = old_state.get("qubits") if isinstance(old_state, dict) else None
    if not isinstance(old_q, dict):
        return {}
    allowed = set(new_ids) if new_ids is not None else None
    claims: dict[str, list[str]] = {}
    for cur, src in qubit_sources.items():
        if isinstance(cur, str) and isinstance(src, str):
            claims.setdefault(src, []).append(cur)
    out: dict[str, str] = {}
    for src, curs in claims.items():
        if len(curs) != 1 or src not in old_q:
            continue
        cur = curs[0]
        if cur == src or (allowed is not None and cur not in allowed):
            continue
        out[src] = cur
    return out


# An old pair whose id no longer names it (a member renamed, no rebuilt twin:
# removed, or rebuilt reversed) is held under this; the loss / reversed-pair
# report reads "q1-q2_source". No space: the report reads a line's owner from
# the dot-path before the first space.
_SOURCE_SUFFIX = "_source"


def _source_label(oid: str, taken: set) -> str:
    label = oid + _SOURCE_SUFFIX
    while label in taken:
        label += "_"
    taken.add(label)
    return label


def _stale_label(tid: str, taken: set) -> str:
    label = tid + "_stale"
    while label in taken:
        label += "_"
    taken.add(label)
    return label


def _displaced_label(oid: str, taken: set) -> str:
    label = oid + _DISPLACED_SUFFIX
    while label in taken:
        label += "_"
    taken.add(label)
    return label


def token_pattern(ids) -> re.Pattern | None:
    """THE match rule for qubit ids inside a name: each ``_``-delimited
    occurrence of one of ``ids``, longest first (``q1_b`` is matched as
    itself, never as ``q1`` followed by ``_b``). Shared by the rebuild's
    rename and the history lineage (docs/296)."""
    every = sorted({i for i in ids if isinstance(i, str) and i}, key=len, reverse=True)
    if not every:
        return None
    alt = "|".join(re.escape(k) for k in every)
    return re.compile(rf"(?<![^_])(?:{alt})(?![^_])")


def _token_renamer(tok: dict[str, str], ids=()):
    """``f(text)`` renaming each ``_``-delimited occurrence of a source qubit id
    -- ``cz_SNZ_flux_pulse_q2_q1`` -> ``cz_SNZ_flux_pulse_q1_q0`` -- all at once
    (a swap stays a swap). Labs and the builder both name per-partner
    operations this way; an id that is only part of a word is left alone.

    ``ids`` -- every source qubit id. They are all in the pattern, longest
    first, so an id that contains another (``q1_b`` beside ``q1``, which the
    name rule allows) is matched as itself and kept, never read as ``q1``
    followed by ``_b``."""
    if not tok:
        return lambda text: text
    pat = token_pattern(set(tok) | set(ids))
    return lambda text: pat.sub(lambda m: tok.get(m.group(0), m.group(0)), text)


def _renamed_pointer(s: str, pmap: dict, rename) -> str:
    """A pointer with the renamed ids swapped in: the pair segment after
    ``qubit_pairs`` by ``pmap``, every other segment by its qubit-id tokens --
    except below ``extras``, which is free-form and kept verbatim."""
    head, _, rest = s.partition("/")
    if not rest:
        return s
    segs = rest.split("/")
    out, free = [], False
    for i, seg in enumerate(segs):
        if free:
            out.append(seg)
            continue
        prev = segs[i - 1] if i else None
        out.append(pmap.get(seg, seg) if prev == "qubit_pairs" else rename(seg))
        free = seg == "extras"
    return head + "/" + "/".join(out)


def _rewrite_ids(node: Any, pmap: dict, rename, parent: str | None = None,
                 free: bool = False) -> Any:
    """``node`` re-expressed in the new ids by one rule for keys and string
    values alike: a pair id is swapped whole, everything else has its qubit-id
    tokens renamed. A value names a qubit or an operation as often as a key
    does -- ``id``, a channel's ``thread``, a TWPA's ``qubits``, and the
    operation a lab's CZ macro plays (``pulse.id = "cz_unipolar_pulse_q1"``,
    which must follow the operation key it names). Pointers follow
    :func:`_renamed_pointer` wherever they sit -- one kept under ``extras``
    still points into the renamed chip -- while other ``extras`` keys and
    values are free-form and kept verbatim."""
    if isinstance(node, dict):
        if free:
            new_keys = {k: k for k in node}
        elif parent == "qubit_pairs":
            new_keys = {k: pmap.get(k, k) for k in node}
        else:
            new_keys = {k: rename(k) if isinstance(k, str) else k for k in node}
        # The token map is one-to-one (renamed, removed-and-reused and stale
        # ids each get their own name), so two keys cannot meet; should one
        # anyway, they are never merged into one.
        out: dict = {}
        for k, v in node.items():
            nk = new_keys[k]
            while nk in out:
                nk += "_"
            out[nk] = _rewrite_ids(v, pmap, rename, k, free or k == "extras")
        return out
    if isinstance(node, list):
        return [_rewrite_ids(v, pmap, rename, parent, free) for v in node]
    if isinstance(node, str):
        if node.startswith("#"):
            return _renamed_pointer(node, pmap, rename)
        if not free:
            return pmap[node] if node in pmap else rename(node)
    return node


def rebuilt_pairs(new_state: dict | None, new_wiring: dict | None) -> dict[str, tuple[str, str]]:
    """``{pair id: (control, target)}`` of a rebuilt chip -- what
    :func:`rename_source_qubits` matches a renamed pair against."""
    out: dict[str, tuple[str, str]] = {}
    if not isinstance(new_state, dict):
        return out
    new_doc = _merged_doc(new_state, new_wiring)
    for nid, npair in (new_state.get("qubit_pairs") or {}).items():
        m = _pair_membership(npair, new_doc)
        if m is not None:
            out[nid] = m
    return out


def rename_source_qubits(old_state: dict, old_wiring: dict | None,
                         renames: dict[str, str],
                         new_state: dict | None = None,
                         new_wiring: dict | None = None, *,
                         new_ids: Any = None,
                         new_pairs: dict | None = None):
    """The SOURCE chip re-expressed in the rebuilt chip's qubit ids.

    The merge matches by path, so before this a wizard rename moved calibration
    by NAME: renaming q1, q2 to q0, q1 carried the old q1's values onto the new
    q1 -- the qubit that used to be q2 -- and reported the old q2's as lost.
    Renaming the source first makes every later step (tier-1 carry, port
    moves, populate protection, the loss report) see one qubit under one id.

    What is renamed: the qubit keys (state and wiring), every pointer, every
    string that is a qubit id (``id``, a channel's ``thread``, a TWPA's
    ``qubits``), and the qubit ids inside keys -- ``cz_SNZ_flux_pulse_q2_q1``
    becomes ``cz_SNZ_flux_pulse_q1_q0``, the name a fresh build gives the
    renamed pair's pulse. ``extras`` is free-form and kept verbatim.

    ``renames`` is :func:`source_renames`' ``{source id: new id}``. A source
    qubit the user removed whose id a rename now uses is held as
    ``"<id>_removed"`` so its values stay reported as not carried. With
    ``new_state`` given, a pair with a renamed member takes the id the rebuild
    gave the pair with the same (control, target); without it (no build yet)
    pair ids stay as they are. ``new_ids`` / ``new_pairs`` (the rebuilt
    chip's qubit ids and :func:`rebuilt_pairs`) stand in for ``new_state``
    when only its record is at hand: the history lineage (docs/296) re-expresses
    a state saved before a rename with the same rule the rebuild used.

    Returns ``(state, wiring, qubits_renamed, pairs_renamed)``, the two lists
    as ``[(old id, new id)]``. Pure: the inputs are not modified, and with no
    renames they come back as they are.
    """
    plan = rename_plan(old_state, old_wiring, renames, new_state, new_wiring,
                       new_ids=new_ids, new_pairs=new_pairs)
    if plan is None:
        return old_state, old_wiring, [], []
    tok, pmap, old_q = plan
    state, wiring = apply_rename_plan(old_state, old_wiring, tok, pmap, old_q)
    qubits = sorted(renames.items(), key=lambda t: natural_key(t[0]))
    pairs = sorted(((o, n) for o, n in pmap.items()
                    if not n.endswith(_SOURCE_SUFFIX)),
                   key=lambda t: natural_key(t[0]))
    return state, wiring, qubits, pairs


def rename_plan(old_state: dict, old_wiring: dict | None, renames: dict[str, str],
                new_state: dict | None = None, new_wiring: dict | None = None, *,
                new_ids: Any = None, new_pairs: dict | None = None):
    """``(token map, pair map, source ids)`` :func:`rename_source_qubits`
    applies (None when nothing is renamed) -- the rename record of a rebuild
    (docs/296) keeps the maps, so the history reads the same rule."""
    old_q = old_state.get("qubits") if isinstance(old_state, dict) else None
    if not renames or not isinstance(old_q, dict):
        return None
    tok = dict(renames)
    taken = set(old_q) | set(tok.values())
    if isinstance(new_state, dict):
        taken |= set(new_state.get("qubits") or {})
        new_pairs = rebuilt_pairs(new_state, new_wiring)
    elif new_ids is not None:
        taken |= set(new_ids)
    for o in old_q:
        if o not in tok and o in set(renames.values()):
            tok[o] = _displaced_label(o, taken)
    # A new id the SOURCE never had, spelled inside one of its names (a
    # leftover "cz_pulse_q0" on a chip with no q0, when q1 becomes q0), names
    # no qubit of this chip: it is held as "<id>_stale" in keys, pointers and
    # strings alike, so nothing renamed lands on it and nothing that used it
    # is pointed at the renamed one.
    for t in set(renames.values()):
        if t not in old_q and t not in tok:
            tok[t] = _stale_label(t, taken)

    pmap: dict[str, str] = {}
    old_pairs = old_state.get("qubit_pairs")
    if isinstance(old_pairs, dict) and old_pairs and new_pairs is not None:
        old_doc = _merged_doc(old_state, old_wiring)
        new_by_mem: dict[tuple[str, str], str] = {}
        for nid, m in new_pairs.items():
            new_by_mem.setdefault(tuple(m), nid)
        mem = {}
        for oid, opair in old_pairs.items():
            m = _pair_membership(opair, old_doc)
            if m is not None:
                mem[oid] = (tok.get(m[0], m[0]), tok.get(m[1], m[1]))
        # A pair with a renamed (or removed-and-reused) member is the rebuilt
        # pair with the same (control, target), or no pair at all: its old id
        # now names a different pair (or none), so it is never kept as is --
        # a NEW pair the user added under that id would inherit it.
        moved = set(tok.values())
        pall = set(old_pairs) | set(new_pairs)
        for oid, m in mem.items():
            if not (m[0] in moved or m[1] in moved):
                continue
            nid = new_by_mem.get(m)
            if nid is not None and nid not in pmap.values():
                if nid != oid:
                    pmap[oid] = nid
            else:
                pmap[oid] = _source_label(oid, pall)
        ptaken = set(pmap.values())
        for oid in old_pairs:                  # an untouched pair whose id a rename took
            if oid in pmap or oid not in ptaken:
                continue
            nid = new_by_mem.get(mem.get(oid))
            pmap[oid] = (nid if nid is not None and nid not in ptaken
                         else _source_label(oid, pall))
            ptaken.add(pmap[oid])

    return tok, pmap, list(old_q)


def apply_rename_plan(state: dict, wiring: dict | None, tok: dict, pmap: dict, ids):
    """``(state, wiring)`` with the token map ``tok`` and the pair map
    ``pmap`` applied by :func:`_rewrite_ids` -- pure."""
    rename = _token_renamer(tok, ids)
    out = _rewrite_ids(state, pmap, rename)
    return out, (_rewrite_ids(wiring, pmap, rename) if isinstance(wiring, dict) else wiring)


class _Cycle(Exception):
    pass


def _hop(doc: dict, pointer: str, field_path: tuple) -> tuple[Any, tuple]:
    """``_one_hop`` that also returns where it landed (the target's own path)."""
    if pointer.startswith("#/"):
        parts = [p for p in pointer[2:].split("/") if p]
    else:
        parts = list(field_path[:-1])
        for seg in pointer[1:].split("/"):
            if seg == "..":
                parts = parts[:-1]
            elif seg and seg != ".":
                parts.append(seg)
    node: Any = doc
    for p in parts:
        if isinstance(node, dict) and p in node:
            node = node[p]
        elif isinstance(node, list) and p.isdigit() and int(p) < len(node):
            node = node[int(p)]
        else:
            return _MISSING, tuple(parts)
    return node, tuple(parts)


def _resolved_leaves(doc: dict, top: str, path: tuple | None = None) -> dict[str, Any]:
    """Every leaf under ``doc[top]`` as the value QUAM would read: pointers followed
    (a pointer to an object expands to its leaves), an unresolvable one kept as its
    text. Raises ``_Cycle`` when a chain loops."""
    out: dict[str, Any] = {}

    def value(v: Any, path: tuple, seen: frozenset) -> Any:
        hops = 0
        while is_pointer(v):
            hops += 1
            if hops > 40:
                raise _Cycle(".".join(path))
            tgt, where = _hop(doc, v, path)
            if tgt is _MISSING:
                return ("<unresolved>", v)
            if where in seen:
                raise _Cycle(".".join(path))
            seen = seen | {where}
            v, path = tgt, where
        return v, path, seen

    def walk(node: Any, path: tuple, label: str, seen: frozenset, depth: int) -> None:
        if depth > 60:
            raise _Cycle(label)
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, path + (str(k),), f"{label}.{k}", seen, depth + 1)
            return
        if is_pointer(node):
            r = value(node, path, seen | {path})
            if isinstance(r, tuple) and len(r) == 2:
                out[label] = r
                return
            v, where, seen2 = r
            if isinstance(v, dict):
                walk(v, where, label, seen2, depth + 1)
            else:
                out[label] = v
            return
        out[label] = node

    if path is not None:
        node = doc
        for k in path:
            node = node[k]
        walk(node, path, ".".join(path), frozenset(), len(path) - 1)
    elif isinstance(doc.get(top), dict):
        walk(doc[top], (top,), top, frozenset(), 0)
    return out


def _restore_source_forms(merged: dict, old: dict, wiring: dict | None,
                          protect: set[str] | None) -> list[str]:
    """Put back the SOURCE's own form (a lab's pointer direction, an inline object
    the rebuild replaced with a link, a pointer to a different but equal field)
    wherever doing so changes no value QUAM reads and closes no cycle.

    A candidate is a place where the source and the merged state hold a different
    raw form. They are tried together first, then one by one (objects before
    pointers, so a link can land on an object put back before it); each attempt is
    kept only if every affected leaf resolves exactly as before (a ``__class__``
    is a leaf too) and no pointer chain loops. Independent subtrees need no
    revalidation; the trial order and accepted set remain unchanged."""
    cands: list[tuple[str, tuple, Any]] = []

    def walk(o: Any, m: Any, path: tuple) -> None:
        if isinstance(o, dict) and isinstance(m, dict):
            for k, ov in o.items():
                if k in m:
                    walk(ov, m[k], path + (k,))
            return
        if o == m:
            return
        if not (is_pointer(o) or is_pointer(m)):
            return
        dot = ".".join(path)
        if protect and any(dot == p or dot.startswith(p + ".") or p.startswith(dot + ".")
                           for p in protect):
            return
        cands.append((dot, path, o))

    walk(old, merged, ())
    if not cands:
        return []
    cands.sort(key=lambda c: (is_pointer(c[2]), c[0]))

    # Partition the document into small, stable subtrees. A candidate is always
    # wholly inside one partition (stop early at a candidate that changes shape).
    # Connect partitions using pointers from BOTH possible forms. This conservative
    # graph includes chains, object references, missing targets and new cycles;
    # no attempted substitution can affect a disconnected partition.
    boundaries = {path for _, path, _ in cands}
    doc = _merged_doc(merged, wiring)
    units: dict[tuple, int] = {}

    def partition(node: Any, path: tuple) -> None:
        if path and (len(path) >= 4 or path in boundaries or not isinstance(node, dict)):
            units[path] = len(units)
        elif isinstance(node, dict):
            for k, v in node.items():
                partition(v, path + (k,))

    partition(doc, ())
    parents = list(range(len(units)))

    def find(i: int) -> int:
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    below: dict[tuple, list[int]] = {}
    for path, i in units.items():
        for depth in range(len(path) + 1):
            below.setdefault(path[:depth], []).append(i)

    def overlaps(path: tuple) -> list[int]:
        for depth in range(1, len(path) + 1):
            if path[:depth] in units:
                return [units[path[:depth]]]
        return below.get(path, [])

    def connect(node: Any, path: tuple) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                connect(v, path + (k,))
        elif is_pointer(node):
            _, target = _hop(doc, node, path)
            linked = overlaps(path) + overlaps(target)
            if linked:
                first = find(linked[0])
                for i in linked[1:]:
                    parents[find(i)] = first

    connect(doc, ())
    connect(_merged_doc(old, wiring), ())
    components: dict[int, list[tuple]] = {}
    for path, i in units.items():
        if path[0] in merged:
            components.setdefault(find(i), []).append(path)

    def read_unit(d: dict, path: tuple) -> dict:
        # Root scalars were never resolved by the original whole-state reader.
        if len(path) == 1 and not isinstance(merged[path[0]], dict):
            return {path[0]: merged[path[0]]}
        return _resolved_leaves(d, path[0], path)

    try:
        baseline = {path: read_unit(doc, path)
                    for paths in components.values() for path in paths}
    except _Cycle:
        return []

    def doc_of(state: dict) -> dict:
        return _merged_doc(state, wiring)

    candidate_components = {path: find(overlaps(path)[0]) for _, path, _ in cands}

    def setp(root: dict, path: tuple, v: Any) -> None:
        node = root
        for k in path[:-1]:
            node = node[k]
        node[path[-1]] = copy.deepcopy(v)

    def ok(state: dict, group: list) -> bool:
        try:
            affected = {candidate_components[path] for _, path, _ in group}
            d = doc_of(state)
            return all(read_unit(d, path) == baseline[path]
                       for component in affected for path in components.get(component, []))
        except _Cycle:
            return False

    def get(root: dict, path: tuple) -> Any:
        node = root
        for k in path:
            node = node[k]
        return node

    done: list[str] = []

    def attempt(group: list) -> None:
        """Put a group back; keep it if the state reads the same, else halve it."""
        if not group:
            return
        saved = [(path, copy.deepcopy(get(merged, path))) for _, path, _ in group]
        for _, path, v in group:
            setp(merged, path, v)
        if ok(merged, group):
            done.extend(dot for dot, _, _ in group)
            return
        for path, v in reversed(saved):
            setp(merged, path, v)
        if len(group) == 1:
            return
        mid = len(group) // 2
        attempt(group[:mid])
        attempt(group[mid:])

    # all together first (forms that only work as a set, a link and the object it
    # lands on); else objects first, then pointers, each halved until it holds
    saved = copy.deepcopy(merged)
    for _, path, v in cands:
        setp(merged, path, v)
    if ok(merged, cands):
        return [c[0] for c in cands]
    merged.clear()
    merged.update(saved)
    attempt([c for c in cands if not is_pointer(c[2])])
    attempt([c for c in cands if is_pointer(c[2])])
    return done

def merge_states(old_state: dict, new_state: dict,
                 class_schemas: dict[str, list[str]] | None = None,
                 protect_paths: set[str] | None = None,
                 old_wiring: dict | None = None,
                 new_wiring: dict | None = None,
                 keep_classes: dict | None = None,
                 twpa_ids: Any = None,
                 env_fields: dict | None = None,
                 declared_fems: dict | None = None) -> MergeResult:
    """Merge the OLD calibrated state onto the NEW rebuilt structure.

    ``twpa_ids`` -- the TWPA ids the build spec asked for (QA r2-10): an OLD
    TWPA the wizard offered and the spec no longer carries is NOT grafted
    back (see :func:`_twpas_the_spec_removed`). ``None`` ⇒ graft all, as
    before.

    ``env_fields`` -- the build env's probe of the SOURCE chip's classes,
    ``{cls: [field, ...] | None}`` (QA r2-15): every tier-2 graft is gated at
    every depth by it (see :func:`_gate_graft`). ``None`` ⇒ legacy graft.

    A port's values follow the line that owns it (QA F1): see
    :func:`_port_moves`; ``stats.ports_moved`` / ``stats.ports_fresh`` name
    each one.

    ``keep_classes`` -- optional ``{class_path: {"bases": [...], "fields":
    [...]}}`` for the SOURCE chip's classes that the build env imports
    (docs/202 §15). Where the rebuild wrote a class that is in the source
    class's MRO, the source class is kept and its own fields carried; see
    :func:`_kept_class`. ``None`` ⇒ the NEW class always wins, as before.

    Returns the merged state plus :class:`MergeStats`. ``stats.residual_lost``
    lists any OLD scalar path with no home in the merged tree (should be empty
    for a same-structure rebuild; non-empty only where the user intentionally
    removed structure). ``stats.dangling_grafts`` lists grafted subtrees whose
    absolute pointers no longer resolve -- surface these as warnings.

    ``class_schemas`` — optional ``{class_path: [field, ...]}`` map of the NEW
    build env's dataclass fields (harvested by ``run_build`` inside the env,
    carried via ``_result.json``). When present, the tier-2 graft refuses to
    copy an OLD-only key into a ``__class__``-tagged dict whose class doesn't
    know that field — those are fields an older stack generation serialized
    that the new one renamed/removed, and quam's loader dies on them. Dropped
    paths land in ``stats.schema_dropped`` (never silent). ``None`` or a class
    missing from the map ⇒ legacy behavior (graft), so old build results and
    probe failures degrade safely.

    ``protect_paths`` — optional set of merged-tree dot-paths whose NEW value
    must win over tier-1 (the user's deliberate Populate-step edits, expanded
    by :mod:`regen_populate`). Protected leaves land in
    ``stats.populate_protected``; ``None`` ⇒ legacy behavior.
    """
    stats = MergeStats()
    # docs/118: the wirings are what make a TWO-hop pair reference resolvable —
    # without them the membership key falls back to the last path segment and
    # every pair id reconciliation silently misses, orphaning the pairs'
    # calibration in the merge.
    new_state = _reconcile_pair_ids(old_state, new_state,
                                    old_wiring, new_wiring)   # align pair ids first
    carry_ports = _carryable_ports(old_state, new_state, old_wiring, new_wiring, declared_fems)
    # QA F1: the OLD side the merge reads -- a port's values moved to wherever
    # its line went. QA r2-10: minus the TWPAs the user took off step 4.
    inherited: dict[str, tuple[str, str]] = {}
    moves, fresh = _port_moves(old_state, new_state, old_wiring, new_wiring,
                               inherited)
    sources = _port_sources(old_state, new_state, old_wiring, new_wiring)
    old_view = (_ports_view(old_state, new_state, moves, fresh)
                if (moves or fresh) else copy.deepcopy(old_state))
    _port_consensus(old_view, old_state, sources, stats, old_wiring)
    stats.ports_moved = sorted(((o, n, who) for n, (o, who) in moves.items()),
                               key=lambda t: natural_key(t[1]))
    stats.ports_fresh = sorted(((n, was, now) for n, (was, now) in fresh.items()),
                               key=lambda t: natural_key(t[0]))
    stats.ports_inherited = sorted(
        ((n, was, now) for n, (was, now) in inherited.items()),
        key=lambda t: natural_key(t[0]))
    removed_twpas = _twpas_the_spec_removed(old_state, new_state, old_wiring,
                                            twpa_ids)
    if removed_twpas:
        old_view = dict(old_view)
        old_view["twpas"] = {k: v for k, v in old_state["twpas"].items()
                             if k not in removed_twpas}
        # ...except a RENAME: the same device under the id the user typed.
        renamed = _twpas_the_spec_renamed(old_state, new_state, old_wiring,
                                          new_wiring, removed_twpas)
        for o, n in renamed.items():
            old_view["twpas"][n] = _renamed_twpa_view(
                old_state["twpas"][o], o, n, new_state["twpas"][n])
        stats.twpas_renamed = sorted(renamed.items(),
                                     key=lambda t: natural_key(t[0]))
        stats.twpas_removed = sorted(removed_twpas - set(renamed), key=natural_key)
    stats.pairs_reversed = _reversed_pairs(old_state, new_state,
                                           old_wiring, new_wiring)
    # v1.6.0 review: source pointers -- kept where the rebuild wrote the literal they
    # point at, inlined where a grafted one would have no literal target
    keep_ptr, inline = _pointer_decisions(old_view, _merged_doc(new_state, new_wiring))
    merged = _merge(old_view, new_state, "", stats, class_schemas, protect_paths,
                    keep_classes, carry_ports, env_fields, keep_ptr, inline)
    removed_ids = {k for c in ("qubits", "qubit_pairs")
                   for k in (old_view.get(c) or {}) if k not in (new_state.get(c) or {})}
    _ungraft_unlanded(merged, stats, new_wiring, removed_ids)
    # A removed qubit/pair is still NAMED inside a list another object carried
    # (a TWPA's `qubits`); the list survives as a leaf, so drop the reference
    # visibly here (reported with the residual loss) rather than ship it broken.
    ref_drops = _prune_removed_entity_refs(merged, old_state)
    # A lab's own pointer forms come back where they change no value (see the helper)
    stats.source_forms_restored = sorted(
        _restore_source_forms(merged, old_view, new_wiring, protect_paths), key=natural_key)
    _carry_crosstalk(old_state, merged, moves, fresh, stats, old_wiring, new_wiring, new_state)
    _finish_port_values(old_state, new_state, merged, old_wiring, new_wiring,
                        sources, stats, protect_paths)
    _remap_upconverters(old_state, new_state, merged, sources, stats, protect_paths, old_wiring)
    _check_mw_bands(merged, stats)
    stats.ports_carried.sort(key=natural_key)
    # Every one of these lists is a set of dot-paths shown to the user in
    # the build-result transparency panel, TRUNCATED to the first 80/200
    # (regenerate.py) — so the sort decides both the order and WHICH paths
    # are shown. Natural order: q2 before q10, .101 before .1009
    # (customer rule 2026-09-09).
    stats.schema_dropped.sort(key=natural_key)
    stats.populate_protected.sort(key=natural_key)
    stats.class_changed.sort(key=lambda c: natural_key(c[0]))
    stats.class_kept.sort(key=lambda c: natural_key(c[0]))

    merged_paths = {p for p, _ in _iter_leaves(merged)}
    old_scalars = [(p, v) for p, v in _iter_leaves(old_state)
                   if not is_pointer(v)
                   and not p.startswith("__package_versions__")]  # artifact, never "lost"
    moved_from = [(o, n) for o, n, _ in stats.ports_moved]
    moved_from += stats.crosstalk_remapped
    # ...and a renamed TWPA's values live under its new id.
    moved_from += [(f"twpas.{o}", f"twpas.{n}") for o, n in stats.twpas_renamed]
    for p, ov in old_scalars:
        if p in merged_paths:
            continue
        # A schema rejection belongs to the destination line, even when its
        # source hardware address disappears entirely.
        if any(p == src + "." + record["field"] or
               p.startswith(src + "." + record["field"] + ".")
               for record in stats.port_fields_dropped
               for src in sources.get(record["port"], {})):
            continue
        if any(p == record["path"] for record in stats.crosstalk_dropped):
            continue
        # QA F1: a moved port's values live at the port its line went to.
        if moved_from and any(
                (p == o or p.startswith(o + ".")) and (n + p[len(o):]) in merged_paths
                for o, n in moved_from):
            continue
        # An OLD null the rebuild replaced with real structure is not a lost
        # calibration (see _merge: NEW keeps structure over an OLD null).
        if ov is None and _node_is_dict(merged, p):
            continue
        # Schema-gate drops are already reported in stats.schema_dropped —
        # don't double-count them as residual loss (they are deliberate, not
        # "calibration with no home"). A dropped key may be a subtree root, so
        # prefix-match its leaves.
        if any(p == dp or p.startswith(dp + ".") for dp in stats.schema_dropped):
            continue
        # ...and an object the rebuild left empty is ONE line below, not its leaves
        if any(p == rp or p.startswith(rp + ".") for rp, _ in stats.rebuild_removed):
            continue
        # A path is SUPERSEDED (not lost) when the NEW structure replaced an OLD
        # inline subtree with a POINTER — the value lives at the pointer's target
        # (e.g. a CZ pulse the old builder stored inline in the macro, the current
        # builder references from the qubit z line). Walk the merged tree: a
        # pointer ancestor => superseded; a missing key => truly lost.
        (stats.superseded if _has_pointer_ancestor(merged, p)
         else stats.residual_lost).append(p)
    stats.superseded.sort(key=natural_key)
    stats.residual_lost.sort(key=natural_key)
    # First, not sorted in: the panel shows only the first 80 of a list a
    # removed qubit fills with hundreds of its own leaves, and a changed
    # association is the line a reader could not have predicted.
    stats.residual_lost[:0] = sorted(
        ref_drops + [f"{p} (the rebuild left it empty; {why})"
                     for p, why in stats.rebuild_removed], key=natural_key)

    grafted_prefixes = [p for p, _ in stats.graft_subtrees]
    dangling: list[str] = []
    for p, v in _iter_leaves(merged):
        if is_pointer(v) and v.startswith("#/") and not _resolves(merged, v):
            if any(p == gp or p.startswith(gp + ".") for gp in grafted_prefixes):
                dangling.append(p)

    # Prune redundant OLD-representation operations the rebuild superseded (their
    # pointers are broken and nothing references them). What remains dangling is
    # inherent — e.g. a preserved TWPA's wiring pointer the builder can't rebuild.
    stats.pruned_ops, stats.dangling_grafts = _prune_redundant_graft_ops(merged, dangling)

    return MergeResult(merged=merged, stats=stats)


# ---------------------------------------------------------------------------
# Report views over MergeStats (QA F17 / regenerate-r2-28). Derived only: the
# stats lists above stay exactly as the merge recorded them.

_LOST_KIND_RANK = {"qubit": 0, "pair": 1, "twpa": 2, "port": 3, "other": 4}


def _lost_owner(path: str) -> tuple[str, str, list[str]]:
    """``(kind, owner, owner_segments)`` of one not-carried report line.

    A line may carry a trailing note (``"<path> (the rebuild left it empty;
    ...)"`` / ``"<path> -> #/qubits/q5 (...)"``) -- the owner is read from the
    dot-path before the first space."""
    segs = path.split(" ", 1)[0].split(".")
    top = segs[0]
    kinds = {"qubits": "qubit", "qubit_pairs": "pair", "twpas": "twpa"}
    if top in kinds and len(segs) > 1:
        return kinds[top], segs[1], segs[:2]
    if top == "ports" and len(segs) > 3:
        # ports.<type>.<con>.<fem>.<port>.<leaf> (OPX1000) or
        # ports.<type>.<con>.<port>.<leaf> (OPX+): the digit run names the port.
        nums: list[str] = []
        for s in segs[3:-1]:
            if not s.isdigit():
                break
            nums.append(s)
        return ("port", f"{segs[1]} {segs[2]}/" + "/".join(nums),
                segs[:3 + len(nums)])
    return "other", top, segs[:1]


def group_lost_paths(paths: list[str], merged: dict | None = None,
                     reversed_pairs: dict[str, str] | None = None,
                     cap: int = 20) -> list[dict]:
    """Group not-carried report lines by the entity they belonged to.

    One removed qubit fills ``residual_lost`` with hundreds of its own leaves
    (a port's ``controller_id`` beside a real calibration), which a flat list
    buries. Each group: ``{"kind", "owner", "n", "paths"}`` (paths capped at
    ``cap``; ``n`` is the true count) plus ``"present"`` -- whether the owner
    still exists in ``merged`` (``None`` when unknown / not an entity) -- and
    ``"reversed_as"`` for a pair the rebuild only has reversed. Ordered
    qubits, pairs, TWPAs, ports, then the rest; natural order within each."""
    groups: dict[tuple[str, str], dict] = {}
    for p in paths:
        kind, owner, osegs = _lost_owner(p)
        g = groups.get((kind, owner))
        if g is None:
            present = None
            if merged is not None and kind != "other":
                node: Any = merged
                for s in osegs:
                    node = node.get(s) if isinstance(node, dict) else None
                present = isinstance(node, dict)
            g = groups[(kind, owner)] = {"kind": kind, "owner": owner,
                                         "present": present, "n": 0, "paths": []}
            if kind == "pair" and reversed_pairs and owner in reversed_pairs:
                g["reversed_as"] = reversed_pairs[owner]
        g["n"] += 1
        if len(g["paths"]) < cap:
            g["paths"].append(p)
    return sorted(groups.values(),
                  key=lambda g: (_LOST_KIND_RANK[g["kind"]], natural_key(g["owner"])))


def _nearest_typed_ancestor(root: Any, dot_path: str) -> str | None:
    """Dot-path of the closest ANCESTOR of ``dot_path`` in ``root`` that
    carries a ``__class__`` (``"(root)"`` for the root) -- the object whose
    class schema decided whether that field could stay."""
    segs = dot_path.split(".")
    nodes = [root]
    node = root
    for s in segs[:-1]:
        node = node.get(s) if isinstance(node, dict) else None
        nodes.append(node)
    for i in range(len(nodes) - 1, -1, -1):
        n = nodes[i]
        if isinstance(n, dict) and isinstance(n.get("__class__"), str):
            return ".".join(segs[:i]) if i else "(root)"
    return None


def class_change_groups(class_changed: list[tuple[str, str, str]],
                        dropped_paths: list[str], old_state: dict,
                        sample: int = 3) -> list[dict]:
    """``class_changed`` grouped by ``(old, new)`` class, first-seen order,
    over the FULL list (the report ships only 80 paths; grouping that page
    under-counted every group and hid any group sorted past it -- QA r2-28).

    Each group: ``{"old", "new", "count", "paths" (first ``sample``),
    "dropped"}`` where ``dropped`` counts the dropped / not-carried fields
    whose owning object (nearest typed ancestor in the SOURCE chip) is one of
    the group's re-typed objects. ``dropped == 0`` is a measured "no fields
    lost" -- e.g. a class quam moved into quam_builder -- not a claim that
    the two classes behave identically."""
    owner_of = {p: (o, n) for p, o, n in class_changed}
    groups: dict[tuple[str, str], dict] = {}
    for p, o, n in class_changed:
        g = groups.setdefault((o, n), {"old": o, "new": n, "count": 0,
                                       "paths": [], "dropped": 0})
        g["count"] += 1
        if len(g["paths"]) < sample:
            g["paths"].append(p)
    if owner_of:
        for dp in dropped_paths:
            anc = _nearest_typed_ancestor(old_state, dp.split(" ", 1)[0])
            if anc in owner_of:
                groups[owner_of[anc]]["dropped"] += 1
    return list(groups.values())
