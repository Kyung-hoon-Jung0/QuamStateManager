"""Unit tests for the value-preserving Re-generate merge (core/regen_merge.py).

Synthetic cases pin the 2-tier rules; a real-data test (auto-skipped when the
chip folder is absent) reproduces the P2 fidelity result: residual loss 0.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.core.regen_merge import (
    graft_twpa_wiring,
    merge_states,
    reconcile_twpa_ids,
)


def test_tier1_carries_calibrated_value_over_default():
    old = {"qubits": {"q1": {"f_01": 5.1e9, "T1": 4.2e-5}}}
    new = {"qubits": {"q1": {"f_01": 0.0, "T1": None}}}  # fresh defaults
    r = merge_states(old, new)
    assert r.merged["qubits"]["q1"]["f_01"] == 5.1e9
    assert r.merged["qubits"]["q1"]["T1"] == 4.2e-5
    assert r.stats.carried == 2
    assert r.stats.residual_lost == []


def test_new_pointer_wins_over_old_value():
    # NEW wiring pointer must survive (structure), even if OLD had a scalar there.
    old = {"qubits": {"q1": {"xy": {"opx_output": "#/ports/mw_outputs/con1/1/2"}}}}
    new = {"qubits": {"q1": {"xy": {"opx_output": "#/ports/mw_outputs/con1/1/9"}}}}
    r = merge_states(old, new)
    assert r.merged["qubits"]["q1"]["xy"]["opx_output"] == "#/ports/mw_outputs/con1/1/9"
    assert r.stats.kept_new_pointer == 1
    assert r.stats.carried == 0


def test_tier2_grafts_user_added_operation_subtree():
    old = {"qubits": {"q1": {"z": {"operations": {
        "cz_unipolar": {"length": 100, "amplitude": 0.1},
        "cz_flattop": {"length": 120, "amplitude": 0.2, "sigma": 5},  # user-added
    }}}}}
    new = {"qubits": {"q1": {"z": {"operations": {
        "cz_unipolar": {"length": 16, "amplitude": 0.0},
    }}}}}
    r = merge_states(old, new)
    ops = r.merged["qubits"]["q1"]["z"]["operations"]
    assert "cz_flattop" in ops                       # grafted wholesale
    assert ops["cz_flattop"] == {"length": 120, "amplitude": 0.2, "sigma": 5}
    assert ops["cz_unipolar"]["length"] == 100       # tier1 carried
    assert r.stats.grafted == 3
    assert r.stats.residual_lost == []


def test_removed_port_not_resurrected_but_user_op_still_grafts():
    """Regression: an OLD-only port slot (a removed qubit's now-unallocated port,
    which lives several levels deep under `ports`) must NOT be grafted back, while
    a user-added operation under a SURVIVING qubit still grafts. The old guard
    only matched the exact path 'ports', so deep port slots leaked through."""
    old = {
        "qubits": {"q1": {"xy": {"operations": {
            "x180": {"amplitude": 0.1},
            "my_custom": {"amplitude": 0.3},        # user-added op on a surviving qubit
        }}}},
        "ports": {"mw_outputs": {"con1": {"1": {
            "2": {"full_scale_power_dbm": -11},     # q1's port (survives the rebuild)
            "3": {"full_scale_power_dbm": -11},     # removed q2's port
        }}}},
    }
    new = {
        "qubits": {"q1": {"xy": {"operations": {
            "x180": {"amplitude": 0.0},
        }}}},
        "ports": {"mw_outputs": {"con1": {"1": {
            "2": {"full_scale_power_dbm": 0},       # rebuild kept only q1's port
        }}}},
    }
    r = merge_states(old, new)
    # The removed port slot .3 must NOT be resurrected (deep-path graft blocked).
    assert set(r.merged["ports"]["mw_outputs"]["con1"]["1"].keys()) == {"2"}
    # ...but the user-added op under the surviving qubit still grafts.
    assert "my_custom" in r.merged["qubits"]["q1"]["xy"]["operations"]


def test_residual_lost_when_structure_removed():
    # User dropped q2 in the rebuild -> its calibrated values have no home.
    old = {"qubits": {"q1": {"f_01": 5e9}, "q2": {"f_01": 6e9}}}
    new = {"qubits": {"q1": {"f_01": 0.0}}}
    r = merge_states(old, new)
    assert "qubits.q2.f_01" in r.stats.residual_lost
    assert r.merged["qubits"].keys() == {"q1"}       # q2 not resurrected


def test_new_only_leaf_kept_as_default():
    old = {"qubits": {"q1": {}}}
    new = {"qubits": {"q1": {"chi": -1.5e6}}}         # field the rebuild introduced
    r = merge_states(old, new)
    assert r.merged["qubits"]["q1"]["chi"] == -1.5e6
    assert r.stats.kept_new_only == 1


def test_pair_id_reconciliation_by_membership():
    # The builder may name a pair (qA2-A1) differently from the source (qA2-qA1)
    # while both reference the same qubits — merge must align on MEMBERSHIP and
    # adopt the source id, else every pair value orphans (real LabA bug).
    old = {"qubit_pairs": {"qA2-qA1": {
        "qubit_control": "#/qubits/qA2", "qubit_target": "#/qubits/qA1",
        "detuning": 12.5e6, "macros": {"cz": {"amplitude": 0.1}}}}}
    new = {"qubit_pairs": {"qA2-A1": {
        "qubit_control": "#/qubits/qA2", "qubit_target": "#/qubits/qA1",
        "detuning": 0.0, "macros": {"cz": {"amplitude": 0.0}}}}}
    r = merge_states(old, new)
    assert "qA2-qA1" in r.merged["qubit_pairs"]        # adopted the source id
    assert "qA2-A1" not in r.merged["qubit_pairs"]
    assert r.merged["qubit_pairs"]["qA2-qA1"]["detuning"] == 12.5e6   # carried
    assert r.stats.residual_lost == []                 # nothing orphaned


def test_twpas_preserved_when_rebuild_drops_them():
    # quam_builder can't build TWPAs, so every rebuild emits an empty twpas dict.
    # A missing TWPA is a builder gap, NOT a user removal -> the OLD twpas must be
    # grafted back wholesale (real LabA: 156 leaves would otherwise be lost).
    old = {"twpas": {"twpa1": {"frequency": 8e9, "gain": 20.0, "power": -5}},
           "active_twpa_names": ["twpa1"]}
    new = {"twpas": {}, "active_twpa_names": []}      # what build_quam produces
    r = merge_states(old, new)
    assert r.merged["twpas"]["twpa1"]["frequency"] == 8e9   # preserved
    assert r.stats.residual_lost == []                       # nothing lost
    # a genuinely removed qubit is still NOT resurrected (entity-collection guard)
    old2 = {"qubits": {"q1": {"f": 1}, "q2": {"f": 2}}}
    new2 = {"qubits": {"q1": {"f": 0}}}
    assert merge_states(old2, new2).merged["qubits"].keys() == {"q1"}


def test_removed_qubit_reference_dropped_from_a_carried_list():
    # QA F14: removing q2 left '#/qubits/q2' inside the TWPA's `qubits` LIST --
    # a list is a merge leaf, so tier-1 carried the old list whole over the
    # builder's null and the dangling scan (strings only) never looked inside.
    old = {"qubits": {"q1": {"f": 1}, "q2": {"f": 2}},
           "twpas": {"t": {"qubits": ["#/qubits/q1", "#/qubits/q2"]}}}
    new = {"qubits": {"q1": {"f": 0}},
           "twpas": {"t": {"qubits": None}}}           # TWPA(...).to_dict()
    r = merge_states(old, new)
    assert r.merged["twpas"]["t"]["qubits"] == ["#/qubits/q1"]
    refs = [x for x in r.stats.residual_lost if x.startswith("twpas.")]
    assert len(refs) == 1                            # reported, never silent
    # listed FIRST: the panel shows 80 lines and the removed qubit's own
    # leaves (hundreds on a real chip) would otherwise bury it
    assert r.stats.residual_lost[0] == refs[0]
    assert "twpas.t.qubits" in refs[0] and "#/qubits/q2" in refs[0]
    # the same when the whole TWPA is grafted (builder emitted no twpas)
    r2 = merge_states(old, {"qubits": {"q1": {"f": 0}}, "twpas": {}})
    assert r2.merged["twpas"]["t"]["qubits"] == ["#/qubits/q1"]
    assert any("#/qubits/q2" in x for x in r2.stats.residual_lost)


def test_surviving_list_references_carried_verbatim():
    # control: nothing removed -> the list is carried untouched, nothing reported;
    # a reference that was ALREADY broken in the source is not ours to edit.
    old = {"qubits": {"q1": {"f": 1}, "q2": {"f": 2}},
           "twpas": {"t": {"qubits": ["#/qubits/q1", "#/qubits/q2", "#/qubits/q9",
                                      "#/wiring/x", "plain"]}}}
    new = {"qubits": {"q1": {"f": 0}, "q2": {"f": 0}},
           "twpas": {"t": {"qubits": None}}}
    r = merge_states(old, new)
    assert r.merged["twpas"]["t"]["qubits"] == old["twpas"]["t"]["qubits"]
    assert r.stats.residual_lost == []


def test_dangling_graft_flagged():
    # A grafted macro points at a qubit the rebuild no longer has.
    old = {"qubit_pairs": {"p": {"macros": {"cz": {"ref": "#/qubits/q9/z"}}}}}
    new = {"qubit_pairs": {"p": {"macros": {}}}, "qubits": {"q1": {}}}
    r = merge_states(old, new)
    assert "qubit_pairs.p.macros.cz.ref" in r.stats.dangling_grafts


def test_prune_removes_unreferenced_broken_old_op():
    # OLD z op whose internal pointer targets a subtree the rebuild re-expressed:
    # it's grafted back, its pointer dangles, and nothing references it -> pruned.
    old = {"qubits": {"q1": {"z": {"operations": {
        "cz_unipolar_pulse_q2": {"length": "#/qubit_pairs/p/macros/cz/flux/length"}}}}},
        "qubit_pairs": {"p": {"macros": {}}}}
    new = {"qubits": {"q1": {"z": {"operations": {}}}},
           "qubit_pairs": {"p": {"macros": {}}}}
    r = merge_states(old, new)
    ops = r.merged["qubits"]["q1"]["z"]["operations"]
    assert "cz_unipolar_pulse_q2" not in ops          # pruned (broken + unreferenced)
    assert "qubits.q1.z.operations.cz_unipolar_pulse_q2" in r.stats.pruned_ops
    assert r.stats.dangling_grafts == []              # nothing left dangling


def test_prune_keeps_op_that_is_referenced():
    # a broken op that IS still referenced must NOT be pruned (would orphan the ref)
    old = {
        "qubits": {"q1": {"z": {"operations": {"op_a": {"ref": "#/nonexistent/x"}}}}},
        "linker": {"link": "#/qubits/q1/z/operations/op_a"},   # top-level ref -> grafts
    }
    new = {"qubits": {"q1": {"z": {"operations": {}}}}}
    r = merge_states(old, new)
    assert "op_a" in r.merged["qubits"]["q1"]["z"]["operations"]  # kept (referenced)
    assert r.stats.pruned_ops == []


def test_graft_twpa_wiring_carries_wiring_and_ports():
    # a preserved TWPA points through #/wiring/twpas -> #/ports; the rebuild has
    # neither, so we carry both (only filling absent keys).
    merged = {"twpas": {"twpaA": {"pump": {"opx_output": "#/wiring/twpas/twpaA/pump/opx_output"}}},
              "ports": {"mw_outputs": {"con1": {"1": {"2": {"band": 3}}}}}}  # port 8 absent
    old_state = {"ports": {"mw_outputs": {"con1": {"1": {"8": {"band": 3, "lo": 8e9}}}}}}
    old_wiring = {"wiring": {"twpas": {"twpaA": {"pump": {"opx_output": "#/ports/mw_outputs/con1/1/8"}}}}}
    new_wiring = {"wiring": {"qubits": {}}}                 # builder made no twpa wiring
    n = graft_twpa_wiring(merged, old_state, old_wiring, new_wiring)
    assert n == 1
    assert new_wiring["wiring"]["twpas"]["twpaA"]["pump"]["opx_output"] == "#/ports/mw_outputs/con1/1/8"
    assert merged["ports"]["mw_outputs"]["con1"]["1"]["8"]["lo"] == 8e9   # port carried
    assert merged["ports"]["mw_outputs"]["con1"]["1"]["2"]["band"] == 3   # existing kept


def test_graft_twpa_wiring_noop_without_twpas():
    assert graft_twpa_wiring({"qubits": {}}, {}, {"wiring": {}}, {"wiring": {}}) == 0


# --- real-data parity with the P2 probe (auto-skip when absent) -------------
_OLD = Path("<quam-states>/gen_2x3_cz_tunable/state.json")
_NEW = Path("/mnt/d/Work/state-manager/.tmp_p2/rebuilt/state.json")


@pytest.mark.skipif(not (_OLD.exists() and _NEW.exists()),
                    reason="real calibrated chip + rebuilt probe output not present")
def test_real_chip_zero_residual_loss():
    old = json.loads(_OLD.read_text())
    new = json.loads(_NEW.read_text())
    r = merge_states(old, new)
    assert r.stats.residual_lost == [], r.stats.residual_lost[:10]
    assert r.stats.carried > 500          # core calibration carried
    assert r.stats.grafted > 50           # user-added CZ variants grafted
    assert r.stats.dangling_grafts == []


# ---------------------------------------------------------------------------
# TWPA id reconciliation (review-r6: builder-generation id drift twpaA ⇄ A)
# ---------------------------------------------------------------------------

def _mismatch_fixture():
    old_state = {"twpas": {"twpaA": {"pump": {"opx_output": "#/wiring/twpas/twpaA/pump/opx_output"},
                                     "pump_frequency": 8.4e9}},
                 "active_twpa_names": ["twpaA"]}
    new_state = {"twpas": {"A": {"pump": {"opx_output": "#/wiring/twpas/A/p/opx_output"},
                                 "pump_frequency": None}},
                 "active_twpa_names": ["A"]}
    new_wiring = {"wiring": {"twpas": {"A": {"p": {"opx_output": "#/ports/mw_outputs/con1/1/8"}}}}}
    return old_state, new_state, new_wiring


def test_reconcile_twpa_ids_renames_new_onto_old():
    old_state, new_state, new_wiring = _mismatch_fixture()
    mapping = reconcile_twpa_ids(new_state, new_wiring, old_state)
    assert mapping == {"A": "twpaA"}
    # state + wiring keys renamed; internal pointers rewritten; membership too
    assert set(new_state["twpas"]) == {"twpaA"}
    assert new_state["twpas"]["twpaA"]["pump"]["opx_output"] == \
        "#/wiring/twpas/twpaA/p/opx_output"
    assert set(new_wiring["wiring"]["twpas"]) == {"twpaA"}
    assert new_state["active_twpa_names"] == ["twpaA"]


def test_reconcile_then_merge_no_zombie_no_dangling():
    old_state, new_state, new_wiring = _mismatch_fixture()
    reconcile_twpa_ids(new_state, new_wiring, old_state)
    result = merge_states(old_state, new_state)
    # ONE twpa (no zombie 'A'), old calibration carried onto the new structure
    assert set(result.merged["twpas"]) == {"twpaA"}
    assert result.merged["twpas"]["twpaA"]["pump_frequency"] == 8.4e9
    assert not any(p.startswith("twpas.") for p in result.stats.dangling_grafts)


def test_reconcile_identity_is_noop():
    # builder kept the full ids (the qualang_tools f"twpa{id}" round-trip) —
    # nothing renamed, nothing rewritten
    old_state = {"twpas": {"twpaA": {"pump_frequency": 1.0}}}
    new_state = {"twpas": {"twpaA": {"pump": {"opx_output": "#/wiring/twpas/twpaA/p/opx_output"}}}}
    new_wiring = {"wiring": {"twpas": {"twpaA": {"p": {"opx_output": "#/ports/mw_outputs/con1/1/8"}}}}}
    before = json.dumps([new_state, new_wiring], sort_keys=True)
    assert reconcile_twpa_ids(new_state, new_wiring, old_state) == {}
    assert json.dumps([new_state, new_wiring], sort_keys=True) == before


def test_reconcile_never_clobbers_an_existing_new_id():
    # pathological: NEW carries BOTH 'A' and 'twpaA' — renaming would clobber
    old_state = {"twpas": {"twpaA": {}}}
    new_state = {"twpas": {"A": {}, "twpaA": {}}}
    assert reconcile_twpa_ids(new_state, {"wiring": {}}, old_state) == {}
    assert set(new_state["twpas"]) == {"A", "twpaA"}


# ---------------------------------------------------------------------------
# Cross-generation schema gate (class_schemas)
# ---------------------------------------------------------------------------

CZGATE = ("quam_builder.architecture.superconducting.custom_gates."
          "flux_tunable_transmon_pair.two_qubit_gates.CZGate")
FTG = "quam.components.pulses._FlatTopGaussianPulse"


def test_schema_gate_drops_cross_generation_fields():
    """Incident repro (17Q regen, 2026-08): the OLD fork's CZGate serialized
    duration_control/moving_qubit (renamed/moved in quam_builder 0.4.0) and its
    _FlatTopGaussianPulse a sigma (removed in quam 0.6.0). With the build env's
    schemas known, those keys must NOT graft -- quam raises
    AttributeError('Unexpected attribute') on any field a class doesn't know."""
    old = {"qubit_pairs": {"q1-2": {"macros": {"cz": {
        "__class__": CZGATE, "phase_shift_control": 0.1,
        "duration_control": None, "moving_qubit": "control"}}}},
        "qubits": {"q1": {"z": {"operations": {"cz_flattop_pulse": {
            "__class__": FTG, "amplitude": 0.13, "sigma": 2.0}}}}}}
    new = {"qubit_pairs": {"q1-2": {"macros": {"cz": {
        "__class__": CZGATE, "phase_shift_control": 0.0,
        "duration_qubit": None}}}},
        "qubits": {"q1": {"z": {"operations": {"cz_flattop_pulse": {
            "__class__": FTG, "amplitude": 0.1, "smoothing_length": 20}}}}}}
    schemas = {
        CZGATE: ["phase_shift_control", "duration_qubit"],
        FTG: ["amplitude", "smoothing_length"],
    }
    r = merge_states(old, new, class_schemas=schemas)
    cz = r.merged["qubit_pairs"]["q1-2"]["macros"]["cz"]
    assert "duration_control" not in cz and "moving_qubit" not in cz
    assert cz["phase_shift_control"] == 0.1          # tier1 carry still works
    assert cz["duration_qubit"] is None              # NEW field kept
    op = r.merged["qubits"]["q1"]["z"]["operations"]["cz_flattop_pulse"]
    assert "sigma" not in op
    assert op["amplitude"] == 0.13
    assert r.stats.schema_dropped == [
        "qubit_pairs.q1-2.macros.cz.duration_control",
        "qubit_pairs.q1-2.macros.cz.moving_qubit",
        "qubits.q1.z.operations.cz_flattop_pulse.sigma",
    ]
    # dropped is deliberate and reported in its own counter -- never double-
    # counted as residual loss
    assert r.stats.residual_lost == []


