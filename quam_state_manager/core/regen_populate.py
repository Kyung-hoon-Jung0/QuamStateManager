"""Populate-edit protection for the Re-generate flow (r16, docs/72).

The regenerate pipeline rebuilds a chip WITH the user's Populate-step values
(``run_build.apply_populate``), then ``regen_merge.merge_states`` tier-1
carries every OLD calibrated leaf over the fresh build — which silently
reverted every populate edit the user made in the wizard ("the whole point of
re-gen is changing existing values", r16 report ⓪).

This module computes, from the WIZARD-SESSION diff, the set of merged-tree
leaf paths whose NEW value must win over tier-1:

- ``changed_fields(spec_populate, baseline, touched)`` — which
  ``(group, id, field)`` cells the user changed *inside the wizard*. The
  baseline is the populate the wizard DISPLAYED at hydration (snapshotted
  client-side and POSTed back verbatim) — never re-derived at build time,
  so a concurrent in-app edit to the working copy can not masquerade as a
  wizard edit and get clobbered.
- ``protect_paths(changed, spec_populate, old_state, old_wiring, new_state,
  new_wiring)`` — expands each changed cell to the concrete state dot-paths
  ``apply_populate`` wrote (the fanout mirrors ``run_build``'s writers),
  existence-gated against the NEW state so the set stays tight.

Pure dict work, no quam imports. Unknown groups/fields expand to nothing —
an unprotected edit degrades to today's behavior (tier-1 carry), never worse.
"""

from __future__ import annotations

import math
import re
from typing import Any, Iterable

from quam_state_manager.core import cr_semantics
from quam_state_manager.core.regen_merge import _pair_membership

# Mirror of run_build._BAND_TO_DELAY_NS (LF z-port delay per xy band, ns).
# Pinned in sync by tests/test_regen_populate.py::test_band_delay_table_in_sync.
_BAND_TO_DELAY_NS = {1: 141, 2: 161, 3: 141}

# Single-qubit DragCosine gate family: add_DragCosine_pulses derives the whole
# family (x90 / y180 / -x90 …) from the x180 seeds, so a seed edit must protect
# every family member's derived field or tier-1 reverts them one by one.
_DRAG_FAMILY_RE = re.compile(r"^-?[xy]\d+")


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _close(a: Any, b: Any) -> bool:
    """Value equality with float tolerance (wizard cells round-trip floats
    through input boxes — 5.905e9 typed back is bit-identical, but be safe)."""
    if _num(a) and _num(b):
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12)
    return a == b


def changed_fields(spec_populate: Any, baseline: Any,
                   touched: Iterable[Iterable[str]] | None = None
                   ) -> list[tuple[str, str, str]]:
    """``[(group, id, field)]`` the user changed in this wizard session.

    Rule: changed iff (present in BOTH and not close) OR (explicitly touched
    AND present in spec). A cell present in baseline but CLEARED in the spec
    is NOT changed — "clear = don't re-seed", tier-1 keeps the calibration.
    """
    spec_populate = spec_populate if isinstance(spec_populate, dict) else {}
    baseline = baseline if isinstance(baseline, dict) else {}
    touched_set: set[tuple[str, str, str]] = set()
    for t in touched or ():
        try:
            g, i, f = (str(x) for x in t)
            touched_set.add((g, i, f))
        except (TypeError, ValueError):
            continue

    out: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for group, ids in spec_populate.items():
        if not isinstance(ids, dict):
            continue
        base_ids = baseline.get(group)
        base_ids = base_ids if isinstance(base_ids, dict) else {}
        for rid, fields in ids.items():
            if not isinstance(fields, dict):
                continue
            base_fields = base_ids.get(rid)
            base_fields = base_fields if isinstance(base_fields, dict) else {}
            for fname, val in fields.items():
                key = (str(group), str(rid), str(fname))
                if key in seen:
                    continue
                differs = (fname in base_fields
                           and not _close(val, base_fields[fname]))
                if differs or key in touched_set:
                    seen.add(key)
                    out.append(key)
    # touched cells whose value happens to equal a baseline the extractor
    # couldn't read are covered above; a touched cell absent from the spec
    # (cleared after typing) is deliberately NOT protected.
    return out


# ---------------------------------------------------------------------------
# path resolution helpers

def _walk(root: Any, dot_path: str) -> Any:
    node = root
    for seg in dot_path.split("."):
        if isinstance(node, dict) and seg in node:
            node = node[seg]
        else:
            return None
    return node


def _exists(root: dict, dot_path: str) -> bool:
    node = root
    for seg in dot_path.split("."):
        if isinstance(node, dict) and seg in node:
            node = node[seg]
        else:
            return False
    return True


