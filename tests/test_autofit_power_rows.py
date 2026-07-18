"""Coupled power rows — the figure-axis→state-value mapping verification
(docs/56 §6G, the user's CRITICAL item).

The rvp node's update is atomic across frequency + readout amplitude + the
SHARED feedline full_scale_power_dbm (+ power-preserving sibling rescales).
``power_rows.coupled_power_rows`` must reproduce that update exactly from the
node-authored numbers — and refuse honestly when they're absent.

Two tiers:
  * dummy-state unit tests (always run) — rescale math, feedline membership,
    every refusal branch, the amp>1 warning;
  * real-archive goldens (auto-skip off this workstation) — replaying the
    apply over the run's PRE-update state must land bit-exact on the node's
    own patches (KRISS #599, KRISS_CR #9 — incl. the >1.0 rescale the node
    itself wrote), and the target_*-less variants (#568/#565) plus qsvp (#12)
    must be refused, never approximated.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from quam_state_manager.core.autofit import families as fam_mod
from quam_state_manager.core.autofit import power_rows
from quam_state_manager.core.autofit.synth import patch_path_to_dotted

RVP = power_rows.POWER_COUPLED_FAMILY


# ---------------------------------------------------------------------------
# dummy-state tier
# ---------------------------------------------------------------------------

def _dummy_state():
    def qubit(amp):
        return {"resonator": {
            "f_01": 7.2e9, "RF_frequency": 7.2e9,
            "opx_output": "#/wiring/qubits/{q}/rr/opx_output",
            "operations": {"readout": {"amplitude": amp}},
        }}

    state = {
        "qubits": {"qA1": qubit(0.02), "qA2": qubit(0.5), "qA3": qubit(0.9),
                   "qB1": qubit(0.1)},
        "wiring": {"qubits": {}},
        "ports": {"mw_outputs": {"con1": {
            "1": {"1": {"full_scale_power_dbm": -5}},
            "2": {"1": {"full_scale_power_dbm": 13}},
        }}},
    }
    for q in ("qA1", "qA2", "qA3", "qB1"):
        line = "#/ports/mw_outputs/con1/1/1" if q.startswith("qA") \
            else "#/ports/mw_outputs/con1/2/1"
        state["qubits"][q]["resonator"]["opx_output"] = \
            f"#/wiring/qubits/{q}/rr/opx_output"
        state["wiring"]["qubits"][q] = {"rr": {"opx_output": line}}
    return state


def _fresh(amp=0.08, fsp=-11, line="con1/1/1", **extra):
    d = {"success": True, "resonator_frequency": 7.2001e9,
         "frequency_shift": 1e5, "optimal_power": fsp + 20 * math.log10(amp),
         "target_amplitude": amp, "target_full_scale_power_dbm": fsp,
         "readout_line": line}
    d.update(extra)
    return d


class TestCoupledRows:
    def test_fsp_move_rescales_every_feedline_sibling(self):
        state = _dummy_state()
        out = power_rows.coupled_power_rows(RVP, "qA1", _fresh(), state)
        assert out["skipped"] is None
        by_kind = {}
        for r in out["rows"]:
            by_kind.setdefault(r["kind"], []).append(r)
        assert [r["path"] for r in by_kind["power_amp"]] == \
            ["qubits.qA1.resonator.operations.readout.amplitude"]
        assert by_kind["power_amp"][0]["value"] == 0.08
        assert by_kind["power_fsp"][0]["path"] == \
            "ports.mw_outputs.con1.1.1.full_scale_power_dbm"
        assert by_kind["power_fsp"][0]["value"] == -11
        # −5 → −11: siblings rescale by 10**(6/20); qB1 (other line) untouched
        factor = 10.0 ** ((-5 - (-11)) / 20.0)
        resc = {r["path"]: r["value"] for r in by_kind["power_rescale"]}
        assert resc == {
            "qubits.qA2.resonator.operations.readout.amplitude": 0.5 * factor,
            "qubits.qA3.resonator.operations.readout.amplitude": 0.9 * factor,
        }
        assert 0.9 * factor > 1.0
        assert any("qA3" in w and "1.0" in w for w in out["warnings"])

    def test_fsp_unchanged_writes_only_the_target_amp(self):
        state = _dummy_state()
        out = power_rows.coupled_power_rows(RVP, "qA1", _fresh(fsp=-5), state)
        assert out["skipped"] is None
        assert [r["kind"] for r in out["rows"]] == ["power_amp"]

    def test_missing_node_authored_split_is_refused(self):
        state = _dummy_state()
        fresh = _fresh()
        for k in ("target_amplitude", "target_full_scale_power_dbm",
                  "readout_line"):
            fresh.pop(k)
        out = power_rows.coupled_power_rows(RVP, "qA1", fresh, state)
        assert out["rows"] == []
        assert "did not record" in out["skipped"]

    def test_readout_line_mismatch_is_refused(self):
        state = _dummy_state()
        out = power_rows.coupled_power_rows(
            RVP, "qA1", _fresh(line="con1/2/1"), state)
        assert out["rows"] == []
        assert "does not match" in out["skipped"]

    def test_qsvp_family_never_gets_power_rows(self):
        # the FSP identity provably fails for qubit-spec-vs-power (#12:
        # constant +3.98 dB offset) — refuse by family, even with target_*
        out = power_rows.coupled_power_rows(
            "qubit_spectroscopy_vs_power", "qA1", _fresh(), _dummy_state())
        assert out["rows"] == []
        assert "only defined" in out["skipped"]

    def test_pointer_valued_sibling_refuses_the_whole_block(self):
        state = _dummy_state()
        state["qubits"]["qA2"]["resonator"]["operations"]["readout"][
            "amplitude"] = "#/qubits/qA1/resonator/operations/readout/amplitude"
        out = power_rows.coupled_power_rows(RVP, "qA1", _fresh(), state)
        assert out["rows"] == []
        assert "qA2" in out["skipped"]

    def test_pointer_valued_target_amp_is_refused(self):
        state = _dummy_state()
        state["qubits"]["qA1"]["resonator"]["operations"]["readout"][
            "amplitude"] = "#./length"
        out = power_rows.coupled_power_rows(RVP, "qA1", _fresh(), state)
        assert out["rows"] == []
        assert "not a literal number" in out["skipped"]


# ---------------------------------------------------------------------------
# real-archive golden tier (auto-skips off this workstation)
# ---------------------------------------------------------------------------

_DS = Path("/mnt/d/work_laptop/dataset")
_A599 = _DS / "KRISS/2026-06-14/#599_1Q_05b_resonator_spectroscopy_vs_power_iq_191747"
_A568 = _DS / "KRISS/2026-06-14/#568_1Q_05b_resonator_spectroscopy_vs_power_iq_085222"
_A565 = _DS / "KRISS/2026-06-13/#565_1Q_05b_resonator_spectroscopy_vs_power_iq_221422"
_CR9 = _DS / "KRISS_CR/2026-06-17/#9_05b_resonator_spectroscopy_vs_power_iq_041114"
_CR12 = _DS / "KRISS_CR/2026-06-17/#12_08b_qubit_spectroscopy_vs_power_043256"

real = pytest.mark.skipif(not _DS.is_dir(),
                          reason="real archive not available")


def _get(state, dotted):
    node = state
    for part in dotted.split("."):
        node = node[part]
    return node


def _set(state, dotted, value):
    node = state
    parts = dotted.split(".")
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = value


def _load_run(run: Path):
    """(pre-update merged state, node dict, fit_results). The run's
    quam_state snapshot is POST-update — patches[].old rewinds it."""
    state = json.loads((run / "quam_state" / "state.json").read_text())
    wiring = json.loads((run / "quam_state" / "wiring.json").read_text())
    merged = dict(state)
    merged["wiring"] = wiring.get("wiring") or {}
    node = json.loads((run / "node.json").read_text())
    for p in node.get("patches") or []:
        if p.get("op", "replace") == "replace":
            _set(merged, patch_path_to_dotted(p["path"]), p["old"])
    data = json.loads((run / "data.json").read_text())
    return merged, node, data.get("fit_results") or {}


def _apply_all(run: Path, order=None):
    """Simulate clicking Apply fresh for every fitted qubit; returns
    (final state, node patches, collected warnings)."""
    merged, node, fits = _load_run(run)
    fam = fam_mod.FAMILIES[RVP]
    warnings = []
    qubits = order or list(fits)
    for q in qubits:
        fresh = fits[q]
        rows = fam_mod.resolve_updates(fam, q, dict(fresh), {},
                                       lambda d: _get(merged, d))
        pr = power_rows.coupled_power_rows(RVP, q, dict(fresh), merged)
        assert pr["skipped"] is None, f"{q}: {pr['skipped']}"
        warnings += pr["warnings"]
        for r in rows + pr["rows"]:
            _set(merged, r["path"], r["value"])
    return merged, node.get("patches") or [], warnings


def _assert_matches_node_patches(final, patches):
    for p in patches:
        if p.get("op", "replace") != "replace":
            continue
        dotted = patch_path_to_dotted(p["path"])
        got, want = _get(final, dotted), p["value"]
        if "amplitude" in dotted or "full_scale_power_dbm" in dotted:
            assert got == want, f"{dotted}: {got!r} != node's {want!r}"
        else:
            # frequency rows: families ASSIGN the absolute fit value while
            # the node INCREMENTS f_01/RF by frequency_shift — identical when
            # f_01 == RF at run, else off by the chip's standing sub-Hz
            # f_01−RF offset (the #563 1 Hz class; budget is kHz)
            assert got == pytest.approx(want, abs=2.0), \
                f"{dotted}: {got!r} vs node's {want!r}"


@real
class TestRealArchiveGoldens:
    def test_599_full_coupled_apply_equals_node_patches(self):
        """KRISS #599: FSP 13→−9, 5 fitted qubits, qA6 rescale >1.0 — the
        union of per-qubit applies must equal the node's own 15 patches,
        amp/FSP bit-exact (incl. qA6 amp 1.2056… the node itself wrote)."""
        final, patches, warnings = _apply_all(_A599)
        assert any("full_scale_power_dbm" in p["path"] for p in patches)
        _assert_matches_node_patches(final, patches)
        # the unfitted feedline member got the node's exact rescale
        qa6 = _get(final, "qubits.qA6.resonator.operations.readout.amplitude")
        assert qa6 > 1.0
        assert any("qA6" in w for w in warnings)

    def test_599_apply_order_does_not_matter(self):
        a, patches, _ = _apply_all(_A599)
        b, _, _ = _apply_all(_A599, order=list(reversed(
            list(json.loads((_A599 / "data.json").read_text())
                 ["fit_results"]))))
        for p in patches:
            if p.get("op", "replace") != "replace":
                continue
            dotted = patch_path_to_dotted(p["path"])
            assert _get(a, dotted) == _get(b, dotted), dotted

    def test_cr9_full_coupled_apply_equals_node_patches(self):
        """KRISS_CR #9 — same invariant on the second chip/campaign."""
        final, patches, _ = _apply_all(_CR9)
        assert any("full_scale_power_dbm" in p["path"] for p in patches)
        _assert_matches_node_patches(final, patches)

    @pytest.mark.parametrize("run", [_A568, _A565],
                             ids=["n568", "n565_3db_backoff"])
    def test_target_less_envelopes_are_refused_never_derived(self, run):
        """#568/#565: the node moved power but recorded no target_* split
        (#565 even used a −3 dB backoff no formula can recover) — power rows
        must refuse; frequency rows still resolve."""
        merged, _node, fits = _load_run(run)
        fam = fam_mod.FAMILIES[RVP]
        for q, fresh in fits.items():
            pr = power_rows.coupled_power_rows(RVP, q, dict(fresh), merged)
            assert pr["rows"] == []
            assert "did not record" in pr["skipped"]
            rows = fam_mod.resolve_updates(fam, q, dict(fresh), {},
                                           lambda d: _get(merged, d))
            assert len(rows) == 2          # f_01 + RF_frequency only

    def test_cr12_qsvp_power_is_never_applied(self):
        """#12: qsvp fit carries optimal_amplitude/optimal_power but the FSP
        identity does not hold (+3.98 dB offset) — family-level refusal."""
        state = json.loads((_CR12 / "quam_state" / "state.json").read_text())
        data = json.loads((_CR12 / "data.json").read_text())
        fam = fam_mod.family_for("08b_qubit_spectroscopy_vs_power")
        assert fam is not None and fam.key == "qubit_spectroscopy_vs_power"
        for q, fresh in (data.get("fit_results") or {}).items():
            pr = power_rows.coupled_power_rows(fam.key, q, dict(fresh), state)
            assert pr["rows"] == []