def test_schema_gate_absent_keeps_legacy_graft():
    # No class_schemas (old build result / allocate / harvest failure) -> the
    # legacy unconditional graft, bit for bit.
    old = {"qubit_pairs": {"p": {"macros": {"cz": {
        "__class__": CZGATE, "duration_control": 42}}}}}
    new = {"qubit_pairs": {"p": {"macros": {"cz": {"__class__": CZGATE}}}}}
    r = merge_states(old, new)
    assert r.merged["qubit_pairs"]["p"]["macros"]["cz"]["duration_control"] == 42
    assert r.stats.schema_dropped == []


def test_schema_gate_unknown_class_falls_back_to_graft():
    # A class the build env couldn't import is absent from the map -> graft
    # (conservative: the gate only fires on classes it positively knows).
    old = {"qubits": {"q1": {"custom": {"__class__": "lab.Fork", "knob": 1}}}}
    new = {"qubits": {"q1": {"custom": {"__class__": "lab.Fork"}}}}
    r = merge_states(old, new, class_schemas={CZGATE: ["duration_qubit"]})
    assert r.merged["qubits"]["q1"]["custom"]["knob"] == 1
    assert r.stats.schema_dropped == []


def test_schema_gate_keeps_legit_field_the_build_omitted():
    # Same-generation regen where the fresh build simply didn't serialize an
    # optional field the old chip carries: the field IS in the schema -> graft.
    old = {"qubits": {"q1": {"xy": {"__class__": "quam.C", "opt_field": 7}}}}
    new = {"qubits": {"q1": {"xy": {"__class__": "quam.C"}}}}
    r = merge_states(old, new, class_schemas={"quam.C": ["opt_field"]})
    assert r.merged["qubits"]["q1"]["xy"]["opt_field"] == 7
    assert r.stats.schema_dropped == []


