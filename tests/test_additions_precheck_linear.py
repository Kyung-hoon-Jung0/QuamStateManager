"""Z5: the build's precheck is linear in the additions.

It used to re-derive every builder pair id (and scan every line) once per
addition, so a 300-addition spec on a chip with ~180 pairs spent its time in
``quam_pair_id``. The spec is read once now (``additions.SpecIndex``).
"""
from __future__ import annotations

import time

import pytest

from quam_state_manager.core import additions

_MAN = {"classes": {
    "labpkg.pulses.LabPulse": {"role": "pulse", "fields": {
        "amplitude": {"kind": "float", "has_default": True}}},
    "labpkg.gates.LabGate": {"role": "macro_pair", "fields": {
        "flux_pulse": {"kind": "pointer", "has_default": True}}},
}, "missing_classes": []}


def _big_spec(n_adds: int, n_q: int = 100) -> dict:
    """A chip of *n_q* qubits on a grid-like coupling graph (~1.8 pairs per
    qubit), every pair with a coupler line, and *n_adds* additions spread over
    flux pulses, coupler pulses and pair macros."""
    qs = [f"q{i}" for i in range(1, n_q + 1)]
    pairs = [[qs[i], qs[j]] for i in range(n_q) for j in (i + 1, i + 10) if j < n_q]
    lines = [{"line": ln, "element": q} for q in qs for ln in ("drive", "flux", "resonator")]
    lines += [{"line": "coupler", "element": f"{c}-{t}"} for c, t in pairs]
    adds = []
    for i in range(n_adds):
        c, t = pairs[i % len(pairs)]
        pid = additions.quam_pair_id([c, t])
        k = i % 3
        if k == 0:
            adds.append({"kind": "pulse", "target": f"qubits.{t}.z.operations", "name": f"cz_{i}",
                         "class": "labpkg.pulses.LabPulse", "fields": {"amplitude": 0.1}})
        elif k == 1:
            adds.append({"kind": "pulse", "target": f"qubit_pairs.{pid}.coupler.operations",
                         "name": f"cp_{i}", "class": "labpkg.pulses.LabPulse", "fields": {}})
        else:
            adds.append({"kind": "macro", "target": f"qubit_pairs.{pid}.macros", "name": f"g_{i}",
                         "class": "labpkg.gates.LabGate",
                         "fields": {"flux_pulse": f"#/qubits/{t}/z/operations/cz_{i - 2}"}})
    return {"qubits": qs, "qubit_pairs": pairs, "populate": {}, "lines": lines, "additions": adds}


def _scan_reason(spec: dict, target: str):
    """The pre-index scan (additions.py at 16b73ba2), kept as the reference."""
    from quam_state_manager.core import addition_cards
    segs = target.split(".")
    lines = [ln for ln in (spec.get("lines") or []) if isinstance(ln, dict)]

    def pair_reason(p):
        ids = {i for i in (additions.quam_pair_id(x) for x in spec.get("qubit_pairs") or []) if i}
        if p in ids:
            return None
        alias = additions.quam_pair_id(p) if "-" in p else None
        if alias and alias != p and alias in ids:
            return f"pair {p} is named {alias} in the built chip (the builder's id)"
        return f"pair {p} is not in this chip"
    if segs[0] == "qubits" and len(segs) == 3:
        if segs[1] not in {str(x) for x in spec.get("qubits") or []}:
            return f"qubit {segs[1]} is not in this chip"
        return None
    if segs[0] == "qubit_pairs" and len(segs) == 3:
        return pair_reason(segs[1])
    if segs[0] == "qubits" and len(segs) == 4:
        q, ch = segs[1], segs[2]
        if q not in {str(x) for x in spec.get("qubits") or []}:
            return f"qubit {q} is not in this chip"
        need = additions._QUBIT_CHANNEL.get(ch)
        qd = (((spec.get("qdac") or {}).get("qubits")) or {}).get(q)
        if ch == "z" and isinstance(qd, dict) and not qd.get("bias_tee"):
            rule = addition_cards.forbidden_reason(
                addition_cards.card_by_id("qdac_z_refusal", additions._deck()), "pulse_on_qdac_z") or (
                "its z is a QdacBiasLine (a DC level), which plays no pulses; put the pulse "
                "on xy or the resonator")
            return f"qubit {q} is biased by the QDAC-II: {rule}"
        if need and not any(ln.get("line") == need and str(ln.get("element")) == q for ln in lines):
            return f"qubit {q} has no {need} line, so it has no {ch} channel"
    elif segs[0] == "qubit_pairs" and len(segs) == 4:
        p, ch = segs[1], segs[2]
        why = pair_reason(p)
        if why:
            return why
        need = additions._PAIR_CHANNEL.get(ch)
        if need and not any(ln.get("line") == need and additions.quam_pair_id(ln.get("element")) == p
                            for ln in lines):
            return f"pair {p} has no {need} line"
    elif segs[0] == "twpas" and len(segs) == 4:
        t = segs[1]
        if t not in {str(x.get("id")) if isinstance(x, dict) else str(x) for x in spec.get("twpas") or []}:
            return f"TWPA {t} is not in this chip"
    return None


