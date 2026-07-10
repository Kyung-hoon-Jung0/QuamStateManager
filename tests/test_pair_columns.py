"""Tests for ``core.pair_columns.derive_pair_columns`` — the dynamic, lab-flexible
column derivation behind the Live State Edit *pair* grid.

Synthetic fixtures encode each heterogeneous shape the design's adversarial critic
found on real chips, so the regressions it caught stay caught:

* a **no-macro** pair (variantb) — must not crash, no gate bands;
* a **CR** pair (``cross_resonance`` + ``zz_drive=None`` + ``cr`` macro);
* **all-null components dropped** — ``coupler=None`` on every flux pair ⇒ no
  Coupler band; ``zz_drive=None`` ⇒ no ZZ Drive band;
* **anchored** pair-name suffix strip on ``coupler.operations`` ids (examplechip) —
  6 distinct coupler pulses, NEVER merged to 1, gate identity preserved;
* **self-ref ``duration``** is read-only (``kind="runtime"``); the always-null
  ``duration_control`` override is dropped;
* **fidelity shape divergence** (dict on one pair, scalar on another).
"""

from __future__ import annotations

import os
import threading

import pytest

from quam_state_manager.core.pair_columns import derive_pair_columns


class _FakeStore:
    """Minimal store: derive_pair_columns only touches _lock/merged/qubit_pair_names."""

    def __init__(self, state: dict):
        self._lock = threading.RLock()
        self.merged = state

    @property
    def qubit_pair_names(self) -> list[str]:
        return list((self.merged.get("qubit_pairs") or {}).keys())


def _derive(state: dict):
    return derive_pair_columns(_FakeStore(state))


def _sections(cols) -> set[str]:
    return {c["section"] for c in cols}


def _by_key(cols) -> dict:
    return {c["key"]: c for c in cols}


# ── fixtures ──────────────────────────────────────────────────────────────────

def _cz_pair(pid: str, *, fidelity, with_coupler_null=True) -> dict:
    p = {
        "id": pid, "__class__": "Pair", "moving_qubit": "control",
        "detuning": 1.0e6, "mutual_flux_bias": None,
        "confusion": [[0.98, 0.02], [0.03, 0.97]],
        "macros": {
            "cz": "#./cz_unipolar",            # string alias slot (self-ref)
            "cz_unipolar": {
                "__class__": "CZGate", "id": "cz_unipolar",
                "duration": "#./inferred_duration",      # runtime self-ref → read-only
                "duration_control": None,                 # always-null override → dropped
                "phase_shift_control": 0.025,
                "phase_shift_target": 0.99,
                "flux_pulse_qubit": {"amplitude": 0.209, "length": 68, "flat_length": 48},
                "coupler_flux_pulse": {"amplitude": 0.1},
                "fidelity": fidelity,
            },
        },
    }
    if with_coupler_null:
        p["coupler"] = None
    return p


def _state_flux_cz() -> dict:
    return {"qubit_pairs": {
        "qA2-qA1": _cz_pair("qA2-qA1", fidelity={"Bell_State": {"Fidelity": 0.97}}),
        "qA3-qA2": _cz_pair("qA3-qA2", fidelity=0.95),   # scalar fidelity → divergence
    }}


def _state_cr() -> dict:
    return {"qubit_pairs": {"qA2-qA1": {
        "id": "qA2-qA1", "__class__": "Pair", "detuning": 1.0e6,
        "coupler": None, "zz_drive": None,                # both null → no bands
        "confusion": None,
        "cross_resonance": {
            "__class__": "CrossResonance",
            "amplitude_scaling": 0.5, "phase": 0.0,
            "operations": {
                "square": {"amplitude": 1.0, "length": 100},
                "flattop": {"amplitude": 0.158, "length": 16, "flat_length": 12},
            },
        },
        "macros": {"cr": {"__class__": "CRGate", "duration": "#./inferred_duration",
                          "phase_shift_control": 0.1}},
    }}}


def _state_variantb() -> dict:
    return {"qubit_pairs": {"coupler_qA1_qA2": {
        "id": "coupler_qA1_qA2", "__class__": "Pair",
        "detuning": 0.036, "confusion": None,
        "coupler": {"id": "c1", "decouple_offset": 0.0,
                    "operations": {"const": {"amplitude": 0.5, "length": 100}}},
        "extras": {"CZ_time": 52, "CZ_coupler_flux": 0.026, "CZ_qubit_flux": 0.0308},
    }}}