def test_schema_gate_user_added_op_still_grafts():
    # operations/macros/extras are plain dict CONTAINERS (no __class__): their
    # keys are user namespace, never schema fields. A user-added op grafts
    # wholesale even with schemas present.
    old = {"qubits": {"q1": {"z": {"operations": {
        "my_op": {"__class__": "lab.WeirdPulse", "amplitude": 0.3}}}}}}
    new = {"qubits": {"q1": {"z": {"operations": {}}}}}
    r = merge_states(old, new, class_schemas={FTG: ["amplitude"]})
    assert r.merged["qubits"]["q1"]["z"]["operations"]["my_op"]["amplitude"] == 0.3
    assert r.stats.schema_dropped == []


def test_schema_gate_extras_content_untouched():
    # extras is quam's designed junk drawer -- its inner keys always carry.
    old = {"qubits": {"q1": {"__class__": "quam.Q", "extras": {"lab_note": "x"}}}}
    new = {"qubits": {"q1": {"__class__": "quam.Q", "extras": {}}}}
    r = merge_states(old, new, class_schemas={"quam.Q": ["extras"]})
    assert r.merged["qubits"]["q1"]["extras"]["lab_note"] == "x"
    assert r.stats.schema_dropped == []


def test_package_versions_stamp_always_new():
    # quam's serialization stamp is an artifact, not calibration: the OLD one
    # must never carry over the NEW build's (it would lie about the writer),
    # and an OLD-only stamp neither grafts nor counts as residual loss.
    old = {"__class__": "quam.Root", "qubits": {"q1": {"f_01": 5.1e9}},
           "__package_versions__": {"quam": "0.5.0"}}
    new_with = {"__class__": "quam.Root", "qubits": {"q1": {"f_01": 0.0}},
                "__package_versions__": {"quam": "0.6.0"}}
    r = merge_states(old, new_with)
    assert r.merged["__package_versions__"] == {"quam": "0.6.0"}
    assert r.merged["qubits"]["q1"]["f_01"] == 5.1e9

    new_without = {"__class__": "quam.Root", "qubits": {"q1": {"f_01": 0.0}}}
    r2 = merge_states(old, new_without)
    assert "__package_versions__" not in r2.merged
    assert r2.stats.schema_dropped == []
    assert r2.stats.residual_lost == []