def _best_ms(spec: dict, runs: int = 5) -> float:
    additions.precheck(spec, _MAN)
    best = float("inf")
    for _ in range(runs):
        t = time.perf_counter()
        additions.precheck(spec, _MAN)
        best = min(best, (time.perf_counter() - t) * 1000)
    return best


def test_precheck_of_300_additions_is_fast():
    """Failing threshold: 40 ms for 300 additions on a 100-qubit, 180-pair
    chip. The quadratic version measured ~150 ms on this spec; the linear one
    a few ms (best of five, so a loaded machine does not flake it)."""
    spec = _big_spec(300)
    assert len(spec["qubit_pairs"]) >= 180
    ms = _best_ms(spec)
    assert ms < 40, f"precheck of 300 additions took {ms:.1f} ms"


def test_precheck_reads_the_pair_ids_once(monkeypatch):
    """The structural form of the same pin: the builder's pair-id rule runs
    once per pair and per coupler line, never once per addition."""
    spec = _big_spec(300)
    calls = {"n": 0}
    real = additions.quam_pair_id

    def counting(p):
        calls["n"] += 1
        return real(p)
    monkeypatch.setattr(additions, "quam_pair_id", counting)
    additions.precheck(spec, _MAN)
    n_pairs = len(spec["qubit_pairs"])
    assert calls["n"] <= 2 * n_pairs + 10, calls["n"]


def test_the_index_answers_what_the_scan_answered():
    """Same verdicts as the old per-addition scan, on every target shape the
    check knows (and a few it refuses)."""
    spec = _big_spec(30, n_q=12)
    spec["twpas"] = ["twpa1"]
    spec["qdac"] = {"qubits": {"q3": {"channel": 1}}}
    spec["lines"] = [ln for ln in spec["lines"] if ln["element"] not in ("q2", "q1-q2")]
    idx = additions.SpecIndex(spec)
    targets = ["qubits.q1.xy.operations", "qubits.q2.xy.operations", "qubits.q99.xy.operations",
               "qubits.q3.z.operations", "qubits.q1.macros", "qubits.q99.macros",
               "qubit_pairs.q1-2.macros", "qubit_pairs.q1-q2.macros", "qubit_pairs.q5-9.macros",
               "qubit_pairs.q1-2.coupler.operations", "qubit_pairs.q3-4.coupler.operations",
               "twpas.twpa1.pump.operations", "twpas.twpa9.pump.operations", "extras.x", "a.b.c.d.e"]
    for t in targets:
        assert additions.spec_channel_reason(spec, t, idx) == _scan_reason(spec, t), t
        assert additions.spec_channel_reason(spec, t) == _scan_reason(spec, t), t


@pytest.mark.parametrize("n", [300])
def test_precheck_is_unchanged_on_the_big_spec(n):
    """The refactor keeps every verdict: no addition of the synthetic spec is
    refused, and the order is placement order."""
    spec = _big_spec(n)
    out = additions.precheck(spec, _MAN)
    assert len(out) == n and not [e for e in out if e.get("reason")]
