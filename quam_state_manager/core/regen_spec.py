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
        if pairv:
            pop_pairs[pid] = pairv

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
        rr = ch.get("rr", {}) if isinstance(ch, dict) else {}
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

    for q, ch in (wire.get("qubits") or {}).items():
        if not isinstance(ch, dict):
            continue
        p = _parse_port(ch.get("xy", {}).get("opx_output"))
        if p:
            note_fem(p[0], p[1], p[2])
            lines.append({"element": q, "line": "drive",
                          "channel": {"kind": "mw_fem", "con": p[1], "slot": p[2], "out_port": p[3]}})
        p = _parse_port(ch.get("z", {}).get("opx_output"))
        if p:
            note_fem(p[0], p[1], p[2])
            lines.append({"element": q, "line": "flux",
                          "channel": {"kind": "lf_fem", "con": p[1], "slot": p[2], "out_port": p[3]}})

    # Pairs come from STATE (authoritative — EVERY pair, regardless of gate),
    # not from wiring: a fixed-coupler / CR chip has no coupler wiring channel,
    # so reading pairs off wiring would miss them entirely. The coupler wiring
    # constraint (tunable-coupler chips only) is pulled from wiring when present.
    pairs: list[list[str]] = []
    wire_pairs = wire.get("qubit_pairs") or {}
    for pid, p in (state.get("qubit_pairs") or {}).items():
        if not isinstance(p, dict):
            continue
        ctrl = str(p.get("qubit_control", "")).split("/")[-1]
        tgt = str(p.get("qubit_target", "")).split("/")[-1]
        wp = wire_pairs.get(pid, {}) if isinstance(wire_pairs.get(pid), dict) else {}
        c = wp.get("c", {}) if isinstance(wp, dict) else {}
        if not ctrl:
            ctrl = str(c.get("control_qubit", "")).split("/")[-1]
        if not tgt:
            tgt = str(c.get("target_qubit", "")).split("/")[-1]
        if not (ctrl and tgt):
            notes.append(f"pair {pid!r}: could not read control/target qubits")
            continue
        pairs.append([ctrl, tgt])
        cp = _parse_port(c.get("opx_output")) if c else None
        if cp:                                       # tunable coupler → pin the coupler line
            note_fem(cp[0], cp[1], cp[2])
            lines.append({"element": f"{ctrl}-{tgt}", "line": "coupler",
                          "channel": {"kind": "lf_fem", "con": cp[1], "slot": cp[2], "out_port": cp[3]}})

    # TWPAs: modern quam_builder builds them natively (Connectivity.add_twpa_lines),
    # so pin each pump line from the source wiring instead of losing them. The pump
    # constraint seeds pump + pump_ on one MW port; an optional isolation port maps
    # to a twpa_isolation line. (Older builders without add_twpa_lines skip these
    # with a warning — see run_build.build_connectivity.)
    twpa_ids: list[str] = []
    for tid, ch in (wire.get("twpas") or {}).items():
        if not isinstance(ch, dict):
            continue
        twpa_ids.append(tid)
        pump = _parse_port((ch.get("pump") or {}).get("opx_output")) if isinstance(ch.get("pump"), dict) else None
        if pump:
            note_fem(pump[0], pump[1], pump[2])
            lines.append({"element": tid, "line": "twpa_pump",
                          "channel": {"kind": "mw_fem", "con": pump[1], "slot": pump[2], "out_port": pump[3]}})
        iso = _parse_port((ch.get("isolation") or {}).get("opx_output")) if isinstance(ch.get("isolation"), dict) else None
        if iso:
            note_fem(iso[0], iso[1], iso[2])
            lines.append({"element": tid, "line": "twpa_isolation",
                          "channel": {"kind": "mw_fem", "con": iso[1], "slot": iso[2], "out_port": iso[3]}})

    controllers = [{"con": con, "fems": [{"slot": s, "fem": ft} for s, ft in sorted(sl)]}
                   for con, sl in sorted(fems.items())]

    pair_gate, mixed = _detect_pair_gate(state)
    if mixed:
        notes.append(f"chip uses multiple gate families; rebuilt with '{pair_gate}', "
                     "per-pair variants preserved by the merge graft.")

    qubits = list((wire.get("qubits") or {}).keys()) or list((state.get("qubits") or {}).keys())

    # Full populate extraction so the re-opened wizard's Populate step is
    # PRE-FILLED (RF · anharm · LO · FSP · grid, readout, flux), not blank —
    # inverts apply_populate. Also feeds grid_location to the chip board.
    merged = dict(state)
    merged["wiring"] = wiring.get("wiring", {})
    populate = _extract_populate(state, merged)

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
    return ReconstructedSpec(spec=spec, mixed_gates=mixed, notes=notes)