# ---------------------------------------------------------------------------
# Natural order (customer rule 2026-09-09). Every one of these lists is shown
# in the build-result transparency panel AND truncated there (regenerate.py
# slices them to 80/200), so the sort decides both the order the user reads
# and WHICH paths survive the cut. q2 before q10; a numeric port segment is a
# number, so ".2" before ".10".
# ---------------------------------------------------------------------------

class TestNaturalOrderedStats:
    _FTG = "quam_builder.architecture.components.pulses.FlatTopGaussianPulse"

    def test_residual_lost_counts_qubit_numbers_as_numbers(self):
        old = {"qubits": {f"q{n}": {"f_01": 5.1e9}
                          for n in (1, 2, 3, 9, 10, 11)}}
        new = {"qubits": {"q1": {"f_01": 0.0}}}
        r = merge_states(old, new)
        assert r.stats.residual_lost == [f"qubits.q{n}.f_01"
                                         for n in (2, 3, 9, 10, 11)]

    def test_residual_lost_counts_port_numbers_as_numbers(self):
        old = {"ports": {"mw_outputs": {"con1": {"1": {
            str(p): {"full_scale_power_dbm": -11} for p in (2, 3, 10, 11)}}}}}
        new = {"ports": {"mw_outputs": {}}}
        r = merge_states(old, new)
        assert r.stats.residual_lost == [
            f"ports.mw_outputs.con1.1.{p}.full_scale_power_dbm"
            for p in (2, 3, 10, 11)]

    def test_schema_dropped_counts_qubit_numbers_as_numbers(self):
        def _op(**extra):
            return {"z": {"operations": {"cz": dict(
                {"__class__": self._FTG, "amplitude": 0.1}, **extra)}}}
        qs = (2, 3, 10)
        old = {"qubits": {f"q{n}": _op(sigma=1.0) for n in qs}}
        new = {"qubits": {f"q{n}": _op() for n in qs}}
        r = merge_states(old, new, class_schemas={self._FTG: ["amplitude"]})
        assert r.stats.schema_dropped == [
            f"qubits.q{n}.z.operations.cz.sigma" for n in qs]

    def test_populate_protected_counts_qubit_numbers_as_numbers(self):
        qs = (2, 3, 10)
        old = {"qubits": {f"q{n}": {"f_01": 5.1e9} for n in qs}}
        new = {"qubits": {f"q{n}": {"f_01": 6.0e9} for n in qs}}
        protect = {f"qubits.q{n}.f_01" for n in qs}
        r = merge_states(old, new, protect_paths=protect)
        assert r.stats.populate_protected == [f"qubits.q{n}.f_01" for n in qs]

    def test_superseded_counts_qubit_numbers_as_numbers(self):
        qs = (2, 3, 10)
        old = {"qubits": {f"q{n}": {"z": {"operations": {
            "cz": {"amplitude": 0.1}}}} for n in qs}}
        new = {"qubits": {f"q{n}": {"z": {"operations": {
            "cz": "#/shared/cz"}}} for n in qs},
            "shared": {"cz": {"amplitude": 0.0}}}
        r = merge_states(old, new)
        assert r.stats.superseded == [
            f"qubits.q{n}.z.operations.cz.amplitude" for n in qs]
        assert r.stats.residual_lost == []

    def test_pruned_ops_counts_qubit_numbers_as_numbers(self):
        qs = (2, 3, 10)
        old = {"qubits": {f"q{n}": {"z": {"operations": {"cz_broken": {
            "length": "#/qubit_pairs/p/macros/cz/flux/length"}}}} for n in qs},
            "qubit_pairs": {"p": {"macros": {}}}}
        new = {"qubits": {f"q{n}": {"z": {"operations": {}}} for n in qs},
               "qubit_pairs": {"p": {"macros": {}}}}
        r = merge_states(old, new)
        assert r.stats.pruned_ops == [
            f"qubits.q{n}.z.operations.cz_broken" for n in qs]