def _resolve_ptr_path(root: dict, ptr: Any, _depth: int = 0) -> str | None:
    """Follow an absolute ``#/a/b/c`` pointer CHAIN and return the final
    node's dot-path (``ports.mw_outputs.con1.2.3``), or None."""
    if _depth > 8 or not (isinstance(ptr, str) and ptr.startswith("#/")):
        return None
    node: Any = root
    segs: list[str] = []
    for seg in ptr[2:].split("/"):
        if not seg:
            continue
        if isinstance(node, dict) and seg in node:
            node = node[seg]
            segs.append(seg)
        else:
            return None
    if isinstance(node, str) and node.startswith("#/"):
        return _resolve_ptr_path(root, node, _depth + 1)
    return ".".join(segs)


def _root_of(state: dict, wiring: dict) -> dict:
    root = dict(state)
    w = wiring.get("wiring") if isinstance(wiring, dict) else None
    root["wiring"] = w if isinstance(w, dict) else {}
    if "ports" not in root and isinstance(wiring, dict) \
            and isinstance(wiring.get("ports"), dict):
        root["ports"] = wiring["ports"]
    return root


def _chan_port_path(root: dict, qubit: dict | None, chan: str) -> str | None:
    """Dot-path of the resolved output port of ``qubits.<q>.<chan>``."""
    ch = qubit.get(chan) if isinstance(qubit, dict) else None
    if not isinstance(ch, dict):
        return None
    return _resolve_ptr_path(root, ch.get("opx_output"))


def _old_pair_id(members: tuple[str, str] | None, old_state: dict) -> str | None:
    """The OLD state's pair id for a (control, target) membership — the merge
    walks post-reconcile keys, which ARE the old ids."""
    if not members:
        return None
    for pid, pair in (old_state.get("qubit_pairs") or {}).items():
        if _pair_membership(pair) == members:
            return pid
    return None


def _spec_pair_members(rid: str, spec_populate: dict,
                       new_state: dict) -> tuple[str, str] | None:
    """Members for a wizard ``populate.pairs`` key. The wizard keys buckets by
    the canonical ``control-target`` id derived from pair REFS — never parse
    the id string (short-form ``q1-2`` hazard); look the pair up instead."""
    for pid, pair in (new_state.get("qubit_pairs") or {}).items():
        m = _pair_membership(pair)
        if m and f"{m[0]}-{m[1]}" == rid:
            return m
    # fall back: an id that IS a new-state key
    m = _pair_membership((new_state.get("qubit_pairs") or {}).get(rid))
    return m


# ---------------------------------------------------------------------------
# fanout

