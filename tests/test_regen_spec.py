"""Unit tests for spec reconstruction (core/regen_spec.py)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.core.regen_spec import reconstruct_spec
from quam_state_manager.core import config_generator


def _tiny():
    state = {
        "qubits": {"q1": {}, "q2": {}},
        "qubit_pairs": {"q1-2": {"coupler": {"x": 1}, "macros": {"cz_unipolar": {}}}},
    }
    wiring = {
        "network": {"host": "1.2.3.4", "cluster_name": "C", "port": None},
        "wiring": {
            "qubits": {
                "q1": {"rr": {"opx_input": "#/ports/mw_inputs/con1/1/1",
                              "opx_output": "#/ports/mw_outputs/con1/1/1"},
                       "xy": {"opx_output": "#/ports/mw_outputs/con1/1/2"},
                       "z": {"opx_output": "#/ports/analog_outputs/con1/4/1"}},
                "q2": {"rr": {"opx_input": "#/ports/mw_inputs/con1/1/1",
                              "opx_output": "#/ports/mw_outputs/con1/1/1"},
                       "xy": {"opx_output": "#/ports/mw_outputs/con1/1/3"},
                       "z": {"opx_output": "#/ports/analog_outputs/con1/4/2"}},
            },
            "qubit_pairs": {"q1-2": {"c": {"control_qubit": "#/qubits/q1",
                                            "target_qubit": "#/qubits/q2",
                                            "opx_output": "#/ports/analog_outputs/con1/4/7"}}},
        },
    }
    return state, wiring


def test_twpa_lines_extracted_and_buildable():
    # modern quam_builder builds TWPAs (Connectivity.add_twpa_lines), so the
    # reconstruct must PIN each pump line from wiring.twpas, not drop them.
    state = {"qubits": {"q1": {}},
             "twpas": {"twpaA": {"pump": {}, "qubits": ["q1"]}}}
    wiring = {"network": {"host": "1.2.3.4", "cluster_name": "C"}, "wiring": {
        "qubits": {"q1": {"rr": {"opx_output": "#/ports/mw_outputs/con1/1/1",
                                 "opx_input": "#/ports/mw_inputs/con1/1/1"}}},
        "twpas": {"twpaA": {"pump": {"opx_output": "#/ports/mw_outputs/con1/1/8"},
                            "pump_": {"opx_output": "#/ports/mw_outputs/con1/1/8"}}}}}
    rec = reconstruct_spec(state, wiring)
    # WIZARD-NATIVE object shape (review-r6: bare strings rendered as broken
    # empty rows in step 4), carrying the source chip's coverage list
    assert rec.spec["twpas"] == [{"id": "twpaA", "qubits": ["q1"]}]
    pump = [ln for ln in rec.spec["lines"] if ln["line"] == "twpa_pump"]
    assert len(pump) == 1
    assert pump[0]["element"] == "twpaA"
    assert pump[0]["channel"] == {"kind": "mw_fem", "con": 1, "slot": 1, "out_port": 8}
    assert config_generator.validate_spec(rec.spec) == []   # object-id TWPAs accepted


def test_twpa_short_line_keys_accepted():
    # quam_builder 0.4.0 / qualang_tools 0.22 write the TWPA pump under the
    # SHORT key "p" (isolation under "i") — same convention as qubit rr/xy/z.
    # A chip built by that stack must not lose its TWPA on re-generate.
    state = {"qubits": {"q1": {}}, "twpas": {"twpa1": {"pump": {}}}}
    wiring = {"network": {"host": "1.2.3.4", "cluster_name": "C"}, "wiring": {
        "qubits": {"q1": {"rr": {"opx_output": "#/ports/mw_outputs/con1/1/1",
                                 "opx_input": "#/ports/mw_inputs/con1/1/1"}}},
        "twpas": {"twpa1": {"p": {"opx_output": "#/ports/mw_outputs/con1/1/4"},
                            "i": {"opx_output": "#/ports/mw_outputs/con1/1/5"}}}}}
    rec = reconstruct_spec(state, wiring)
    assert rec.spec["twpas"] == [{"id": "twpa1", "qubits": []}]
    pump = [ln for ln in rec.spec["lines"] if ln["line"] == "twpa_pump"]
    iso = [ln for ln in rec.spec["lines"] if ln["line"] == "twpa_isolation"]
    assert len(pump) == 1 and pump[0]["channel"]["out_port"] == 4
    assert len(iso) == 1 and iso[0]["channel"]["out_port"] == 5


def test_reconstructs_structure():
    state, wiring = _tiny()
    r = reconstruct_spec(state, wiring)
    s = r.spec
    assert s["qubits"] == ["q1", "q2"]
    assert s["qubit_pairs"] == [["q1", "q2"]]
    assert s["network"]["host"] == "1.2.3.4"
    assert s["pair_gate"] == "cz_tunable"          # coupler + cz macro
    # multiplexed resonator: both qubits share one feedline group
    res = [l for l in s["lines"] if l["line"] == "resonator"]
    assert {l["group"] for l in res} == {"feedline1"}
    # instruments inferred: one MW-FEM (slot 1) + one LF-FEM (slot 4) on con1
    fems = {(f["slot"], f["fem"]) for f in s["instruments"]["controllers"][0]["fems"]}
    assert (1, "mw") in fems and (4, "lf") in fems


def test_reconstructed_spec_is_valid():
    state, wiring = _tiny()
    r = reconstruct_spec(state, wiring)
    assert config_generator.validate_spec(r.spec) == []


def test_dangling_pairs_dropped_with_note():
    # deviceC-class chip (r13): a cut-down layout keeps qubit_pairs entries
    # whose member qubit was removed (real data: qB3-qA4 / qD4-qA3 against 15
    # real qubits). The reconstruct must DROP them — a phantom member
    # hard-blocks BOTH the wizard's step-4 gate and validate_spec — say so in
    # notes, and drop their populate overrides too (run_build would only
    # warn-and-ignore them under a phantom key).
    state, wiring = _tiny()
    state["qubit_pairs"]["q1-qGone"] = {
        "qubit_control": "#/qubits/q1", "qubit_target": "#/qubits/qGone",
        "coupler": {"x": 1}, "macros": {"cz_unipolar": {}},
    }
    r = reconstruct_spec(state, wiring)
    assert r.spec["qubit_pairs"] == [["q1", "q2"]]          # phantom dropped
    assert any("q1-qGone" in n and "dropped" in n for n in r.notes)
    assert "q1-qGone" not in ((r.spec.get("populate") or {}).get("pairs") or {})
    assert config_generator.validate_spec(r.spec) == []     # step-4 unblocked


def test_wiring_only_dangling_pair_not_resurrected():
    # The wiring-only recovery path (state pair deleted, wiring channel kept)
    # used to append its tail-split members unchecked — same phantom hazard.
    state, wiring = _tiny()
    wiring["wiring"]["qubit_pairs"]["ghost"] = {
        "c": {"control_qubit": "#/qubits/q1",
              "target_qubit": "#/qubits/qNope",
              "opx_output": "#/ports/analog_outputs/con1/4/8"}}
    r = reconstruct_spec(state, wiring)
    assert ["q1", "qNope"] not in r.spec["qubit_pairs"]
    assert any("ghost" in n and "not on this chip" in n for n in r.notes)
    assert config_generator.validate_spec(r.spec) == []


def test_wizard_missing_pair_member_stays_visible():
    # r13 companion of the drop: when a pair's model value names a qubit that
    # no longer exists (in-wizard deletion — the server path drops dangling
    # pairs before hydrate), the select must render an explicit
    # "<name> (missing)" option instead of silently showing the "—"
    # placeholder while the model still holds the phantom.
    base = Path(__file__).resolve().parent.parent / "quam_state_manager" / "web"
    js = (base / "static" / "generate.js").read_text(encoding="utf-8")
    assert '" (missing)</option>"' in js
    assert "gen-pair-missing" in js
    css = (base / "static" / "style.css").read_text(encoding="utf-8")
    assert "select.gen-pair-missing" in css


def test_mixed_gates_flagged():
    state = {"qubit_pairs": {
        "a": {"coupler": {"x": 1}, "macros": {"cz_unipolar": {}}},
        "b": {"cross_resonance": {"x": 1}, "macros": {"cr_drive": {}}},
    }}
    r = reconstruct_spec(state, {"wiring": {}})
    assert r.mixed_gates is True


# --- real calibrated chip (auto-skip when absent) ---------------------------
_CHIP = Path("<quam-states>/gen_2x3_cz_tunable")


@pytest.mark.skipif(not _CHIP.exists(), reason="real chip folder not present")
def test_real_chip_reconstructs_to_valid_buildable_spec():
    state = json.loads((_CHIP / "state.json").read_text())
    wiring = json.loads((_CHIP / "wiring.json").read_text())
    r = reconstruct_spec(state, wiring)
    assert len(r.spec["qubits"]) == 6
    assert len(r.spec["qubit_pairs"]) == 7
    assert r.spec["pair_gate"] == "cz_tunable"
    assert config_generator.validate_spec(r.spec) == []   # buildable


class TestCrReconstruction:
    """docs/54 — CR/ZZ inversion: wp['cr']/wp['zz'] become pinned lines,
    shared-port layouts are detected, and the CR populate table pre-fills."""

    def _fixture(self, **kw):
        import sys as _s
        from pathlib import Path as _P
        _s.path.insert(0, str(_P(__file__).parent))
        from cr_fixtures import make_flavor_b
        return make_flavor_b(**kw)

    def test_cr_lines_pinned_and_shared_detected(self):
        state, wiring = self._fixture()
        rec = reconstruct_spec(state, wiring)
        spec = rec.spec
        cr_lines = [ln for ln in spec["lines"] if ln["line"] == "cross_resonance"]
        assert len(cr_lines) == 4                    # both directions, all pairs
        # each CR line pinned to its CONTROL's xy port (q0 xy = con1/1/2)
        q01 = next(ln for ln in cr_lines if ln["element"] == "q0-q1")
        assert q01["channel"] == {"kind": "mw_fem", "con": 1,
                                  "slot": 1, "out_port": 2}
        assert spec["cr_port_mode"] == "shared_xy"
        assert spec["pair_gate"] == "cr"
        assert ["q0", "q1"] in spec["qubit_pairs"]
        assert ["q1", "q0"] in spec["qubit_pairs"]   # directed pairs preserved
        assert not rec.mixed_gates

    def test_zz_line_inverted(self):
        state, wiring = self._fixture(with_zz=True)
        spec = reconstruct_spec(state, wiring).spec
        zz_lines = [ln for ln in spec["lines"] if ln["line"] == "zz_drive"]
        assert len(zz_lines) == 1 and zz_lines[0]["element"] == "q0-q1"
        pv = spec["populate"]["pairs"]["q0-q1"]   # canonical control-target key
        assert pv["zz_detuning"] == -30e6
        assert pv["zz_flattop_length"] == 300

    def test_cr_populate_prefilled(self):
        state, wiring = self._fixture()
        pv = reconstruct_spec(state, wiring).spec["populate"]["pairs"]["q0-q1"]
        assert pv["cr_drive_phase"] == 0.11
        assert pv["cr_drive_amplitude_scaling"] == 1.0
        assert pv["cr_drive_amplitude"] == 0.79      # square op amplitude
        assert pv["cr_flattop_length"] == 300
        assert pv["cr_cancel_amplitude"] == 0.01     # target-xy stub amplitude
        # dual-upconverter LO recovery on the qubit side
        qv = reconstruct_spec(state, wiring).spec["populate"]["qubit"]["q0"]
        assert qv["LO_frequency"] == 5.0e9           # upconverters["1"]
        assert qv["cr_lo_frequency"] == 5.0e9        # upconverters["2"]

    def test_dedicated_cr_not_marked_shared(self):
        import sys as _s
        from pathlib import Path as _P
        _s.path.insert(0, str(_P(__file__).parent))
        from cr_fixtures import make_flavor_a
        state, wiring = make_flavor_a()
        spec = reconstruct_spec(state, wiring).spec
        cr_lines = [ln for ln in spec["lines"] if ln["line"] == "cross_resonance"]
        assert len(cr_lines) == 2                    # dedicated FEM-2 ports
        assert cr_lines[0]["channel"]["slot"] == 2
        assert "cr_port_mode" not in spec


def test_populate_pairs_keyed_by_control_target_orientation():
    """Per-pair populate buckets ride the WIZARD-canonical control-target id —
    keying by the source chip's ascending pair NAME lost the seeds of every
    orientation-flipped pair (wizard reconcile pruned them / run_build fell
    back to default seeding)."""
    from quam_state_manager.core import regen_spec
    state = {"qubits": {"q0": {}, "q1": {}},
             "qubit_pairs": {"q0-1": {
                 "qubit_control": "#/qubits/q1",
                 "qubit_target": "#/qubits/q0",
                 "moving_qubit": "control"}}}
    merged = dict(state)
    merged["wiring"] = {}
    pop = regen_spec._extract_populate(state, merged)
    assert list(pop["pairs"].keys()) == ["q1-q0"]


def test_populate_pair_key_wiring_fallback_and_raw_name():
    from quam_state_manager.core import regen_spec
    pair = {"moving_qubit": "target"}
    root = {"wiring": {"qubit_pairs": {"p7": {"c": {
        "control_qubit": "#/qubits/q3", "target_qubit": "#/qubits/q4"}}}}}
    assert regen_spec._populate_pair_key("p7", pair, root) == "q3-q4"
    assert regen_spec._populate_pair_key("p8", {}, {"wiring": {}}) == "p8"


# --- r16 adaptive loading (docs/72): ports-union, null channels, qubit union ---

def _customer17q_shaped():
    """Mini replica of the LabD-17Q customer shape: ports live in STATE.json,
    wiring carries only wiring.qubits + network, slot 7 has exactly one user."""
    state = {
        "qubits": {"q1": {}, "q2": {}},
        "qubit_pairs": {"q1-2": {"qubit_control": "#/qubits/q1",
                                 "qubit_target": "#/qubits/q2",
                                 "macros": {"cz_unipolar": {}}}},
        "twpas": [],                       # real chips ship a LIST here
        "ports": {
            "mw_outputs": {"con1": {"1": {"1": {}, "2": {}, "3": {}},
                                    "__class__": "x"}},
            "mw_inputs": {"con1": {"1": {"1": {}}}},
            "analog_outputs": {"con1": {"5": {"1": {}, "2": {}},
                                        "7": {"1": {}}}},
            "__class__": "quam.components.ports.Ports",
        },
    }
    wiring = {
        "network": {"host": "1.2.3.4", "cluster_name": "C", "port": None},
        "wiring": {"qubits": {
            "q1": {"rr": {"opx_input": "#/ports/mw_inputs/con1/1/1",
                          "opx_output": "#/ports/mw_outputs/con1/1/1"},
                   "xy": {"opx_output": "#/ports/mw_outputs/con1/1/2"},
                   "z": {"opx_output": "#/ports/analog_outputs/con1/5/1"}},
            "q2": {"rr": {"opx_input": "#/ports/mw_inputs/con1/1/1",
                          "opx_output": "#/ports/mw_outputs/con1/1/1"},
                   "xy": {"opx_output": "#/ports/mw_outputs/con1/1/3"},
                   "z": {"opx_output": "#/ports/analog_outputs/con1/7/1"}},
        }},
    }
    return state, wiring


def _fems_of(spec):
    return {(c["con"], f["slot"], f["fem"])
            for c in spec["instruments"]["controllers"] for f in c["fems"]}


def test_ports_inventory_unions_into_fems():
    # slot 7's ONLY user is q2.z — delete q2 from wiring and the FEM must
    # survive via the state ports inventory (the LabD-17Q slot-7 report).
    state, wiring = _customer17q_shaped()
    del wiring["wiring"]["qubits"]["q2"]
    r = reconstruct_spec(state, wiring)
    fems = _fems_of(r.spec)
    assert (1, 7, "lf") in fems
    assert (1, 5, "lf") in fems and (1, 1, "mw") in fems
    assert any("slot con1/7" in n and "ports inventory" in n for n in r.notes)


def test_state_only_qubit_kept_with_note_and_pairs_survive():
    state, wiring = _customer17q_shaped()
    del wiring["wiring"]["qubits"]["q2"]
    r = reconstruct_spec(state, wiring)
    assert r.spec["qubits"] == ["q1", "q2"]          # union, wiring order first
    assert ["q1", "q2"] in r.spec["qubit_pairs"]     # membership gate passes
    assert any("q2" in n and "auto-allocated" in n for n in r.notes)


def test_null_channels_do_not_crash():
    # Explorer nulling / hand edits produce channels serialized as null.
    state, wiring = _customer17q_shaped()
    wiring["wiring"]["qubits"]["q1"]["rr"] = None
    wiring["wiring"]["qubits"]["q1"]["xy"] = None
    wiring["wiring"]["qubits"]["q2"]["z"] = None
    r = reconstruct_spec(state, wiring)              # must not raise
    assert (1, 7, "lf") in _fems_of(r.spec)          # kept from ports
    assert config_generator.validate_spec(r.spec) == []


def test_null_pair_coupler_channel_tolerated():
    state, wiring = _customer17q_shaped()
    wiring["wiring"]["qubit_pairs"] = {"q1-2": {"c": None}}
    r = reconstruct_spec(state, wiring)              # must not raise
    assert ["q1", "q2"] in r.spec["qubit_pairs"]


def test_twpas_as_list_tolerated():
    state, wiring = _customer17q_shaped()
    state["twpas"] = [{"id": "weird"}]               # non-dict shape
    wiring["wiring"]["twpas"] = {"t1": {"p": {
        "opx_output": "#/ports/mw_outputs/con1/1/3"}}}
    r = reconstruct_spec(state, wiring)              # must not raise
    assert r.spec["twpas"] == [{"id": "t1", "qubits": []}]


def test_clean_chip_emits_no_inventory_notes():
    # The union must stay SILENT when every declared slot has channel users
    # (no note noise on healthy chips).
    state, wiring = _customer17q_shaped()
    r = reconstruct_spec(state, wiring)
    assert not any("ports inventory" in n for n in r.notes)
    assert not any("auto-allocated" in n for n in r.notes)


def test_wiring_only_pair_fem_reaches_controllers():
    # The wiring-only-pair recovery loop runs AFTER the old early controllers
    # assembly ran — its note_fem() additions were silently lost (fixed by
    # assembling controllers last, with the ports union). Pin with a coupler
    # on a slot NO other channel uses.
    state, wiring = _tiny()
    del state["qubit_pairs"]["q1-2"]                 # pair exists only in wiring
    wiring["wiring"]["qubit_pairs"]["q1-2"]["c"]["opx_output"] = \
        "#/ports/analog_outputs/con1/6/7"
    r = reconstruct_spec(state, wiring)
    assert (1, 6, "lf") in _fems_of(r.spec)         # coupler-only FEM present


class TestQdacInversion:
    """docs/134 review [11]: a docs/119 QDAC-biased chip must reconstruct WITH
    its bias story — before this, reconstruct dropped it entirely and the
    rebuild silently produced qubits with neither an OPX z line nor a QDAC
    bias component."""

    def _qdac_state_wiring(self):
        state = {
            "qubits": {
                "q1": {"z": {"__class__":
                             "quam_config.qdac_components.QdacBiasLine",
                             "channel": 13, "dc_offset": -0.09,
                             "trigger_port": "ext1", "dwell": 2e-6,
                             "slew_rate": 2e7, "output_range": "high",
                             "output_filter": "med", "settle_time": 20000}},
                "q2": {},
            },
            "qdac": {"__class__": "quam_config.qdac_components.QdacInstrument",
                     "communication_type": "Ethernet",
                     "ip_address": "192.168.88.244", "port": 5025,
                     "usb_device": None, "lib": "@py"},
        }
        wiring = {
            "network": {"host": "1.2.3.4", "cluster_name": "C", "port": None},
            "wiring": {"qubits": {
                "q1": {"rr": {"opx_input": "#/ports/mw_inputs/con1/1/1",
                              "opx_output": "#/ports/mw_outputs/con1/1/1"},
                       "xy": {"opx_output": "#/ports/mw_outputs/con1/1/2"}},
                # q1 deliberately has NO z wiring channel — bias is QDAC.
                "q2": {"rr": {"opx_input": "#/ports/mw_inputs/con1/1/1",
                              "opx_output": "#/ports/mw_outputs/con1/1/1"},
                       "xy": {"opx_output": "#/ports/mw_outputs/con1/1/3"},
                       "z": {"opx_output": "#/ports/analog_outputs/con1/4/1"}},
            }},
        }
        return state, wiring

    def test_qdac_bias_carried_into_spec(self):
        state, wiring = self._qdac_state_wiring()
        rec = reconstruct_spec(state, wiring)
        qd = rec.spec.get("qdac")
        assert isinstance(qd, dict), "spec.qdac missing for a QDAC-biased chip"
        assert qd["ip_address"] == "192.168.88.244"
        assert set(qd["qubits"]) == {"q1"}
        assert qd["qubits"]["q1"]["channel"] == 13
        assert qd["qubits"]["q1"]["trigger_port"] == "ext1"
        assert qd["qubits"]["q1"]["dc_offset"] == -0.09
        # No OPX flux line invented for the QDAC qubit; q2 keeps its real one.
        flux = [ln["element"] for ln in rec.spec["lines"] if ln["line"] == "flux"]
        assert flux == ["q2"]
        # The carry is announced, never silent.
        assert any("QDAC-biased" in n for n in rec.notes)

    def test_plain_chip_gets_no_qdac_key(self):
        state, wiring = _tiny()
        rec = reconstruct_spec(state, wiring)
        assert "qdac" not in rec.spec