# --- docs/202: a class substitution is the CAUSE the report never named ------

LAB_RO = "quam_config.complex_weights_pulse.ComplexWeightsReadoutPulse"
STOCK_RO = "quam.components.pulses.SquareReadoutPulse"


def test_a_class_substitution_is_recorded():
    """The customer case (KRS_5Q, 2026-09-18): the chip's readout pulse is the
    lab's OWN class declaring weights_real/weights_imag/ringdown_length, and a
    rebuild produces the stock SquareReadoutPulse -- because the build spec has
    no slot for a per-pulse class, so reconstruct_spec cannot carry it. The
    weights then drop through the schema gate, which IS reported; the class
    substitution that caused it was reported nowhere."""
    old = {"qubits": {"q1": {"resonator": {"operations": {"readout": {
        "__class__": LAB_RO, "amplitude": 0.1,
        "weights_real": [1.0, 2.0], "ringdown_length": 40}}}}}}
    new = {"qubits": {"q1": {"resonator": {"operations": {"readout": {
        "__class__": STOCK_RO, "amplitude": 0.0}}}}}}
    r = merge_states(old, new, class_schemas={STOCK_RO: ["amplitude"]})
    assert r.stats.class_changed == [
        ("qubits.q1.resonator.operations.readout", LAB_RO, STOCK_RO)]
    # the consequence is still reported, unchanged
    assert set(r.stats.schema_dropped) == {
        "qubits.q1.resonator.operations.readout.weights_real",
        "qubits.q1.resonator.operations.readout.ringdown_length"}


def test_every_dropped_field_sits_on_an_object_the_report_names():
    """The property that makes the new line worth showing: on the real chip all
    50 dropped paths sat on one of the 10 re-typed objects, so the class lines
    explain the whole drop. Pin the relationship, not the numbers."""
    qs = ("q1", "q2", "q3")
    old = {"qubits": {q: {"resonator": {"operations": {"readout": {
        "__class__": LAB_RO, "amplitude": 0.1, "weights_real": [1.0],
        "weights_imag": [2.0], "ringdown_length": 40}}}} for q in qs}}
    new = {"qubits": {q: {"resonator": {"operations": {"readout": {
        "__class__": STOCK_RO, "amplitude": 0.0}}}} for q in qs}}
    r = merge_states(old, new, class_schemas={STOCK_RO: ["amplitude"]})
    assert len(r.stats.schema_dropped) == 9          # 3 qubits x 3 lab fields
    retyped = {p for p, _, _ in r.stats.class_changed}
    assert len(retyped) == 3
    orphans = [p for p in r.stats.schema_dropped
               if p.rsplit(".", 1)[0] not in retyped]
    assert orphans == [], f"dropped with no class line to explain them: {orphans}"


def test_an_unchanged_class_is_never_reported_as_a_substitution():
    # Every merged object carries __class__; only a DIFFERENT one is news.
    old = {"qubits": {"q1": {"xy": {"__class__": "quam.X", "f": 1.0}}}}
    new = {"qubits": {"q1": {"xy": {"__class__": "quam.X", "f": 0.0}}}}
    r = merge_states(old, new)
    assert r.stats.class_changed == []


def test_a_version_stamp_move_is_not_a_class_substitution():
    """__package_versions__ shares the branch and moves on almost every rebuild,
    so reporting it would bury the real substitutions under noise.

    The string spelling is DELIBERATELY hostile: across the 46 real chips on
    this machine that key is always a dict at the root, so `isinstance(nv, str)`
    already excludes it and a fixture using the real shape cannot reach the
    state the `k == "__class__"` guard protects -- it passes with the guard
    deleted, which is a vacuous pin (docs/141 4af). Both spellings are pinned:
    the dict for what real data does, the string for what the guard is for."""
    real = ({"__package_versions__": {"quam": "0.5.0"},
             "qubits": {"q1": {"__class__": "quam.X"}}},
            {"__package_versions__": {"quam": "0.6.0"},
             "qubits": {"q1": {"__class__": "quam.X"}}})
    hostile = ({"__package_versions__": "0.5.0",
                "qubits": {"q1": {"__class__": "quam.X"}}},
               {"__package_versions__": "0.6.0",
                "qubits": {"q1": {"__class__": "quam.X"}}})
    for old, new in (real, hostile):
        assert merge_states(old, new).stats.class_changed == []


def test_a_root_class_substitution_is_named_too():
    # The root decides what the chip can contain (docs/176). It has no dot-path,
    # so it is reported under an explicit marker rather than an empty string.
    old = {"__class__": "quam_config.my_quam.Quam", "qubits": {}}
    new = {"__class__": "quam_builder.Quam", "qubits": {}}
    r = merge_states(old, new)
    assert r.stats.class_changed == [
        ("(root)", "quam_config.my_quam.Quam", "quam_builder.Quam")]


def test_a_pulse_the_rebuild_never_wrote_is_not_a_substitution():
    """Measured on the customer chip (2026-09-18): of its 24 lab-typed pulses,
    only the 10 READOUTS are re-typed. The 16 SNZ + 4 GaussianNZ CZ flux pulses
    come through the rebuild byte-identical -- class and all 11/13 keys -- because
    the builder does not write those operations at all, so tier-2 grafts them
    whole.

    This is why the report is a MEASUREMENT of the finished rebuild and not a
    prediction made from the source chip: a reconstruct-time warning written in
    the same session claimed all 24 would lose fields, and 20 of them do not."""
    lab_cz = "quam_config.two_flux_gate.SNZTwoFluxPulse"
    old = {"qubits": {"q1": {
        "resonator": {"operations": {"readout": {
            "__class__": LAB_RO, "amplitude": 0.1, "weights_real": [1.0]}}},
        # the builder writes no cz_SNZ_* operation, so this whole subtree grafts
        "z": {"operations": {"cz_SNZ_flux_pulse_q1_q2": {
            "__class__": lab_cz, "amplitude": 0.2, "t_snz": 12}}}}}}
    new = {"qubits": {"q1": {
        "resonator": {"operations": {"readout": {
            "__class__": STOCK_RO, "amplitude": 0.0}}},
        "z": {"operations": {}}}}}
    r = merge_states(old, new, class_schemas={STOCK_RO: ["amplitude"]})

    # the grafted CZ pulse kept its own class and every field
    cz = r.merged["qubits"]["q1"]["z"]["operations"]["cz_SNZ_flux_pulse_q1_q2"]
    assert cz["__class__"] == lab_cz and cz["t_snz"] == 12
    # ...and is reported as a graft, never as a substitution
    assert [p for p, _, _ in r.stats.class_changed] == [
        "qubits.q1.resonator.operations.readout"]
    assert any(p.startswith("qubits.q1.z.operations.cz_SNZ")
               for p, _ in r.stats.graft_subtrees)


