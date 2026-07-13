"""Unit tests for the value-preserving Re-generate merge (core/regen_merge.py).

Synthetic cases pin the 2-tier rules; a real-data test (auto-skipped when the
chip folder is absent) reproduces the P2 fidelity result: residual loss 0.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.core.regen_merge import graft_twpa_wiring, merge_states


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