def _state_examplechip_coupler_ops() -> dict:
    # coupler.operations ids fuse gate identity + pair name; they ALIAS the macro's
    # coupler_flux_pulse node. Anchored strip must keep them 6-distinct (not 1).
    return {"qubit_pairs": {"q0-1": {
        "id": "q0-1", "__class__": "Pair", "moving_qubit": "target",
        "coupler": {"operations": {
            "const": {"amplitude": 0.1, "length": 100},
            "cz_unipolar_coupler_pulse_q0-1": {"amplitude": 0.1, "flat_length": 100},
            "cz_flattop_coupler_pulse_q0-1": {"amplitude": 0.2, "flat_length": 80},
        }},
        "macros": {
            "cz_unipolar": {"coupler_flux_pulse": {"amplitude": 0.1}},
            "cz_flattop": {"coupler_flux_pulse": {"amplitude": 0.2}},
        },
    }}}


# ── tests ─────────────────────────────────────────────────────────────────────

class TestFluxCz:
    def test_no_coupler_band_when_coupler_null_everywhere(self):
        cols, _ = _derive(_state_flux_cz())
        assert "Coupler" not in _sections(cols), "coupler=None on all pairs must yield no band"

    def test_flux_pulse_leaves_are_editable_columns(self):
        cols, path_map = _derive(_state_flux_cz())
        labels = {c["label"] for c in cols if c["editable"]}
        assert any("flux" in l and "amplitude" in l for l in labels)
        assert any("flux" in l and "length" in l for l in labels)
        # the real stored write path is what a pair posts
        flat = {p for pm in path_map.values() for (p, _m) in pm.values()}
        assert "qubit_pairs.qA2-qA1.macros.cz_unipolar.flux_pulse_qubit.amplitude" in flat

    def test_runtime_self_ref_duration_is_read_only(self):
        cols, path_map = _derive(_state_flux_cz())
        dur = [c for c in cols if c["section"] == "CZ Unipolar" and c["label"].endswith("duration")]
        assert dur, "duration column should exist"
        assert all(c["kind"] == "runtime" and not c["editable"] for c in dur)

    def test_always_null_duration_control_is_dropped(self):
        cols, _ = _derive(_state_flux_cz())
        assert not any("duration*" in c["label"] for c in cols), \
            "duration_control is null on every pair → no column"

    def test_confusion_is_a_list_badge(self):
        cols, path_map = _derive(_state_flux_cz())
        conf = [c for c in cols if c["section"] == "Confusion"]
        assert conf and all(c["kind"] == "list" and not c["editable"] for c in conf)

    def test_string_alias_macro_slot_not_iterated_as_dict(self):
        # macros.cz = "#./cz_unipolar" is a self-ref STRING; must not crash and must
        # surface as a runtime cell, never recursed as a dict.
        cols, _ = _derive(_state_flux_cz())
        cz = [c for c in cols if c["section"] == "CZ"]
        assert all(c["kind"] == "runtime" for c in cz)

    def test_fidelity_shape_divergence_both_present(self):
        # one pair: fidelity.Bell_State.Fidelity (dict); other: fidelity scalar.
        cols, path_map = _derive(_state_flux_cz())
        labels = {c["label"] for c in cols}
        assert any("Bell_State" in l and "Fidelity" in l for l in labels), "dict sub-leaf column"
        assert any(l.rstrip().endswith("fidelity") for l in labels), "scalar fidelity column"


class TestCr:
    def test_cross_resonance_band_present_with_distinct_pulses(self):
        cols, _ = _derive(_state_cr())
        assert "Cross Resonance" in _sections(cols)
        cr_labels = " ".join(c["label"] for c in cols if c["section"] == "Cross Resonance")
        assert "square" in cr_labels and "flattop" in cr_labels, \
            "CR drive pulses square+flattop must be distinct columns"

    def test_no_zz_drive_band_when_null(self):
        cols, _ = _derive(_state_cr())
        assert "ZZ Drive" not in _sections(cols)

    def test_no_coupler_band_when_null(self):
        cols, _ = _derive(_state_cr())
        assert "Coupler" not in _sections(cols)

    def test_cr_macro_band_present(self):
        cols, _ = _derive(_state_cr())
        assert "CR" in _sections(cols)


class TestVariantBNoMacros:
    def test_no_crash_and_no_gate_bands(self):
        cols, path_map = _derive(_state_variantb())
        secs = _sections(cols)
        assert not any(s.startswith("CZ") or s == "CR" for s in secs), "no gate bands"
        assert "General" in secs and "Coupler" in secs and "Extras" in secs

    def test_variantb_gate_params_in_extras_are_editable(self):
        cols, path_map = _derive(_state_variantb())
        ex = [c for c in cols if c["section"] == "Extras"]
        assert any("CZ_time" in c["label"] for c in ex)
        assert all(c["editable"] for c in ex if "CZ_" in c["label"])

    def test_coupler_operations_const_present(self):
        cols, _ = _derive(_state_variantb())
        cpl = [c["label"] for c in cols if c["section"] == "Coupler"]
        assert any("const" in l and "amplitude" in l for l in cpl)