# --- docs/202 §15: keep a lab subclass the build env can hold ----------------

LAB_GEF = "quam_config.gef_weights_pulse.GefWeightsReadoutPulse"


def _ro_chip(cls, **fields):
    return {"qubits": {"q1": {"resonator": {"operations": {"readout": dict(
        {"__class__": cls, "amplitude": 0.1}, **fields)}}}}}


KEEP = {LAB_RO: {"bases": [STOCK_RO, "quam.components.pulses.ReadoutPulse"],
                 "fields": ["amplitude", "weights_real", "weights_imag", "ringdown_length"]}}


def test_a_lab_subclass_the_env_holds_is_kept_with_its_fields():
    """The customer case, measured on the real chip: ComplexWeightsReadoutPulse
    subclasses the SquareReadoutPulse the builder writes, the build env imports
    it -- so the merge keeps it, and the optimized weights ride along."""
    old = _ro_chip(LAB_RO, weights_real=[1.0, 2.0], ringdown_length=40)
    new = _ro_chip(STOCK_RO, amplitude=0.0)
    r = merge_states(old, new, class_schemas={STOCK_RO: ["amplitude"]}, keep_classes=KEEP)
    ro = r.merged["qubits"]["q1"]["resonator"]["operations"]["readout"]
    assert ro["__class__"] == LAB_RO
    assert ro["weights_real"] == [1.0, 2.0] and ro["ringdown_length"] == 40
    assert ro["amplitude"] == 0.1                        # tier-1 carry unchanged
    assert r.stats.class_kept == [("qubits.q1.resonator.operations.readout", LAB_RO)]
    assert r.stats.class_changed == [] and r.stats.schema_dropped == []


def test_a_class_that_does_not_subclass_the_new_one_is_never_kept():
    """docs/136's poison: an old object of an unrelated class grafted into a
    slot typed for something else kills Quam.load(). Importable is not enough;
    the class the rebuild wrote must be in the old class's MRO."""
    unrelated = {LAB_RO: {"bases": ["quam.components.pulses.Pulse"],
                          "fields": ["amplitude", "weights_real"]}}
    old = _ro_chip(LAB_RO, weights_real=[1.0])
    new = _ro_chip(STOCK_RO)
    r = merge_states(old, new, class_schemas={STOCK_RO: ["amplitude"]}, keep_classes=unrelated)
    ro = r.merged["qubits"]["q1"]["resonator"]["operations"]["readout"]
    assert ro["__class__"] == STOCK_RO
    assert "weights_real" not in ro
    assert r.stats.class_kept == []
    assert [p for p, _, _ in r.stats.class_changed] == ["qubits.q1.resonator.operations.readout"]


def test_a_class_the_env_cannot_import_is_never_kept():
    old = _ro_chip(LAB_RO, weights_real=[1.0])
    new = _ro_chip(STOCK_RO)
    r = merge_states(old, new, class_schemas={STOCK_RO: ["amplitude"]},
                     keep_classes={"other.Class": {"bases": [STOCK_RO], "fields": []}})
    assert r.merged["qubits"]["q1"]["resonator"]["operations"]["readout"]["__class__"] == STOCK_RO
    assert r.stats.class_kept == []


def test_keeping_a_class_at_one_path_never_widens_the_value_gate():
    """`keep` only decides what is legal for the object being kept. The value
    gate (a graft whose VALUE has a class this build never wrote, under a
    tagged parent) still reads the build's own schemas -- so a lab class the
    env CAN import is still refused where the rebuild never put its base."""
    tagged = "quam.components.channels.IQChannel"
    old = {"qubits": {"q1": {"xy": {"__class__": tagged, "extra_pulse": {
        "__class__": LAB_RO, "weights_real": [1.0]}}}}}
    new = {"qubits": {"q1": {"xy": {"__class__": tagged}}}}
    r = merge_states(old, new, class_schemas={tagged: ["extra_pulse"]}, keep_classes=KEEP)
    assert "extra_pulse" not in r.merged["qubits"]["q1"]["xy"]
    assert "qubits.q1.xy.extra_pulse" in r.stats.schema_dropped


def test_a_subclass_of_a_subclass_is_kept_too():
    """GefWeightsReadoutPulse -> ComplexWeightsReadoutPulse -> SquareReadoutPulse
    on the real chip: the rule reads the whole MRO, not the first base."""
    keep = {LAB_GEF: {"bases": [LAB_RO, STOCK_RO], "fields": ["amplitude", "u_centers"]}}
    old = _ro_chip(LAB_GEF, u_centers=[0.1, 0.2])
    new = _ro_chip(STOCK_RO)
    r = merge_states(old, new, class_schemas={STOCK_RO: ["amplitude"]}, keep_classes=keep)
    ro = r.merged["qubits"]["q1"]["resonator"]["operations"]["readout"]
    assert ro["__class__"] == LAB_GEF and ro["u_centers"] == [0.1, 0.2]


LAB_ROOT = "quam_config.my_quam.Quam"
STOCK_ROOT = "quam_builder.architecture.superconducting.qpu.flux_tunable_quam.FluxTunableQuam"


def test_the_root_class_the_user_picked_is_never_overridden():
    """QA regenerate-r2-16: the Review step's "Chip root class" is the spec's
    own slot (docs/176) -- the user picked the stock root to share the chip
    without the lab package, the build wrote it, and the §15 keep restored the
    lab root anyway ("kept quam_config.my_quam.Quam -- 1 place"). Below the
    root the keep still applies."""
    keep = dict(KEEP)
    keep[LAB_ROOT] = {"bases": [STOCK_ROOT], "fields": ["qubits", "lab_extra"]}
    old = _ro_chip(LAB_RO, weights_real=[1.0, 2.0])
    old.update({"__class__": LAB_ROOT, "lab_extra": 7})
    new = _ro_chip(STOCK_RO)
    new["__class__"] = STOCK_ROOT
    r = merge_states(old, new, keep_classes=keep,
                     class_schemas={STOCK_ROOT: ["qubits"], STOCK_RO: ["amplitude"]})
    assert r.merged["__class__"] == STOCK_ROOT
    assert r.stats.class_changed == [("(root)", LAB_ROOT, STOCK_ROOT)]
    assert "lab_extra" in r.stats.schema_dropped
    assert r.stats.class_kept == [("qubits.q1.resonator.operations.readout", LAB_RO)]


# ---------------------------------------------------------------------------
# docs/202 §17 -- a declared port nothing references is carried
# ---------------------------------------------------------------------------

MW_OUT = "quam.components.ports.analog_outputs.MWFEMAnalogOutputPort"
MW_IN = "quam.components.ports.analog_inputs.MWFEMAnalogInputPort"


