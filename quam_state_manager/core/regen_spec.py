"""Reconstruct a build spec from an existing chip's state + wiring.

The Re-generate Config flow re-opens the wizard pre-filled from a chip the user
already generated. The wizard's structural inputs are the ``spec`` that drives
``generator/run_build.py``; this module rebuilds that spec from the chip's
``state.json`` + ``wiring.json`` so the rebuild reproduces the same structure
(then the P2 merge -- :mod:`core.regen_merge` -- carries the calibrated values
and grafts user-added operations back on).

Design:

- **Wiring is pinned** from the existing port pointers (each channel emits a
  hard ``mw_fem`` / ``lf_fem`` constraint), so an untouched chip rebuilds to the
  same ports; only the lines the user edits in the wizard re-allocate.
- **Instruments** are inferred from the ports actually used (MW-FEM from
  ``mw_*`` pointers, LF-FEM from ``analog_*``).
- **pair_gate** is the *dominant* gate family across pairs (the spec carries a
  single ``pair_gate``); per-pair gate VARIETY is preserved by the merge graft,
  not by the spec. ``mixed_gates`` flags when a chip uses more than one family.

Pure functions over plain dicts -- no ``quam`` / ``quam_builder`` imports. Where
a persisted ``generate_spec.json`` sidecar exists it should be preferred over
this best-effort reconstruction (exact vs inferred). See
``docs/51_regenerate_config.md``.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Exact-spec sidecar. Written next to a rebuilt chip so a later re-generate uses
# the EXACT spec that built it instead of the best-effort reconstruction. Lives
# in a SUBFOLDER: QUAM's ``Quam.load()`` reads every top-level ``.json`` in a
# chip folder, so a spec ``.json`` at the top level would corrupt the load — a
# subfolder is invisible to it (verified). See docs/51_regenerate_config.md.
_SIDECAR_DIR = ".regen"
_SIDECAR_FILE = "generate_spec.json"

_PORT_RE = re.compile(r"#/ports/([a-z_]+)/con(\d+)/(\d+)/(\d+)")


def _parse_port(ptr: Any) -> tuple[str, int, int, int] | None:
    m = _PORT_RE.match(ptr or "") if isinstance(ptr, str) else None
    if not m:
        return None
    return m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))


def _fem_type(category: str) -> str | None:
    if category.startswith("mw"):
        return "mw"
    if category.startswith("analog"):
        return "lf"
    return None


def _detect_pair_gate(state: dict) -> tuple[str, bool]:
    """Return ``(dominant_gate, mixed)``. Families: cz_tunable / cz_fixed / cr."""
    families: list[str] = []
    for pair in (state.get("qubit_pairs") or {}).values():
        if not isinstance(pair, dict):
            continue
        macro_names = " ".join((pair.get("macros") or {}).keys()).lower()
        has_coupler = isinstance(pair.get("coupler"), dict) and pair["coupler"]
        has_cr = (isinstance(pair.get("cross_resonance"), dict) and pair["cross_resonance"])
        if "cr" in macro_names or has_cr:
            families.append("cr")
        elif "cz" in macro_names or has_coupler:
            families.append("cz_tunable" if has_coupler else "cz_fixed")
    if not families:
        return "cz_tunable", False
    counts = {f: families.count(f) for f in set(families)}
    dominant = max(counts, key=counts.get)
    return dominant, len(counts) > 1


def _resolve_ptr(root: dict, ptr: Any, _depth: int = 0) -> Any:
    """Resolve an absolute ``#/a/b/c`` pointer against ``root``, following pointer
    CHAINS (a channel's ``opx_output`` is ``#/wiring/…`` → ``#/ports/…`` → port).
    ``root`` must be the merged state+wiring dict. Returns None if unresolvable."""
    if _depth > 8 or not (isinstance(ptr, str) and ptr.startswith("#/")):
        return None
    node: Any = root
    for seg in ptr[2:].split("/"):
        if isinstance(node, dict) and seg in node:
            node = node[seg]
        else:
            return None
    if isinstance(node, str) and node.startswith("#/"):   # follow the chain
        return _resolve_ptr(root, node, _depth + 1)
    return node


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _populate_pair_key(pid: str, pair: dict, root: dict) -> str:
    """Key a per-pair populate bucket by the WIZARD's canonical id.

    The wizard keys ``populate.pairs`` by ``f"{control}-{target}"`` derived
    from the spec's ``qubit_pairs`` entries (its reconcile pass prunes any
    other spelling as stale), and ``run_build`` seeds by the same orientation.
    Keying by the SOURCE chip's pair NAME — ascending ``"q0-1"`` while the
    pair's control is actually ``q1`` — lost the seeds of every
    orientation-flipped pair. Same control/target derivation as the spec's
    pair list (state refs first, coupler-channel wiring refs as fallback);
    the raw name only when neither resolves.
    """
    ctrl = str(pair.get("qubit_control", "")).split("/")[-1]
    tgt = str(pair.get("qubit_target", "")).split("/")[-1]
    if not (ctrl and tgt):
        wp = ((root.get("wiring") or {}).get("qubit_pairs") or {}).get(pid)
        c = wp.get("c") if isinstance(wp, dict) else None
        if isinstance(c, dict):
            ctrl = ctrl or str(c.get("control_qubit", "")).split("/")[-1]
            tgt = tgt or str(c.get("target_qubit", "")).split("/")[-1]
    return f"{ctrl}-{tgt}" if ctrl and tgt else pid


def _extract_populate(state: dict, root: dict) -> dict:
    """Invert ``apply_populate`` — read the physics values the wizard's Populate
    step displays back out of the chip's state, so the re-opened wizard is
    pre-filled (not blank). Raw units (Hz etc.), matching what ``apply_populate``
    writes. LO / full-scale-power live on the channel's resolved output PORT.
    (The value-merge preserves everything regardless; this is for DISPLAY.)
    """
    pop_q: dict = {}
    pop_r: dict = {}
    pop_f: dict = {}
    pop_p: dict = {}
    pop_pairs: dict = {}
    for qid, q in (state.get("qubits") or {}).items():
        if not isinstance(q, dict):
            continue
        # --- qubit / xy drive (the visible RF · anharm · LO · FSP · grid table)
        qv: dict = {}
        if _num(q.get("f_01")):
            qv["RF_freq"] = q["f_01"]
        if _num(q.get("anharmonicity")):
            qv["anharmonicity"] = q["anharmonicity"]
        if q.get("grid_location") is not None:
            qv["grid_location"] = q["grid_location"]
        xy = q.get("xy") if isinstance(q.get("xy"), dict) else {}
        xy_out = _resolve_ptr(root, xy.get("opx_output"))
        if isinstance(xy_out, dict):
            if _num(xy_out.get("upconverter_frequency")):
                qv["LO_frequency"] = xy_out["upconverter_frequency"]
            else:
                # Dual-upconverter port (the shared-port CR layout): 1 = the
                # qubit's own drive LO, 2 = the CR LO. Without this fallback a
                # customer-chip regenerate loses EVERY xy LO (the scalar field
                # is None once upconverters exist).
                ucs = xy_out.get("upconverters")
                if isinstance(ucs, dict):
                    u1 = ucs.get("1", ucs.get(1))
                    if isinstance(u1, dict) and _num(u1.get("frequency")):
                        qv["LO_frequency"] = u1["frequency"]
                    u2 = ucs.get("2", ucs.get(2))
                    if isinstance(u2, dict) and _num(u2.get("frequency")):
                        qv["cr_lo_frequency"] = u2["frequency"]
            if _num(xy_out.get("full_scale_power_dbm")):
                qv["full_scale_power_dbm"] = xy_out["full_scale_power_dbm"]
            if _num(xy_out.get("band")):              # real band, never hardcode
                qv["band"] = xy_out["band"]
        if qv:
            pop_q[qid] = qv
        # --- resonator / readout
        r = q.get("resonator") if isinstance(q.get("resonator"), dict) else None
        if r is not None:
            rv: dict = {}
            rf = r.get("RF_frequency", r.get("f_01"))
            if _num(rf):
                rv["RF_freq"] = rf
            r_out = _resolve_ptr(root, r.get("opx_output"))
            if isinstance(r_out, dict):
                if _num(r_out.get("upconverter_frequency")):
                    rv["LO_frequency"] = r_out["upconverter_frequency"]
                if _num(r_out.get("full_scale_power_dbm")):
                    rv["full_scale_power_dbm"] = r_out["full_scale_power_dbm"]
                if _num(r_out.get("band")):           # real readout band, never hardcode
                    rv["band"] = r_out["band"]
            for k in ("depletion_time", "time_of_flight"):
                if _num(r.get(k)):
                    rv[k] = r[k]
            ro = (r.get("operations") or {}).get("readout")
            if isinstance(ro, dict):
                if _num(ro.get("length")):
                    rv["readout_length"] = ro["length"]
                if _num(ro.get("amplitude")):
                    rv["readout_amplitude"] = ro["amplitude"]
            if rv:
                pop_r[qid] = rv
        # --- flux (z) offsets + port output mode
        z = q.get("z") if isinstance(q.get("z"), dict) else None
        if z is not None:
            fv: dict = {}
            for k in ("independent_offset", "joint_offset", "min_offset",
                      "arbitrary_offset", "flux_point", "settle_time"):
                if k in z and not (isinstance(z[k], str) and z[k].startswith("#")):
                    fv[k] = z[k]
            z_out = _resolve_ptr(root, z.get("opx_output"))
            if isinstance(z_out, dict):
                for k in ("output_mode", "upsampling_mode"):
                    if k in z_out:
                        fv[k] = z_out[k]
            if fv:
                pop_f[qid] = fv
        # --- single-qubit gate pulses (x180 DragCosine + saturation)
        xy_ops = (xy.get("operations") or {}) if isinstance(xy, dict) else {}
        pv: dict = {}
        x180 = xy_ops.get("x180_DragCosine")
        if not isinstance(x180, dict):
            x180 = xy_ops.get("x180") if isinstance(xy_ops.get("x180"), dict) else None
        if isinstance(x180, dict):
            if _num(x180.get("length")):
                pv["x180_length"] = x180["length"]
            if _num(x180.get("amplitude")):
                pv["x180_amplitude"] = x180["amplitude"]
            if _num(x180.get("alpha")):
                pv["drag_alpha"] = x180["alpha"]
            if _num(x180.get("detuning")):
                pv["drag_detuning"] = x180["detuning"]
        sat = xy_ops.get("saturation")
        if isinstance(sat, dict):
            if _num(sat.get("length")):
                pv["saturation_length"] = sat["length"]
            if _num(sat.get("amplitude")):
                pv["saturation_amplitude"] = sat["amplitude"]
        if pv:
            pop_p[qid] = pv

    # --- qubit pairs: CZ variant / dur / amp / moving qubit (per pair)
    for pid, pair in (state.get("qubit_pairs") or {}).items():
        if not isinstance(pair, dict):
            continue
        pairv: dict = {}
        mq = pair.get("moving_qubit")
        if mq in ("control", "target"):
            pairv["moving_qubit"] = mq
        macros = pair.get("macros") or {}
        # primary CZ macro: the 'cz' alias points at it (#./cz_unipolar); else the
        # first cz_* macro. Its flux_pulse_qubit carries the dur/amp.
        primary = None
        alias = macros.get("cz")
        if isinstance(alias, str) and alias.startswith("#./"):
            primary = alias.split("/")[-1]
        if primary is None:
            primary = next((n for n in macros if n.startswith("cz_")), None)
        m = macros.get(primary) if primary else None
        if isinstance(m, dict):
            variant = primary[3:] if primary.startswith("cz_") else primary
            if variant in ("unipolar", "flattop", "bipolar", "SNZ", "flattop_erf"):
                pairv["cz_variant"] = variant
            fpq = m.get("flux_pulse_qubit")
            if isinstance(fpq, dict):
                if _num(fpq.get("length")):
                    pairv["cz_interaction_duration"] = fpq["length"]
                if _num(fpq.get("amplitude")):
                    pairv["cz_amplitude"] = fpq["amplitude"]

        # --- CR channel: levers + drive-op geometry (flavor-tolerant via
        # cr_semantics; numeric values only — pointers are re-created by the
        # seeder, and the value-merge preserves everything regardless).
        from quam_state_manager.core import cr_semantics
        cr = cr_semantics.cr_channel(pair)
        if cr is not None:
            for lever, suffix in cr_semantics.lever_map(pair).items():
                if (lever.startswith(("zz_", "macro_"))
                        or lever in ("upconverter", "bell_state_fidelity")):
                    continue
                node = pair
                for seg in suffix.split("."):
                    node = node.get(seg) if isinstance(node, dict) else None
                if _num(node) is not None:
                    # The correction phases are consumed UNPREFIXED everywhere
                    # (run_build's seed table, the wizard columns, the preset
                    # allowlist) — a cr_ prefix here would silently orphan the
                    # calibrated ZI/IZ phases on a regenerate round-trip.
                    key = (lever if lever in ("qc_correction_phase",
                                              "qt_correction_phase")
                           else f"cr_{lever}")
                    pairv[key] = node
            ops = cr.get("operations") if isinstance(cr.get("operations"), dict) else {}
            sq = ops.get("square")
            if isinstance(sq, dict):
                if _num(sq.get("amplitude")):
                    pairv["cr_drive_amplitude"] = sq["amplitude"]
                if _num(sq.get("length")):
                    pairv["cr_square_length"] = sq["length"]
            ft = ops.get("flattop")
            if isinstance(ft, dict):
                if _num(ft.get("length")):
                    pairv["cr_flattop_length"] = ft["length"]
                if _num(ft.get("flat_length")):
                    pairv["cr_flattop_flat_length"] = ft["flat_length"]
            if {"cosine", "gauss"} <= set(ops):
                pairv["cr_shapes"] = "full"
            for k in ("target_qubit_LO_frequency", "target_qubit_IF_frequency"):
                if _num(cr.get(k)):
                    pairv[k] = cr[k]
            # cancel amplitude lives on the TARGET's xy stub (cr_square_<pid>).
            # Explicit-null / pointer-string xy and operations are real
            # serializations — the type guards (not dict.get defaults) are
            # what keep a sparse chip from 500-ing the Re-generate wizard.
            tgt_name = str(pair.get("qubit_target", "")).split("/")[-1]
            tq = (state.get("qubits") or {}).get(tgt_name)
            txy = tq.get("xy") if isinstance(tq, dict) else None
            txy_ops = (txy.get("operations") or {}) if isinstance(txy, dict) else {}
            stub = txy_ops.get(f"cr_square_{pid}")
            if isinstance(stub, dict) and _num(stub.get("amplitude")):
                pairv["cr_cancel_amplitude"] = stub["amplitude"]

        # --- ZZ channel (zz_drive on a08bf66/fa540b6, zz at the branch tip)
        zz = cr_semantics.zz_channel(pair)
        if zz is not None:
            _zk, zch = zz
            if _num(zch.get("detuning")):
                pairv["zz_detuning"] = zch["detuning"]
            zops = zch.get("operations") if isinstance(zch.get("operations"), dict) else {}
            zsq = zops.get("square")
            if isinstance(zsq, dict) and _num(zsq.get("amplitude")):
                pairv["zz_drive_amplitude"] = zsq["amplitude"]
            zft = zops.get("flattop")
            if isinstance(zft, dict):
                if _num(zft.get("length")):
                    pairv["zz_flattop_length"] = zft["length"]
                if _num(zft.get("flat_length")):
                    pairv["zz_flattop_flat_length"] = zft["flat_length"]

        if pairv:
            pop_pairs[_populate_pair_key(pid, pair, root)] = pairv

    out: dict = {}
    if pop_q:
        out["qubit"] = pop_q
    if pop_r:
        out["resonator"] = pop_r
    if pop_f:
        out["flux"] = pop_f
    if pop_p:
        out["pulses"] = pop_p
    if pop_pairs:
        out["pairs"] = pop_pairs
    return out


@dataclass
class ReconstructedSpec:
    spec: dict
    mixed_gates: bool = False
    notes: list[str] = field(default_factory=list)
    exact: bool = False   # True when loaded from an exact spec sidecar (not inferred)


def content_hash(state: dict, wiring: dict) -> str:
    """Stable sha256 of a chip's parsed state+wiring — keys the spec sidecar so a
    chip edited out-of-band invalidates a stale sidecar (hash mismatch => ignore).
    """
    blob = json.dumps({"state": state, "wiring": wiring},
                      sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def write_spec_sidecar(folder: Path | str, spec: dict, state: dict, wiring: dict) -> None:
    """Write the exact ``spec`` to ``<folder>/.regen/generate_spec.json`` keyed by
    the chip's content hash. Best-effort — never raises (a sidecar miss just means
    the next re-generate falls back to reconstruction)."""
    try:
        d = Path(folder) / _SIDECAR_DIR
        d.mkdir(parents=True, exist_ok=True)
        payload = {"content_hash": content_hash(state, wiring), "spec": spec}
        (d / _SIDECAR_FILE).write_text(
            json.dumps(payload, indent=2), encoding="utf-8")
    except OSError:
        pass


def load_spec_sidecar(folder: Path | str, state: dict, wiring: dict) -> dict | None:
    """Return the exact spec from the sidecar iff it exists AND its hash matches
    the chip's CURRENT state+wiring (so an out-of-band edit falls back to a fresh
    reconstruction). Returns None otherwise."""
    try:
        p = Path(folder) / _SIDECAR_DIR / _SIDECAR_FILE
        if not p.is_file():
            return None
        payload = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None
        if payload.get("content_hash") != content_hash(state, wiring):
            return None                       # stale sidecar — chip changed
        spec = payload.get("spec")
        return spec if isinstance(spec, dict) else None
    except (OSError, ValueError):
        return None


def reconstruct_spec(state: dict, wiring: dict) -> ReconstructedSpec:
    """Best-effort spec from a chip's ``state`` + ``wiring`` dicts."""
    wire = wiring.get("wiring", {})
    net = wiring.get("network", {}) or {}
    notes: list[str] = []

    fems: dict[int, set[tuple[int, str]]] = defaultdict(set)

    def note_fem(cat: str, con: int, slot: int) -> None:
        ft = _fem_type(cat)
        if ft:
            fems[con].add((slot, ft))

    lines: list[dict] = []

    # Resonators: group qubits sharing one output port (multiplexed feedline).
    res_groups: dict[tuple, list[str]] = defaultdict(list)
    for q, ch in (wire.get("qubits") or {}).items():
        # `or {}` (not a .get default): real chips carry channels EXPLICITLY
        # null'd ("rr": null — Explorer nulling / hand edits produce it), and
        # .get's default never fires on a present-but-None key.
        rr = (ch.get("rr") or {}) if isinstance(ch, dict) else {}
        o = _parse_port(rr.get("opx_output"))
        i = _parse_port(rr.get("opx_input"))
        if not o:
            continue
        note_fem(*o[:1], o[1], o[2])
        if i:
            note_fem(i[0], i[1], i[2])
        res_groups[(o[1], o[2], o[3], i[3] if i else None)].append(q)
    for gi, ((con, slot, oport, iport), qs) in enumerate(res_groups.items(), 1):
        for q in qs:
            lines.append({"element": q, "line": "resonator", "group": f"feedline{gi}",
                          "channel": {"kind": "mw_fem", "con": con, "slot": slot,
                                      "in_port": iport, "out_port": oport}})

    xy_ports: dict = {}          # qubit -> parsed xy port (shared-port detection)
    for q, ch in (wire.get("qubits") or {}).items():
        if not isinstance(ch, dict):
            continue
        p = _parse_port((ch.get("xy") or {}).get("opx_output"))
        if p:
            xy_ports[q] = p
            note_fem(p[0], p[1], p[2])
            lines.append({"element": q, "line": "drive",
                          "channel": {"kind": "mw_fem", "con": p[1], "slot": p[2], "out_port": p[3]}})
        p = _parse_port((ch.get("z") or {}).get("opx_output"))
        if p:
            note_fem(p[0], p[1], p[2])
            lines.append({"element": q, "line": "flux",
                          "channel": {"kind": "lf_fem", "con": p[1], "slot": p[2], "out_port": p[3]}})

    # Pairs come from STATE (authoritative — EVERY pair, regardless of gate),
    # not from wiring: a fixed-coupler / CR chip has no coupler wiring channel,
    # so reading pairs off wiring would miss them entirely. The coupler wiring
    # constraint (tunable-coupler chips only) is pulled from wiring when present.
    #
    # Membership gate: real chips carry DANGLING pairs — a cut-down layout
    # keeps qubit_pairs entries whose member qubit was removed (deviceC ships
    # qB3-qA4 / qD4-qA3 against 15 real qubits). The tail-split below would
    # happily emit the phantom name, and a spec.qubit_pairs member outside
    # spec.qubits hard-blocks BOTH the wizard's step-4 gate and validate_spec
    # (there is nothing to build the pair against). Drop such pairs here with
    # a visible note — the compare engine's pair_orphans treats the same data
    # the same way — and keep their ids so the populate overrides extracted
    # later are dropped too, not silently carried under a phantom key.
    # Adaptive qubit inventory (r16, docs/72): wiring-first ORDER (ports
    # allocate in wiring order) but UNION with state — a user-trimmed wiring
    # must not silently drop a state qubit (and, through the membership gate
    # below, its pairs). State-only qubits carry no pinned lines; the
    # allocator assigns their ports fresh on build.
    wire_qs = list((wire.get("qubits") or {}).keys())
    _wire_set = set(wire_qs)
    state_qs = list((state.get("qubits") or {}).keys())
    _state_set = set(state_qs)
    qubit_names = wire_qs + [q for q in state_qs if q not in _wire_set]
    if _wire_set:                       # partial trim — say what differs
        for q in qubit_names:
            if q not in _wire_set:
                notes.append(f"qubit {q!r} has no wiring channels — its "
                             "ports will be auto-allocated on build.")
            elif _state_set and q not in _state_set:
                notes.append(f"qubit {q!r} exists only in wiring — no state "
                             "entry; the rebuild creates it with defaults.")
    known_qubits = set(qubit_names)
    dropped_pairs: set[str] = set()
    pairs: list[list[str]] = []
    cr_total = 0                 # CR lines seen / sharing the control xy port
    cr_shared = 0
    wire_pairs = wire.get("qubit_pairs") or {}
    for pid, p in (state.get("qubit_pairs") or {}).items():
        if not isinstance(p, dict):
            continue
        ctrl = str(p.get("qubit_control", "")).split("/")[-1]
        tgt = str(p.get("qubit_target", "")).split("/")[-1]
        wp = wire_pairs.get(pid, {}) if isinstance(wire_pairs.get(pid), dict) else {}
        c = (wp.get("c") or {}) if isinstance(wp, dict) else {}
        if not ctrl:
            ctrl = str(c.get("control_qubit", "")).split("/")[-1]
        if not tgt:
            tgt = str(c.get("target_qubit", "")).split("/")[-1]
        if not (ctrl and tgt):
            notes.append(f"pair {pid!r}: could not read control/target qubits")
            continue
        missing = [q for q in (ctrl, tgt) if q not in known_qubits]
        if missing:
            dropped_pairs.add(pid)
            notes.append(f"pair {pid!r} dropped — references qubit(s) not on "
                         f"this chip: {', '.join(missing)}")
            continue
        pairs.append([ctrl, tgt])
        cp = _parse_port(c.get("opx_output")) if c else None
        if cp:                                       # tunable coupler → pin the coupler line
            note_fem(cp[0], cp[1], cp[2])
            lines.append({"element": f"{ctrl}-{tgt}", "line": "coupler",
                          "channel": {"kind": "lf_fem", "con": cp[1], "slot": cp[2], "out_port": cp[3]}})

        # CR / ZZ drive lines (docs/54): wiring keys are the WiringLineType
        # values 'cr' / 'zz'. Each is pinned to its stored MW port — this is
        # THE inversion the old coupler-only parse dropped, which cascaded
        # into a rebuild with zero pairs (CR pairs exist only via their
        # wiring lines) and every CR calibration in residual_lost.
        for wkey, ltype in (("cr", "cross_resonance"), ("zz", "zz_drive")):
            chd = wp.get(wkey) if isinstance(wp.get(wkey), dict) else None
            if not chd:
                continue
            pp = _parse_port(chd.get("opx_output"))
            if pp:
                note_fem(pp[0], pp[1], pp[2])
                lines.append({"element": f"{ctrl}-{tgt}", "line": ltype,
                              "channel": {"kind": "mw_fem", "con": pp[1],
                                          "slot": pp[2], "out_port": pp[3]}})
                if wkey == "cr":
                    cr_total += 1
                    if xy_ports.get(ctrl) == pp:
                        cr_shared += 1
            else:
                lines.append({"element": f"{ctrl}-{tgt}", "line": ltype,
                              "channel": None})
                notes.append(f"pair {pid!r}: {ltype} line has no parseable "
                             "port — left unpinned (the allocator will pick)")

    # TWPAs: modern quam_builder builds them natively (Connectivity.add_twpa_lines),
    # so pin each pump line from the source wiring instead of losing them. The pump
    # constraint seeds pump + pump_ on one MW port; an optional isolation port maps
    # to a twpa_isolation line. (Older builders without add_twpa_lines skip these
    # with a warning — see run_build.build_connectivity.)
    # Emitted as WIZARD-NATIVE objects {"id", "qubits"} — the step-4 TWPA rows
    # bind `twpa.id`/`twpa.qubits`; bare-string ids rendered as broken empty
    # rows there ("Every TWPA needs an id" — the review-r6 TWPA-loss report).
    # validate_spec + run_build accept both shapes, so old sidecars stay valid.
    twpa_ids: list[dict] = []
    state_twpas = state.get("twpas")
    if not isinstance(state_twpas, dict):     # real chips ship "twpas": []
        state_twpas = {}
    for tid, ch in (wire.get("twpas") or {}).items():
        if not isinstance(ch, dict):
            continue
        st = state_twpas.get(tid) if isinstance(state_twpas.get(tid), dict) else {}
        qlist = st.get("qubits")
        twpa_ids.append({"id": tid,
                         "qubits": list(qlist) if isinstance(qlist, list) else []})
        # quam_builder 0.4.0 / qualang_tools 0.22 write the short line keys
        # "p"/"i" (same convention as qubit "rr"/"xy"/"z"); older stacks
        # wrote "pump"/"isolation". Accept both or the TWPA is silently
        # lost on re-generate.
        pump_ch = ch.get("pump") if isinstance(ch.get("pump"), dict) else (
            ch.get("p") if isinstance(ch.get("p"), dict) else None)
        pump = _parse_port(pump_ch.get("opx_output")) if pump_ch else None
        if pump:
            note_fem(pump[0], pump[1], pump[2])
            lines.append({"element": tid, "line": "twpa_pump",
                          "channel": {"kind": "mw_fem", "con": pump[1], "slot": pump[2], "out_port": pump[3]}})
        iso_ch = ch.get("isolation") if isinstance(ch.get("isolation"), dict) else (
            ch.get("i") if isinstance(ch.get("i"), dict) else None)
        iso = _parse_port(iso_ch.get("opx_output")) if iso_ch else None
        if iso:
            note_fem(iso[0], iso[1], iso[2])
            lines.append({"element": tid, "line": "twpa_isolation",
                          "channel": {"kind": "mw_fem", "con": iso[1], "slot": iso[2], "out_port": iso[3]}})

    pair_gate, mixed = _detect_pair_gate(state)
    if mixed:
        notes.append(f"chip uses multiple gate families; rebuilt with '{pair_gate}', "
                     "per-pair variants preserved by the merge graft.")

    qubits = qubit_names   # same wiring-first/state-fallback source as the gate above

    # Full populate extraction so the re-opened wizard's Populate step is
    # PRE-FILLED (RF · anharm · LO · FSP · grid, readout, flux), not blank —
    # inverts apply_populate. Also feeds grid_location to the chip board.
    merged = dict(state)
    merged["wiring"] = wiring.get("wiring", {})
    populate = _extract_populate(state, merged)
    if dropped_pairs and isinstance(populate.get("pairs"), dict):
        # A dropped pair's calibration overrides must not ride under a phantom
        # key (run_build would only warn-and-ignore them anyway). Buckets are
        # keyed by the canonical control-target id, so pop that too.
        for pid in dropped_pairs:
            populate["pairs"].pop(pid, None)
            pr = (state.get("qubit_pairs") or {}).get(pid)
            if isinstance(pr, dict):
                populate["pairs"].pop(_populate_pair_key(pid, pr, merged), None)
        if not populate["pairs"]:
            populate.pop("pairs")

    # Wiring-only pairs: a wiring.qubit_pairs entry with a live channel whose
    # state pair was deleted (partial edit) would otherwise vanish SILENTLY —
    # no line, no pair, no note — and the merge can never resurrect it
    # (entity collections are never resurrected). Recover when the channel's
    # own qubit pointers resolve; otherwise at least say so.
    seen_pids = set(state.get("qubit_pairs") or {})
    for pid, wp in wire_pairs.items():
        if pid in seen_pids or not isinstance(wp, dict):
            continue
        for wkey, ltype in (("c", "coupler"), ("cr", "cross_resonance"),
                            ("zz", "zz_drive")):
            chd = wp.get(wkey) if isinstance(wp.get(wkey), dict) else None
            if not chd:
                continue
            ctrl = str(chd.get("control_qubit", "")).split("/")[-1]
            tgt = str(chd.get("target_qubit", "")).split("/")[-1]
            if not (ctrl and tgt):
                notes.append(
                    f"pair {pid!r} exists only in wiring and its {ltype} "
                    "channel names no qubits — its port is NOT carried into "
                    "the rebuild.")
                continue
            if not {ctrl, tgt} <= known_qubits:
                # Same dangling gate as the state-pair loop — a wiring-only
                # pair naming a removed qubit must not ship a phantom either.
                notes.append(
                    f"pair {pid!r} exists only in wiring and references "
                    "qubit(s) not on this chip — dropped.")
                continue
            if [ctrl, tgt] not in pairs:
                pairs.append([ctrl, tgt])
            pp = _parse_port(chd.get("opx_output"))
            if pp:
                note_fem(pp[0], pp[1], pp[2])
                kind = "lf_fem" if ltype == "coupler" else "mw_fem"
                lines.append({"element": f"{ctrl}-{tgt}", "line": ltype,
                              "channel": {"kind": kind, "con": pp[1],
                                          "slot": pp[2], "out_port": pp[3]}})
            notes.append(
                f"pair {pid!r} existed only in wiring (state pair deleted) — "
                f"recovered its {ltype} line; the rebuild re-creates the pair "
                "with DEFAULT values (no calibration to merge).")

    # Adaptive FEM inventory (r16, docs/72): the channel scans above derive
    # FEMs ONLY from live channel pointers, so a user-trimmed wiring (a
    # channel deleted or nulled) silently dropped the whole FEM even though
    # the chip's ports section still declares it — the LabD-17Q "slot 7
    # disappeared" report: one z channel was that slot's only user. Union
    # the declared ports inventory in (state.json OR wiring.json may carry
    # "ports"); channel evidence wins slot-type conflicts. Assembled HERE —
    # after every note_fem caller including the wiring-only-pairs recovery
    # loop above, whose FEMs the old early assembly silently lost.
    for ports_root in (state.get("ports"), wiring.get("ports")):
        if not isinstance(ports_root, dict):
            continue
        for cat in ("mw_outputs", "mw_inputs", "analog_outputs", "analog_inputs"):
            cons = ports_root.get(cat)
            ft = _fem_type(cat)
            if not isinstance(cons, dict) or not ft:
                continue
            for con_key, slots in cons.items():
                m = re.match(r"con(\d+)$", str(con_key))
                if not m or not isinstance(slots, dict):
                    continue
                con_n = int(m.group(1))
                have = {s for s, _ in fems[con_n]}
                for slot_key in slots:            # keys are STRINGS ("7")
                    if not str(slot_key).isdigit():
                        continue                  # "__class__" and friends
                    slot_n = int(slot_key)
                    if slot_n in have:
                        continue                  # channel evidence owns it
                    fems[con_n].add((slot_n, ft))
                    have.add(slot_n)
                    notes.append(
                        f"slot con{con_n}/{slot_n} ({ft.upper()}-FEM) has no "
                        "channel pointer — kept from the ports inventory.")

    controllers = [{"con": con, "fems": [{"slot": s, "fem": ft} for s, ft in sorted(sl)]}
                   for con, sl in sorted(fems.items())]

    spec = {
        "network": {"host": net.get("host"), "cluster_name": net.get("cluster_name"),
                    "port": net.get("port")},
        "instruments": {"controllers": controllers, "opx_plus": [], "octaves": []},
        "qubits": qubits,
        "qubit_pairs": pairs,
        "twpas": twpa_ids,
        "lines": lines,
        "pair_gate": pair_gate,
        "populate": populate,   # pre-fills the wizard; merge still owns fidelity
    }
    # Shared-port detection (docs/54): when EVERY CR line rides its control's
    # xy port, the chip is the customer's dual-upconverter layout — record the
    # mode so a rebuild keeps the port plan (mixed layouts stay unset: the
    # explicit per-line pins above reproduce the ports either way, the mode
    # flag only changes unpinned behavior + capability requirements).
    if cr_total > 0 and cr_shared == cr_total:
        spec["cr_port_mode"] = "shared_xy"
    elif cr_shared > 0:
        notes.append(
            f"{cr_shared} of {cr_total} CR lines share their control's xy "
            "port — mixed layout; rebuilt from the explicit per-line pins.")
    return ReconstructedSpec(spec=spec, mixed_gates=mixed, notes=notes)
