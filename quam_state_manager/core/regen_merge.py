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
    populate_protected: list[str] = field(default_factory=list)  # user populate edits kept as NEW over tier-1 (docs/72)
    populate_conflicts: list[str] = field(default_factory=list)  # hand-tuned OLD values kept where a populate edit implied a derived change (z delay)


@dataclass
class MergeResult:
    merged: dict
    stats: MergeStats


def _merge(old: Any, new: Any, path: str, stats: MergeStats,
           schemas: dict[str, list[str]] | None = None,
           protect: set[str] | None = None) -> Any:
    if isinstance(old, dict) and isinstance(new, dict):
        out: dict = {}
        for k, nv in new.items():
            if k in ("__class__", "__package_versions__"):
                # Serialization artifacts, not calibration: always the NEW
                # build's. Tier-1-carrying an OLD __package_versions__ stamp
                # would lie about which stack wrote the rebuilt state.
                out[k] = copy.deepcopy(nv)
                continue
            if k in STRUCTURAL_LEAF_KEYS:               # membership -> always NEW
                out[k] = copy.deepcopy(nv)
                stats.kept_new_pointer += 1
                continue
            if k in old:
                out[k] = _merge(old[k], nv, f"{path}.{k}" if path else k,
                                stats, schemas, protect)
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
        if schemas:
            cls = new.get("__class__")
            if isinstance(cls, str):
                legal = schemas.get(cls)
        for k, ov in old.items():
            if (k in new or k in ("__class__", "__package_versions__")
                    or k in STRUCTURAL_LEAF_KEYS):
                # __package_versions__ is quam's serialization stamp, not user
                # data — never carry a stale one onto a rebuilt state.
                continue
            if not graftable_here:
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
                stats.schema_dropped.append(f"{path}.{k}" if path else k)
                continue
            out[k] = copy.deepcopy(ov)
            n = _count_leaves(ov)
            stats.grafted += n
            stats.graft_subtrees.append((f"{path}.{k}" if path else k, n))
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
    return sorted(prunable), remaining


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


def graft_twpa_wiring(merged_state: dict, old_state: dict,
                      old_wiring: dict, new_wiring: dict) -> int:
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
                dst[segs[-1]] = copy.deepcopy(src[segs[-1]])
    return carried


def merge_states(old_state: dict, new_state: dict,
                 class_schemas: dict[str, list[str]] | None = None,
                 protect_paths: set[str] | None = None,
                 old_wiring: dict | None = None,
                 new_wiring: dict | None = None) -> MergeResult:
    """Merge the OLD calibrated state onto the NEW rebuilt structure.

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
    merged = _merge(old_state, new_state, "", stats, class_schemas, protect_paths)
    stats.schema_dropped.sort()
    stats.populate_protected.sort()

    merged_paths = {p for p, _ in _iter_leaves(merged)}
    old_scalars = [(p, v) for p, v in _iter_leaves(old_state)
                   if not is_pointer(v)
                   and not p.startswith("__package_versions__")]  # artifact, never "lost"
    for p, _ in old_scalars:
        if p in merged_paths:
            continue
        # Schema-gate drops are already reported in stats.schema_dropped —
        # don't double-count them as residual loss (they are deliberate, not
        # "calibration with no home"). A dropped key may be a subtree root, so
        # prefix-match its leaves.
        if any(p == dp or p.startswith(dp + ".") for dp in stats.schema_dropped):
            continue
        # A path is SUPERSEDED (not lost) when the NEW structure replaced an OLD
        # inline subtree with a POINTER — the value lives at the pointer's target
        # (e.g. a CZ pulse the old builder stored inline in the macro, the current
        # builder references from the qubit z line). Walk the merged tree: a
        # pointer ancestor => superseded; a missing key => truly lost.
        (stats.superseded if _has_pointer_ancestor(merged, p)
         else stats.residual_lost).append(p)
    stats.superseded.sort()
    stats.residual_lost.sort()

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