def protect_paths(changed: list[tuple[str, str, str]], spec_populate: Any,
                  old_state: dict, old_wiring: dict,
                  new_state: dict, new_wiring: dict
                  ) -> tuple[set[str], list[str]]:
    """Expand changed populate cells to merged-tree leaf dot-paths.

    Returns ``(protect, conflicts)`` — ``protect`` feeds
    ``merge_states(protect_paths=…)``; ``conflicts`` are honest warnings where
    a derived value (z-port delay) was NOT auto-protected because the old
    value looks hand-tuned (docs/31 makes delay user-overridable post-build).
    """
    protect: set[str] = set()
    conflicts: list[str] = []
    if not changed:
        return protect, conflicts
    new_root = _root_of(new_state, new_wiring)
    old_root = _root_of(old_state, old_wiring)
    spec_populate = spec_populate if isinstance(spec_populate, dict) else {}

    def add(path: str | None) -> None:
        if path and _exists(new_state, path):
            protect.add(path)

    def band_delay(qid: str, old_band: Any, new_band: Any) -> None:
        """LO/band edits change the derived LF z delay; protect it only when
        the old delay was never hand-tuned (== the band table value)."""
        if not (_num(old_band) and _num(new_band)) or old_band == new_band:
            return
        q_new = (new_state.get("qubits") or {}).get(qid)
        zp = _chan_port_path(new_root, q_new, "z")
        if not zp:
            return
        delay_path = f"{zp}.delay"
        if not _exists(new_state, delay_path):
            return
        q_old = (old_state.get("qubits") or {}).get(qid)
        zp_old = _chan_port_path(old_root, q_old, "z")
        old_delay = _walk(old_root, f"{zp_old}.delay") if zp_old else None
        if old_delay is None or old_delay == _BAND_TO_DELAY_NS.get(int(old_band)):
            protect.add(delay_path)
        else:
            conflicts.append(
                f"{delay_path}: band changed {old_band}→{new_band} but the "
                f"old delay ({old_delay} ns) looks hand-tuned — kept; verify.")

    for group, rid, fname in changed:
        if group == "qubit":
            q_new = (new_state.get("qubits") or {}).get(rid)
            base = f"qubits.{rid}"
            if fname == "RF_freq":
                add(f"{base}.f_01")
                add(f"{base}.xy.RF_frequency")
            elif fname in ("anharmonicity", "grid_location"):
                add(f"{base}.{fname}")
            elif fname in ("LO_frequency", "band", "full_scale_power_dbm",
                           "cr_lo_frequency"):
                pp = _chan_port_path(new_root, q_new, "xy")
                if pp:
                    if fname == "LO_frequency":
                        add(f"{pp}.upconverter_frequency")
                        add(f"{pp}.band")
                        add(f"{pp}.upconverters.1.frequency")
                    elif fname == "band":
                        add(f"{pp}.band")
                    elif fname == "cr_lo_frequency":
                        add(f"{pp}.upconverters.2.frequency")
                    else:
                        add(f"{pp}.full_scale_power_dbm")
                if fname in ("LO_frequency", "band"):
                    old_band = _walk(old_root, f"{p}.band") \
                        if (p := _chan_port_path(old_root,
                                                 (old_state.get("qubits") or {}).get(rid),
                                                 "xy")) else None
                    new_band = _walk(new_root, f"{pp}.band") if pp else None
                    band_delay(rid, old_band, new_band)
        elif group == "resonator":
            q_new = (new_state.get("qubits") or {}).get(rid)
            base = f"qubits.{rid}.resonator"
            if fname == "RF_freq":
                add(f"{base}.f_01")
                add(f"{base}.RF_frequency")
            elif fname in ("depletion_time", "time_of_flight"):
                add(f"{base}.{fname}")
            elif fname in ("readout_length", "readout_amplitude"):
                leaf = "length" if fname == "readout_length" else "amplitude"
                add(f"{base}.operations.readout.{leaf}")
            elif fname in ("LO_frequency", "band", "full_scale_power_dbm"):
                r = q_new.get("resonator") if isinstance(q_new, dict) else None
                pp = _resolve_ptr_path(new_root, r.get("opx_output")) \
                    if isinstance(r, dict) else None
                if pp:
                    if fname == "LO_frequency":
                        add(f"{pp}.upconverter_frequency")
                        add(f"{pp}.band")
                    elif fname == "band":
                        add(f"{pp}.band")
                    else:
                        add(f"{pp}.full_scale_power_dbm")
        elif group == "flux":
            base = f"qubits.{rid}.z"
            if fname in ("independent_offset", "joint_offset", "min_offset",
                         "arbitrary_offset", "flux_point", "settle_time"):
                add(f"{base}.{fname}")
            elif fname in ("output_mode", "upsampling_mode"):
                q_new = (new_state.get("qubits") or {}).get(rid)
                pp = _chan_port_path(new_root, q_new, "z")
                if pp:
                    add(f"{pp}.{fname}")
        elif group == "pulses":
            ops_path = f"qubits.{rid}.xy.operations"
            ops = _walk(new_state, ops_path)
            ops = ops if isinstance(ops, dict) else {}
            if fname in ("saturation_length", "saturation_amplitude"):
                leaf = "length" if fname.endswith("length") else "amplitude"
                add(f"{ops_path}.saturation.{leaf}")
            else:
                leaf = {"x180_length": "length", "x180_amplitude": "amplitude",
                        "drag_alpha": "alpha", "drag_detuning": "detuning"
                        }.get(fname)
                if leaf:
                    for op_name in ops:
                        if _DRAG_FAMILY_RE.match(op_name):
                            add(f"{ops_path}.{op_name}.{leaf}")
        elif group == "pairs":
            members = _spec_pair_members(rid, spec_populate, new_state)
            merged_pid = _old_pair_id(members, old_state) or rid
            pbase = f"qubit_pairs.{merged_pid}"
            pair_new, new_key = None, None
            for pid, pr in (new_state.get("qubit_pairs") or {}).items():
                if pid == merged_pid or _pair_membership(pr) == members:
                    pair_new, new_key = pr, pid
                    break

            def add_rel(rel: str) -> None:
                # The MERGED tree keys pairs by the OLD id (post-reconcile),
                # but existence must be checked under the NEW build's key.
                if new_key and _exists(new_state,
                                       f"qubit_pairs.{new_key}.{rel}"):
                    protect.add(f"{pbase}.{rel}")

            if fname == "moving_qubit":
                add_rel("moving_qubit")
            elif fname in ("cz_interaction_duration", "cz_amplitude"):
                leaf = "length" if fname == "cz_interaction_duration" else "amplitude"
                macros = (pair_new or {}).get("macros")
                for mname, m in (macros or {}).items() if isinstance(macros, dict) else ():
                    if not (isinstance(m, dict) and mname.startswith("cz")):
                        continue
                    add_rel(f"macros.{mname}.flux_pulse_qubit.{leaf}")
                # some builders seed the flux pulses on the moving qubit's z ops
                if members:
                    for qn in members:
                        zops_path = f"qubits.{qn}.z.operations"
                        zops = _walk(new_state, zops_path)
                        for on in zops if isinstance(zops, dict) else ():
                            if on.startswith("cz") and (
                                    merged_pid in on
                                    or all(mn in on for mn in members)):
                                add(f"{zops_path}.{on}.{leaf}")
            elif fname in ("target_qubit_LO_frequency", "target_qubit_IF_frequency",
                           "cr_drive_amplitude", "cr_square_length",
                           "cr_flattop_length", "cr_flattop_flat_length",
                           "cr_cancel_amplitude") or fname.startswith("cr_") \
                    or fname in ("qc_correction_phase", "qt_correction_phase") \
                    or fname.startswith("zz_"):
                _protect_cr_zz(add, add_rel, merged_pid, fname, pair_new,
                               members, new_state)

    return protect, conflicts


