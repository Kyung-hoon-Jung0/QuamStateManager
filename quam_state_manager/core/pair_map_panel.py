"""Rows for the chip map's hover panel on the Pairs page.

Every number comes from the same readers the inspector and Chip Status use:
the topology edge (``QueryEngine.get_topology``: best 2Q fidelity, its gate,
its source, the per-metric rows with their RB ``level``) and the flat pair
dict (``QueryEngine.get_pair``: gate parameters). Levels are never mixed: a
per-Clifford number is always labelled per Clifford, a Bell number as Bell.
An absent value has no row; only "not measured" is shown, because a defined
gate without a fidelity is worth seeing.
"""

from __future__ import annotations

import math
from typing import Any

from quam_state_manager.core import units

# What the edge's headline number is (query.get_topology ``fidelity_source``).
_SOURCE_KIND = {
    "macro": "Bell state",
    "interleaved_rb": "per gate (IRB)",
    "channel": "Bell state (CR channel)",
}
# Short metric tags per RB level (query._RB_LEVEL); decay rows are skipped.
_LEVEL_TAG = {"gate": "IRB", "clifford": "SRB", "state": "Bell"}
_LEVEL_ORDER = {"gate": 0, "state": 1, "clifford": 2}


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _fid(v: float) -> str:
    return f"{v:.4f}"


def _variant_line(rows: list[dict]) -> str:
    parts = []
    for r in sorted(rows, key=lambda r: _LEVEL_ORDER.get(r.get("level"), 9)):
        lvl = r.get("level")
        if lvl not in _LEVEL_TAG or not _num(r.get("value")):
            continue
        txt = f"{_LEVEL_TAG[lvl]} {_fid(r['value'])}"
        if lvl == "clifford":
            txt += " /Clifford"
            g = r.get("derived_gate_fidelity")
            if _num(g):
                txt += f" ({_fid(g)} /gate)"
        parts.append(txt)
    return " · ".join(parts)


def pair_rows(edge: dict | None, pair: dict | None, qubit_f01: dict | None = None) -> list[list[str]]:
    """``[[label, value, note], ...]`` for one pair; ``note`` may be ``""``."""
    edge = edge or {}
    pair = pair or {}
    out: list[list[str]] = []
    best = edge.get("best_gate")
    fid = edge.get("cz_fidelity")
    if _num(fid):
        kind = _SOURCE_KIND.get(edge.get("fidelity_source") or "", "")
        note = " · ".join(x for x in (best, kind) if x)
        out.append(["Best 2Q fidelity", _fid(fid), note])
    elif edge.get("has_cz") or edge.get("gate_kind") in ("cz", "cr"):
        out.append(["2Q fidelity", "not measured", "a gate is defined, no fidelity stored"])

    by_gate: dict[str, list[dict]] = {}
    for r in edge.get("gate_fidelities") or []:
        if r.get("level") in _LEVEL_TAG and _num(r.get("value")):
            by_gate.setdefault(str(r.get("gate")), []).append(r)
    show_list = len(by_gate) > 1 or any(len(v) > 1 for v in by_gate.values()) \
        or (by_gate and not _num(fid))
    if show_list:
        for gate, rows in by_gate.items():
            line = _variant_line(rows)
            if line:
                out.append([gate, line, ""])

    mv = edge.get("moving_qubit") or pair.get("moving_qubit")
    if mv in ("control", "target"):
        q = pair.get("qubit_control" if mv == "control" else "qubit_target")
        out.append(["Moving", f"{mv} ({q})" if q else mv, ""])

    gate = best
    if not gate:
        names = [k[: -len("_amplitude")] for k in pair if k.endswith("_amplitude")
                 and not k.endswith("_coupler_amplitude")]
        gate = names[0] if len(names) == 1 else None
    if gate:
        for suffix, label, fmt in (
                ("amplitude", "amp", "{:.6f}"),
                ("coupler_amplitude", "coupler amp", "{:.6f}"),
                ("length", "length (ns)", "{:g}"),
                ("flat_length", "flat length (ns)", "{:g}"),
                ("phase_shift_control", "phase ctrl (turns)", "{:.4f}"),
                ("phase_shift_target", "phase tgt (turns)", "{:.4f}")):
            v = pair.get(f"{gate}_{suffix}")
            if _num(v):
                out.append([f"{gate} {label}", fmt.format(v), ""])

    for role in ("qubit_control", "qubit_target"):
        q = pair.get(role)
        f = (qubit_f01 or {}).get(q)
        if q and _num(f):
            out.append([f"{q} f_01 (GHz)", units.qty_filter(f, "f_01"), ""])

    det = edge.get("detuning_display")
    if pair.get("detuning") is not None and det:
        out.append(["Detuning", str(det), ""])
    if pair.get("is_active") is False:
        out.append(["Active", "off", "not in active_qubit_pair_names"])
    return out


def all_pair_rows(engine) -> dict[str, list[list[str]]]:
    """Panel rows for every pair of the chip, keyed by pair id."""
    topo = engine.get_topology()
    edges = {e.get("pair_id"): e for e in topo.get("edges") or []}
    f01: dict[str, Any] = {}
    for n in topo.get("nodes") or []:
        if _num(n.get("f_01")):
            f01[n.get("id")] = n["f_01"]
    out = {}
    for name in engine.store.qubit_pair_names:
        try:
            pair = engine.get_pair(name)
        except Exception:  # noqa: BLE001 -- the table shows it as an error row
            continue
        for q in (pair.get("qubit_control"), pair.get("qubit_target")):
            if q and q not in f01:
                try:
                    v = engine.get_qubit(q).get("f_01")
                except Exception:  # noqa: BLE001
                    v = None
                if _num(v):
                    f01[q] = v
        out[name] = pair_rows(edges.get(name), pair, f01)
    return out
