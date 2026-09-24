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
# leaves lost on LabA); leaving ``twpas`` graftable preserves them wholesale
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
    ports_fresh: list[tuple[str, str, str]] = field(default_factory=list)  # (port, old owner, new owner) -- old values left with their line; rebuild defaults stand (QA F1)
    pairs_reversed: list[tuple[str, str]] = field(default_factory=list)  # (old id, new id) -- the rebuild has the pair only with control/target swapped (QA r2-09)
    twpas_removed: list[str] = field(default_factory=list)  # OLD TWPAs the step-4 list no longer carries (renamed / deleted) -- not grafted back (QA r2-10)


@dataclass
class MergeResult:
    merged: dict
    stats: MergeStats


def _kept_class(old: dict, new: dict, keep: dict | None) -> str | None:
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
    if not rec or n_cls not in (rec.get("bases") or ()):
        return None
    return o_cls


_DROPPED = object()   # _gate_graft: the whole object is dropped


def _gate_graft(obj: Any, path: str, schemas: dict | None,
                env_fields: dict, stats: MergeStats) -> Any:
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
        g = _gate_graft(v, sub, schemas, env_fields, stats)
        if g is not _DROPPED:
            out[k] = g
    return out


def _merge(old: Any, new: Any, path: str, stats: MergeStats,
           schemas: dict[str, list[str]] | None = None,
           protect: set[str] | None = None,
           keep: dict | None = None,
           carry_ports: set[str] | None = None,
           env_fields: dict | None = None) -> Any:
    if isinstance(old, dict) and isinstance(new, dict):
        out: dict = {}
        # Keys whose OLD typed object met a NEW null: decided by the tier-2
        # graft loop below (schema gate + dangling check), never tier-1.
        deferred: set = set()
        # Never at the root: the root has its own slot in the build spec
        # (docs/176 `spec.quam_class`, the Review step's "Chip root class"),
        # and the build wrote exactly the class the user named there. The
        # docs/202 §15 keep is for objects below it the spec cannot name.
        kept_cls = _kept_class(old, new, keep) if path else None
        for k, nv in new.items():
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
                                env_fields)
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
            if (legal is not None and schemas and isinstance(ov, dict)
                    and isinstance(ov.get("__class__"), str)
                    and ov["__class__"] not in schemas):
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
            if env_fields is not None:
                # QA regenerate-r2-15: the checks above look at the graft ROOT
                # only; the subtree under it is gated here, at every depth,
                # by the build env's own answer for every class in it.
                gv = _gate_graft(ov, f"{path}.{k}" if path else k,
                                 schemas, env_fields, stats)
                if gv is _DROPPED:
                    continue
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
    rest = pointer[1:]                           # "./x" or "../x" or "../../x"
    if rest.startswith("./"):
        rest = rest[2:]
    else:
        while rest.startswith("../"):
            if not base:
                return None
            base = base[:-1]
            rest = rest[3:]
    return ".".join(base + [s for s in rest.split("/") if s])


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
                     new_wiring: dict | None = None) -> set[str]:
    old_ents = _port_entities(old_state.get("ports"))
    if not old_ents:
        return set()
    new_ents = _port_entities(new_state.get("ports"))
    new_hw = {_port_hw(p, e) for p, e in new_ents.items()}
    new_doc = _merged_doc(new_state, new_wiring)
    # (a port the rebuild has is merged normally -- it never reaches the graft)
    cand = {p for p, e in old_ents.items()
            if p not in new_ents and _port_hw(p, e) in new_hw}
    if not cand:
        return set()

    internal: dict[str, list[str]] = {p: [] for p in cand}
    for src, kind, val in _walk_refs(_merged_doc(old_state, old_wiring)):
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
# rule whenever owners are unknown or disagree.
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
                new_wiring: dict | None = None
                ) -> tuple[dict[str, tuple[str, str]], dict[str, tuple[str, str]]]:
    """Which rebuilt ports take which OLD port's values.

    ``moves = {new port: (old port, owner)}`` -- every owner the two builds
    share came from ONE other old port (a brand-new line beside them, e.g. a
    qubit added to a moved feedline, brings no calibration and does not
    count). ``fresh = {new port: (old owner, new owner)}`` -- the port's old
    values LEFT with their line (it is some move's source) and only brand-new
    lines use it now: the rebuild's defaults stand, never someone else's
    calibration. Anything else keeps the port-number identity. ``new_state``
    must be post-:func:`_reconcile_pair_ids`, so a pair's owner path matches
    across a builder's pair-id drift.
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
    moved_from = {o for o, _ in moves.values()}
    fresh: dict[str, tuple[str, str]] = {}
    for n, owners in new_owners.items():
        if n in moves or n not in moved_from:
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


def _ungraft_unlanded(merged: dict, stats: MergeStats,
                      new_wiring: dict | None) -> None:
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


def merge_states(old_state: dict, new_state: dict,
                 class_schemas: dict[str, list[str]] | None = None,
                 protect_paths: set[str] | None = None,
                 old_wiring: dict | None = None,
                 new_wiring: dict | None = None,
                 keep_classes: dict | None = None,
                 twpa_ids: Any = None,
                 env_fields: dict | None = None) -> MergeResult:
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
    carry_ports = _carryable_ports(old_state, new_state, old_wiring, new_wiring)
    # QA F1: the OLD side the merge reads -- a port's values moved to wherever
    # its line went. QA r2-10: minus the TWPAs the user took off step 4.
    moves, fresh = _port_moves(old_state, new_state, old_wiring, new_wiring)
    old_view = (_ports_view(old_state, new_state, moves, fresh)
                if (moves or fresh) else old_state)
    stats.ports_moved = sorted(((o, n, who) for n, (o, who) in moves.items()),
                               key=lambda t: natural_key(t[1]))
    stats.ports_fresh = sorted(((n, was, now) for n, (was, now) in fresh.items()),
                               key=lambda t: natural_key(t[0]))
    removed_twpas = _twpas_the_spec_removed(old_state, new_state, old_wiring,
                                            twpa_ids)
    if removed_twpas:
        old_view = dict(old_view)
        old_view["twpas"] = {k: v for k, v in old_state["twpas"].items()
                             if k not in removed_twpas}
        stats.twpas_removed = sorted(removed_twpas, key=natural_key)
    stats.pairs_reversed = _reversed_pairs(old_state, new_state,
                                           old_wiring, new_wiring)
    merged = _merge(old_view, new_state, "", stats, class_schemas, protect_paths,
                    keep_classes, carry_ports, env_fields)
    _ungraft_unlanded(merged, stats, new_wiring)
    # A removed qubit/pair is still NAMED inside a list another object carried
    # (a TWPA's `qubits`); the list survives as a leaf, so drop the reference
    # visibly here (reported with the residual loss) rather than ship it broken.
    ref_drops = _prune_removed_entity_refs(merged, old_state)
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
    for p, ov in old_scalars:
        if p in merged_paths:
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