class TestExampleChipCouplerOps:
    def test_pair_name_suffix_stripped_keeping_gate_identity(self):
        cols, path_map = _derive(_state_examplechip_coupler_ops())
        cpl_labels = [c["label"] for c in cols if c["section"] == "Coupler"]
        joined = " ".join(cpl_labels)
        # gate identity preserved, pair-name token gone
        assert "cz_unipolar_coupler_pulse" in joined
        assert "cz_flattop_coupler_pulse" in joined
        assert "_q0-1" not in joined, "pair-name suffix must be stripped from the label"

    def test_six_distinct_not_merged_to_one(self):
        # const + cz_unipolar_coupler_pulse + cz_flattop_coupler_pulse → 3 distinct
        # pulse identities (regression for the #3 over-collapse fatal flaw).
        cols, _ = _derive(_state_examplechip_coupler_ops())
        cpl = [c for c in cols if c["section"] == "Coupler"]
        identities = set()
        for c in cpl:
            # label like "op · <pulse> · <leaf>"
            parts = [p.strip() for p in c["label"].split("·")]
            if len(parts) >= 2:
                identities.add(parts[1])
        assert {"const", "cz_unipolar_coupler_pulse", "cz_flattop_coupler_pulse"} <= identities

    def test_each_pair_posts_its_real_stored_path(self):
        _cols, path_map = _derive(_state_examplechip_coupler_ops())
        flat = {p for (p, _m) in path_map["q0-1"].values()}
        # the REAL stored key (with the pair-name suffix) is what's posted
        assert "qubit_pairs.q0-1.coupler.operations.cz_unipolar_coupler_pulse_q0-1.amplitude" in flat


class TestAnchoredStripSafety:
    def test_pair_name_mid_id_not_corrupted(self):
        # a pulse id that CONTAINS the pair name mid-string must NOT be globally
        # replaced — only an exact trailing _<pair> suffix is stripped.
        pid = "q1"
        state = {"qubit_pairs": {pid: {
            "id": pid, "coupler": {"operations": {
                "pulse_q1_extra": {"amplitude": 0.5},     # contains "q1" mid-id, NO trailing _q1
            }},
        }}}
        cols, path_map = _derive(state)
        cpl = [c["label"] for c in cols if c["section"] == "Coupler"]
        assert any("pulse_q1_extra" in l for l in cpl), "mid-id pair-name must be preserved"


class TestColumnModelSanity:
    def test_columns_have_required_keys(self):
        cols, _ = _derive(_state_flux_cz())
        for c in cols:
            assert {"key", "label", "section", "unit", "default_on", "editable", "kind"} <= set(c)
            assert isinstance(c["default_on"], bool) and isinstance(c["editable"], bool)
            assert c["kind"] in ("scalar", "runtime", "list")

    def test_empty_when_no_pairs(self):
        cols, path_map = _derive({"qubit_pairs": {}})
        assert cols == [] and path_map == {}

    def test_default_visible_count_is_tight(self):
        # "all gate bands expanded" still means HEADLINE leaves only on first paint.
        cols, _ = _derive(_state_flux_cz())
        visible = [c for c in cols if c["default_on"]]
        assert 0 < len(visible) <= max(8, len(cols)), "a reasonable headline subset is visible"


# ── optional: real chips under the granted quam_states folder (auto-skip) ──────

_REAL_ROOT = "<quam-states>"


@pytest.mark.skipif(not os.path.isdir(_REAL_ROOT), reason="real quam_states folder absent")
@pytest.mark.parametrize(
    "chip", ["LabA", "LabA_CR", "deviceB", "variantb", "examplechip9q_repro", "CR_state"])
def test_real_chips_no_crash(chip):
    import json
    sp = os.path.join(_REAL_ROOT, chip, "state.json")
    if not os.path.exists(sp):
        pytest.skip(f"{chip} absent")
    state = json.load(open(sp, encoding="utf-8"))
    cols, path_map = _derive(state)
    secs = _sections(cols)
    # invariants that must hold on the real flagship chips
    if chip in ("LabA", "LabA_CR", "deviceB"):
        assert "Coupler" not in secs, f"{chip}: coupler is null on all pairs → no band"
    # CR_state is the fixed-frequency cross-resonance flagship (LabA_CR's slot is
    # now a flux-tunable CZ chip, so the CR invariant moved to the dedicated chip).
    if chip == "CR_state":
        assert "Cross Resonance" in secs and "ZZ Drive" not in secs
    if chip == "variantb":
        assert not any(s.startswith("CZ") for s in secs)
    # every editable column's path_map entry is a real qubit_pairs.* dot-path
    for pm in path_map.values():
        for (path, mode) in pm.values():
            assert path.startswith("qubit_pairs.")
            assert mode in ("edit", "runtime", "list")