def _protect_cr_zz(add, add_rel, merged_pid: str, fname: str,
                   pair_new: dict | None, members: tuple[str, str] | None,
                   new_state: dict) -> None:
    """CR / ZZ populate fields → pair-channel paths (flavor via cr_semantics).

    ``add_rel`` adds a PAIR-relative path (existence-checked under the NEW
    build's pair key, emitted under the merged/OLD id); ``add`` adds an
    absolute state path (for the target-qubit cancel stub).
    """
    if not isinstance(pair_new, dict):
        return
    # channel key (flavor-tolerant): the dict-valued candidate on the pair
    cr_key = next((k for k in ("cross_resonance", "cr")
                   if isinstance(pair_new.get(k), dict)), None)
    zz = cr_semantics.zz_channel(pair_new)
    zz_key = zz[0] if zz else None
    if fname in ("target_qubit_LO_frequency", "target_qubit_IF_frequency"):
        if cr_key:
            add_rel(f"{cr_key}.{fname}")
    elif fname == "cr_drive_amplitude":
        if cr_key:
            add_rel(f"{cr_key}.operations.square.amplitude")
    elif fname == "cr_square_length":
        if cr_key:
            add_rel(f"{cr_key}.operations.square.length")
    elif fname in ("cr_flattop_length", "cr_flattop_flat_length"):
        if cr_key:
            leaf = "length" if fname == "cr_flattop_length" else "flat_length"
            add_rel(f"{cr_key}.operations.flattop.{leaf}")
    elif fname == "cr_cancel_amplitude":
        # the cancel stub lives on the TARGET's xy ops as cr_square_<pair id> —
        # gate on the pair id / member tokens so a shared target qubit's OTHER
        # pairs' stubs are never over-protected.
        if members:
            tgt = members[1]
            ops_path = f"qubits.{tgt}.xy.operations"
            node = new_state.get("qubits", {}).get(tgt)
            xy = node.get("xy") if isinstance(node, dict) else None
            xops = xy.get("operations") if isinstance(xy, dict) else None
            for on in xops if isinstance(xops, dict) else ():
                if on.startswith("cr_square") and (
                        merged_pid in on or all(m in on for m in members)):
                    add(f"{ops_path}.{on}.amplitude")
    elif fname == "zz_detuning":
        if zz_key:
            add_rel(f"{zz_key}.detuning")
    elif fname == "zz_drive_amplitude":
        if zz_key:
            add_rel(f"{zz_key}.operations.square.amplitude")
    elif fname in ("zz_flattop_length", "zz_flattop_flat_length"):
        if zz_key:
            leaf = "length" if fname == "zz_flattop_length" else "flat_length"
            add_rel(f"{zz_key}.operations.flattop.{leaf}")
    elif fname in ("qc_correction_phase", "qt_correction_phase") \
            or fname.startswith("cr_"):
        # lever fields: suffix relative to the pair, via the flavor map
        lever = fname if fname in ("qc_correction_phase", "qt_correction_phase") \
            else fname[3:]
        suffix = cr_semantics.lever_map(pair_new).get(lever)
        if suffix:
            add_rel(suffix)