def _port(cls, fem, port, **kw):
    return {"controller_id": "con1", "fem_id": fem, "port_id": port,
            "__class__": cls, **kw}


def _chip_with_ports(outs, ins=None, refs=(), qubits=("q1",)):
    """A chip whose wiring points at the ``refs`` output ports; ``outs`` /
    ``ins`` are ``{fem: [port, ...]}``. Returns (state, wiring)."""
    ports = {"__class__": "quam.components.ports.ports_containers.FEMPortsContainer",
             "mw_outputs": {"con1": {str(f): {str(p): _port(MW_OUT, f, p, band=3,
                                                              full_scale_power_dbm=-11)
                                              for p in ps} for f, ps in outs.items()}}}
    if ins:
        ports["mw_inputs"] = {"con1": {str(f): {str(p): _port(MW_IN, f, p)
                                                for p in ps} for f, ps in ins.items()}}
    state = {"ports": ports, "qubits": {q: {"id": q} for q in qubits}}
    wiring = {"wiring": {"qubits": {
        q: {"xy": {"opx_output": f"#/ports/mw_outputs/con1/{f}/{p}"}}
        for q, (f, p) in zip(qubits, refs)}}}
    return state, wiring


def _merge_ports(old, new):
    return merge_states(old[0], new[0], old_wiring=old[1], new_wiring=new[1])


class TestADeclaredPortNothingUsesIsCarried:
    def test_the_customer_case_port_8_on_a_fem_the_rebuild_keeps(self):
        """KRS_5Q: ports 3/1..3/7 wired, 3/8 declared (band 3, -11 dBm, LO 7.6
        GHz) and pointed at by nothing. The rebuild has 3/1..3/7."""
        old = _chip_with_ports({3: [1, 8]}, refs=[(3, 1)])
        old[0]["ports"]["mw_outputs"]["con1"]["3"]["8"]["upconverter_frequency"] = 7.6e9
        new = _chip_with_ports({3: [1]}, refs=[(3, 1)])
        r = _merge_ports(old, new)
        p8 = r.merged["ports"]["mw_outputs"]["con1"]["3"]["8"]
        assert p8["upconverter_frequency"] == 7.6e9 and p8["__class__"] == MW_OUT
        assert r.stats.ports_carried == ["ports.mw_outputs.con1.3.8"]
        assert r.stats.residual_lost == []

    def test_a_removed_qubits_port_still_stays_removed(self):
        """The rule the graft block exists for: q2 was dropped in the wizard,
        its port was POINTED AT in the source, so it never comes back."""
        old = _chip_with_ports({3: [1, 2]}, refs=[(3, 1), (3, 2)], qubits=("q1", "q2"))
        new = _chip_with_ports({3: [1]}, refs=[(3, 1)])
        r = _merge_ports(old, new)
        assert set(r.merged["ports"]["mw_outputs"]["con1"]["3"]) == {"1"}
        assert r.stats.ports_carried == []
        assert "ports.mw_outputs.con1.3.2.band" in r.stats.residual_lost

    @pytest.mark.parametrize("how", ["relative pointer", "port list",
                                     "pointer at the whole slot", "state pointer"])
    def test_any_kind_of_reference_counts_as_used(self, how):
        old = _chip_with_ports({3: [1, 8]}, refs=[(3, 1)])
        if how == "relative pointer":            # extras.spare -> ../ports/...
            old[0]["extras"] = {"spare": "#../ports/mw_outputs/con1/3/8"}
        elif how == "port list":                 # the older [con, fem, port] form
            old[0]["qubits"]["q1"]["legacy_out"] = ["con1", 3, 8]
        elif how == "pointer at the whole slot":
            old[0]["extras"] = {"fem": "#/ports/mw_outputs/con1/3"}
        else:
            old[0]["qubits"]["q1"]["spare_out"] = "#/ports/mw_outputs/con1/3/8"
        new = _chip_with_ports({3: [1]}, refs=[(3, 1)])
        r = _merge_ports(old, new)
        assert "8" not in r.merged["ports"]["mw_outputs"]["con1"]["3"]
        assert r.stats.ports_carried == []

    def test_a_port_on_a_fem_the_rebuild_no_longer_uses_is_not_carried(self):
        """Carrying it would ask the config to program a slot that may not be
        in the rack any more."""
        old = _chip_with_ports({3: [1], 5: [8]}, refs=[(3, 1)])
        new = _chip_with_ports({3: [1]}, refs=[(3, 1)])
        r = _merge_ports(old, new)
        assert "5" not in r.merged["ports"]["mw_outputs"]["con1"]
        assert r.stats.ports_carried == []

    def test_a_fem_counts_as_used_through_any_port_type(self):
        """The rebuild's FEM 3 holds only OUTPUT ports; an unused INPUT port on
        that same FEM is on hardware the chip still has, so it comes along
        -- inside a container the rebuild never wrote at all."""
        old = _chip_with_ports({3: [1]}, ins={3: [8]}, refs=[(3, 1)])
        new = _chip_with_ports({3: [1]}, refs=[(3, 1)])
        r = _merge_ports(old, new)
        assert r.merged["ports"]["mw_inputs"]["con1"]["3"]["8"]["__class__"] == MW_IN
        assert r.stats.ports_carried == ["ports.mw_inputs.con1.3.8"]

    def test_a_carried_port_never_points_at_something_left_behind(self):
        """An unused input whose downconverter points at a port that is NOT
        coming (a removed qubit's) would carry a broken pointer into a config
        -- so it stays behind. Pointing at a port that IS coming is fine."""
        old = _chip_with_ports({3: [1, 2, 8]}, ins={3: [7, 8]},
                               refs=[(3, 1), (3, 2)], qubits=("q1", "q2"))
        ins = old[0]["ports"]["mw_inputs"]["con1"]["3"]
        ins["7"]["downconverter_frequency"] = "#/ports/mw_outputs/con1/3/2/upconverter_frequency"
        ins["8"]["downconverter_frequency"] = "#/ports/mw_outputs/con1/3/8/upconverter_frequency"
        old[0]["ports"]["mw_outputs"]["con1"]["3"]["8"]["upconverter_frequency"] = 7.6e9
        new = _chip_with_ports({3: [1]}, refs=[(3, 1)])
        r = _merge_ports(old, new)
        assert r.stats.ports_carried == ["ports.mw_inputs.con1.3.8",
                                         "ports.mw_outputs.con1.3.8"]
        assert "7" not in r.merged["ports"]["mw_inputs"]["con1"]["3"]
        assert r.stats.dangling_grafts == []

    def test_a_port_the_rebuild_already_has_is_merged_not_carried(self):
        old = _chip_with_ports({3: [1, 8]}, refs=[(3, 1)])
        new = _chip_with_ports({3: [1, 8]}, refs=[(3, 1)])
        r = _merge_ports(old, new)
        assert r.stats.ports_carried == []


