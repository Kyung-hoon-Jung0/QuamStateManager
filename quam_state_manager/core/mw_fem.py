"""OPX1000 MW-FEM band + LO-sharing model.

Per the QM docs (Guides/opx1000_fems): the MW-FEM up/downconverter LOs are shared
across fixed port pairs **per controller+FEM** — Out1↔In1, Out2↔Out3, Out4↔Out5,
Out6↔Out7, Out8↔In2. Coupled ports must use the **same band** (NOT the same
frequency); bands 1 and 3 are mutually compatible, band 2 is compatible only with
band 2. A port's up/downconverter frequency must lie within its band's Hz range.

This is the single source of truth for those constraints — both the server (to
attach per-cell LO metadata) and the client (live band-range warnings) read it.
Validation is **advisory** (warn, never hard-block) per the project's
trust-researcher-input philosophy.
"""

from __future__ import annotations

import re
from typing import Any, Optional

# Inclusive Hz range per band (QM docs, user-confirmed).
BANDS: dict[int, tuple[float, float]] = {
    1: (50e6, 5.5e9),
    2: (4.5e9, 7.5e9),
    3: (6.5e9, 10.5e9),
}

# Max |intermediate frequency| an MW-FEM output can synthesize around its
# upconverter LO. The QM stack asserts |IF| < 400 MHz for pair-drive (CR/ZZ)
# channels (the customer's populate scripts enforce exactly this bound);
# advisory here per the module's warn-never-block philosophy.
MW_MAX_ABS_IF_HZ: float = 400e6

# LO-coupled OUTPUT port pairs within one (controller, FEM).
_OUT_PAIRS = {2: 3, 3: 2, 4: 5, 5: 4, 6: 7, 7: 6}


def in_band(freq: Any, band: Any) -> bool:
    """True if *freq* is within *band*'s range (or band/freq unknown — never a
    false alarm)."""
    rng = BANDS.get(band)
    if rng is None or not isinstance(freq, (int, float)) or isinstance(freq, bool):
        return True
    return rng[0] <= freq <= rng[1]


def bands_of(freq: Any) -> list[int]:
    """The band(s) whose range contains *freq* (bands overlap, so >1 possible)."""
    if not isinstance(freq, (int, float)) or isinstance(freq, bool):
        return []
    return [b for b, (lo, hi) in BANDS.items() if lo <= freq <= hi]


def bands_compatible(b1: Any, b2: Any) -> bool:
    """Two LO-coupled ports' bands are compatible iff equal, or {1, 3}."""
    if b1 == b2:
        return True
    return {b1, b2} == {1, 3}


def lo_peer(kind: str, port_id: int) -> Optional[tuple[str, int]]:
    """``(peer_kind, peer_port_id)`` of the LO-coupled port within the same FEM,
    or ``None`` (e.g. an LF-FEM port or an unpaired id)."""
    if kind == "mw_outputs":
        if port_id in _OUT_PAIRS:
            return ("mw_outputs", _OUT_PAIRS[port_id])
        if port_id == 1:
            return ("mw_inputs", 1)
        if port_id == 8:
            return ("mw_inputs", 2)
    elif kind == "mw_inputs":
        if port_id == 1:
            return ("mw_outputs", 1)
        if port_id == 2:
            return ("mw_outputs", 8)
    return None


def freq_field(kind: str) -> str:
    """The frequency leaf field for a port *kind*."""
    return "downconverter_frequency" if kind == "mw_inputs" else "upconverter_frequency"


_PORT_RE = re.compile(r"^ports\.(mw_outputs|mw_inputs)\.([^.]+)\.([^.]+)\.([^.]+)(?:\.(.+))?$")


def port_of_resolved(resolved_path: Any) -> Optional[tuple[str, str, int, int, str]]:
    """Parse ``ports.mw_outputs.con1.1.2.band`` → ``(kind, controller, fem, port, field)``.

    Returns ``None`` for a non-MW-port path. fem/port are ints when numeric.
    """
    if not isinstance(resolved_path, str):
        return None
    m = _PORT_RE.match(resolved_path)
    if not m:
        return None
    kind, con, fem, port, field = m.group(1), m.group(2), m.group(3), m.group(4), (m.group(5) or "")

    def _int(x: str):
        try:
            return int(x)
        except (TypeError, ValueError):
            return x

    return (kind, con, _int(fem), _int(port), field)


# ── FSP → pulse-amplitude compensation (docs/20 r12-B) ──────────────────────

_FSP_LEAF = ".full_scale_power_dbm"


def _walk_dots(root: Any, dot_path: str) -> Any:
    node = root
    for seg in dot_path.split("."):
        if not isinstance(node, dict) or seg not in node:
            return None
        node = node[seg]
    return node