# ---------------------------------------------------------------------------
# QA regenerate-r2-14 -- an object and a null never meet as tier-1 scalars
# ---------------------------------------------------------------------------

FLUX_Q = "quam_builder.architecture.superconducting.qubit.FluxTunableTransmon"
FLUX_LINE = "quam.components.channels.FluxLine"


def _cr_rebuild_of_a_flux_chip():
    """The customer case: a flux chip re-generated as cross-resonance. The
    rebuild still types q1 FluxTunableTransmon but has no flux wiring, so it
    serializes `z: null`; the pair's CR channel is NEW where OLD had null."""
    old = {"qubits": {"q1": {
        "__class__": FLUX_Q, "id": "q1",
        "z": {"__class__": FLUX_LINE, "joint_offset": 0.12,
              "opx_output": "#/wiring/qubits/q1/z/opx_output"}}},
        "qubit_pairs": {"q1-2": {"cross_resonance": None}}}
    new = {"qubits": {"q1": {"__class__": FLUX_Q, "id": "q1", "z": None}},
           "qubit_pairs": {"q1-2": {"cross_resonance": {
               "__class__": "quam_builder.CRChannel", "intermediate_frequency": 0,
               "opx_output": "#/wiring/qubit_pairs/q1-2/cr/opx_output"}}}}
    return old, new


class TestAnObjectNeverMeetsANullAsAScalar:
    def test_a_typed_object_over_a_new_null_goes_through_the_schema_gate(self):
        # QA review: this pin used to assert `qubits.q1.z in schema_dropped`,
        # which the panel prints as "old-stack field this env doesn't know" --
        # but `z` IS a field the env knows; the rebuild left it empty. It is
        # now ONE residual line saying so, and still no per-leaf loss.
        old, new = _cr_rebuild_of_a_flux_chip()
        r = merge_states(old, new, class_schemas={FLUX_Q: ["id", "z"]})
        assert r.merged["qubits"]["q1"]["z"] is None
        assert r.stats.schema_dropped == []
        assert [p for p, _ in r.stats.rebuild_removed] == ["qubits.q1.z"]
        assert r.stats.residual_lost == [
            "qubits.q1.z (the rebuild left it empty; old FluxLine not put "
            "back — this build writes no FluxLine)"]

    def test_one_qubits_removed_flux_line_is_not_put_back(self):
        """QA review of r2-14: the class gate only catches a class the rebuild
        writes NOWHERE. Drop q1's flux line while q2 keeps its own and
        FluxLine is in the schemas -- the old q1 line was grafted back
        pointing at wiring the rebuild never made (reported dangling, shipped
        anyway, generate_config() crash)."""
        line = lambda q, off: {"__class__": FLUX_LINE, "joint_offset": off,  # noqa: E731
                               "opx_output": f"#/wiring/qubits/{q}/z/opx_output"}
        old = {"qubits": {"q1": {"__class__": FLUX_Q, "z": line("q1", 0.12)},
                          "q2": {"__class__": FLUX_Q, "z": line("q2", 0.34)}}}
        new = {"qubits": {"q1": {"__class__": FLUX_Q, "z": None},
                          "q2": {"__class__": FLUX_Q, "z": line("q2", 0.0)}}}
        new_wiring = {"wiring": {"qubits": {"q2": {"z": {"opx_output": "#/ports/x"}}}}}
        schemas = {FLUX_Q: ["z"], FLUX_LINE: ["joint_offset", "opx_output"]}
        r = merge_states(old, new, class_schemas=schemas,
                         old_wiring={"wiring": {"qubits": {
                             "q1": {"z": {}}, "q2": {"z": {}}}}},
                         new_wiring=new_wiring)
        assert r.merged["qubits"]["q1"]["z"] is None
        assert r.merged["qubits"]["q2"]["z"]["joint_offset"] == 0.34   # tier-1
        assert r.stats.dangling_grafts == []
        assert all(p != "qubits.q1.z" for p, _ in r.stats.graft_subtrees)
        assert r.stats.grafted == 0
        assert r.stats.residual_lost == [
            "qubits.q1.z (the rebuild left it empty; old FluxLine not put back "
            "— #/wiring/qubits/q1/z/opx_output is not in the rebuild)"]

    def test_a_graft_whose_pointers_all_land_is_kept(self):
        # control: a user-added object on a field the builder leaves empty
        # (its wiring exists in the rebuild) still grafts, as docs/72 wants;
        # and a STATE-side pointer is judged even with no wiring given.
        obj = {"__class__": FLUX_LINE, "joint_offset": 0.5,
               "opx_output": "#/wiring/qubits/q1/z/opx_output"}
        old = {"qubits": {"q1": {"__class__": FLUX_Q, "z": obj}}}
        new = {"qubits": {"q1": {"__class__": FLUX_Q, "z": None}}}
        r = merge_states(old, new, new_wiring={"wiring": {"qubits": {"q1": {
            "z": {"opx_output": "#/ports/x"}}}}})
        assert r.merged["qubits"]["q1"]["z"] == obj
        assert r.stats.rebuild_removed == []
        gone = dict(obj, opx_output="#/ports/mw_outputs/con1/9/1")
        r = merge_states({"qubits": {"q1": {"__class__": FLUX_Q, "z": gone}}}, new)
        assert r.merged["qubits"]["q1"]["z"] is None
        assert [p for p, _ in r.stats.rebuild_removed] == ["qubits.q1.z"]

    def test_without_schemas_it_grafts_and_its_pointer_is_checked(self):
        old, new = _cr_rebuild_of_a_flux_chip()
        r = merge_states(old, new)
        assert r.merged["qubits"]["q1"]["z"]["joint_offset"] == 0.12
        assert ("qubits.q1.z", 2) in r.stats.graft_subtrees
        assert "qubits.q1.z.opx_output" in r.stats.dangling_grafts

    def test_an_old_null_never_erases_a_channel_the_rebuild_made(self):
        old, new = _cr_rebuild_of_a_flux_chip()
        r = merge_states(old, new, class_schemas={FLUX_Q: ["id", "z"]})
        cr = r.merged["qubit_pairs"]["q1-2"]["cross_resonance"]
        assert cr == new["qubit_pairs"]["q1-2"]["cross_resonance"]
        # the OLD null is no loss (the one line is q1's removed flux line)
        assert not any("cross_resonance" in x for x in r.stats.residual_lost)

    def test_a_scalar_over_a_null_still_carries(self):
        # tier-1 unchanged where neither side is an object
        r = merge_states({"q": {"T1": 4.2e-5, "arr": [1, 2]}},
                         {"q": {"T1": None, "arr": None}})
        assert r.merged["q"] == {"T1": 4.2e-5, "arr": [1, 2]}