def fsp_compensation_plan(merged: dict, resolved_fsp_path: str,
                          new_fsp: Any) -> Optional[dict]:
    """The compensation OFFER for a ``full_scale_power_dbm`` edit.

    NEVER applied silently — the /field/edit(+batch) gates return this plan
    to the UI, which lists every amplitude old→new and asks; only an
    explicit ack commits (FSP + amps in one atomic batch, or FSP alone).

    Physics: ``P_dBm = FSP + 20·log10|amp|`` — keeping every pulse's real
    output power constant across an FSP change means
    ``amp' = amp · 10^((FSP_old − FSP_new)/20)`` (the identity autofit's
    power_rows pins bit-exact against real archives). Lowering FSP GROWS
    amplitudes; any ``|amp'| > 1.0`` clips at the DAC → per-row + top-level
    warnings.

    Channels are found by reverse-pointer traversal: the port node ←
    ``*.opx_output`` referrers, one extra hop through wiring nodes (the
    standard 2-hop ``qubits.qX.rr.opx_output → #/wiring/... → #/ports/...``
    chain). MW outputs only — LF flux/coupler amps are volts, never
    FSP-scaled. Alias operations (string pointers to a sibling op) are
    skipped silently (double-write guard); pointer/non-numeric amplitudes
    become disclosed ``skipped`` rows.

    Returns None when the path is not an MW-output FSP leaf, values are
    non-numeric, or the FSP is unchanged — the edit then proceeds normally.
    """
    if not isinstance(resolved_fsp_path, str)             or not resolved_fsp_path.endswith(_FSP_LEAF):
        return None
    parsed = port_of_resolved(resolved_fsp_path)
    if parsed is None or parsed[0] != "mw_outputs":
        return None
    _kind, con, fem, port, _field = parsed
    old = _walk_dots(merged, resolved_fsp_path)
    if isinstance(old, bool) or not isinstance(old, (int, float)):
        return None
    try:
        new = float(new_fsp)
    except (TypeError, ValueError):
        return None
    if float(old) == new:
        return None

    from quam_state_manager.core.pointer_resolver import is_pointer
    from quam_state_manager.core.pulse_index import build_reverse_pointer_index

    factor = 10.0 ** ((float(old) - new) / 20.0)
    port_node = resolved_fsp_path[:-len(_FSP_LEAF)]
    index = build_reverse_pointer_index(merged)

    channels: set = set()
    for ref in index.get(port_node, []):
        if not ref.endswith(".opx_output"):
            continue
        owner = ref[: -len(".opx_output")]
        if ref.startswith("wiring."):
            # 2-hop chain: channel.opx_output → wiring node → port
            for ref2 in index.get(ref, []):
                if ref2.endswith(".opx_output"):
                    channels.add(ref2[: -len(".opx_output")])
        else:
            channels.add(owner)

    amps: list[dict] = []
    skipped: list[dict] = []
    for chan in sorted(channels):
        ops = _walk_dots(merged, chan + ".operations")
        if not isinstance(ops, dict):
            continue
        for op_name, op_val in sorted(ops.items()):
            if isinstance(op_val, str):
                continue                     # alias op → target compensated once
            if not isinstance(op_val, dict) or "amplitude" not in op_val:
                continue
            amp = op_val["amplitude"]
            apath = f"{chan}.operations.{op_name}.amplitude"
            if is_pointer(amp):
                skipped.append({"path": apath,
                                "reason": "amplitude is a pointer — edit its target"})
                continue
            if isinstance(amp, bool) or not isinstance(amp, (int, float)):
                skipped.append({"path": apath, "reason": "not a number"})
                continue
            new_amp = float(amp) * factor
            amps.append({
                "path": apath,
                "channel": chan,
                "op": op_name,
                "old": amp,
                "new": new_amp,
                "clips": abs(new_amp) > 1.0,
            })

    range_warn = None
    try:
        from quam_state_manager.core.spec_constraints import (
            FULL_SCALE_POWER_DBM_RANGE)
        lo, hi = FULL_SCALE_POWER_DBM_RANGE
        if not (lo <= new <= hi):
            range_warn = (f"FSP {new:g} dBm is outside the hardware range "
                          f"[{lo}, {hi}]")
    except Exception:  # noqa: BLE001
        pass

    return {
        "fsp_path": resolved_fsp_path,
        "port": f"{con}/{fem}/{port}",
        "fsp_old": old,
        "fsp_new": new,
        "factor": factor,
        "amps": amps,
        "skipped": skipped,
        "clip_count": sum(1 for a in amps if a["clips"]),
        "range_warn": range_warn,
    }
