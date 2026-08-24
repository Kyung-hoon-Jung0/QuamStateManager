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
        # The carry is announced, never silent — and reads as a statement of
        # fact, not as an unmet requirement (docs/135): the env's ability to
        # rebuild it is the capability check's answer, not this note's.
        note = next((n for n in rec.info_notes if "QDAC-II" in n), None)
        assert note, f"QDAC carry not announced: {rec.info_notes}"
        assert "carried into the spec" in note
        assert "needs an env" not in note
        # And it does NOT ride the warning list — the renderer prefixes every
        # `notes` entry with ⚠.
        assert not any("QDAC" in n for n in rec.notes), rec.notes

    def test_qdac_qubit_list_is_naturally_ordered(self):
        """q2 before q11 — a lexical list on a 20-qubit chip reads scrambled."""
        state, wiring = self._qdac_state_wiring()
        bias = dict(state["qubits"]["q1"]["z"])
        for name in ("q11", "q2", "q3"):
            state["qubits"][name] = {"z": dict(bias)}
            wiring["wiring"]["qubits"][name] = {
                "xy": {"opx_output": "#/ports/mw_outputs/con1/1/4"}}
        rec = reconstruct_spec(state, wiring)
        note = next(n for n in rec.info_notes if "QDAC-II" in n)
        assert "(q1, q2, q3, q11)" in note, note

    def test_plain_chip_gets_no_qdac_key(self):
        state, wiring = _tiny()
        rec = reconstruct_spec(state, wiring)
        assert "qdac" not in rec.spec

    def _shared_trigger_chip(self):
        """Two QDAC qubits cabled to ONE digital output — how a real bench
        wires it: one OPX digital out per QDAC ext trigger INPUT, shared by
        every qubit armed on that input (docs/135 ⑤)."""
        state, wiring = self._qdac_state_wiring()
        state["qubits"]["q3"] = {"z": dict(state["qubits"]["q1"]["z"], channel=15)}
        wiring["wiring"]["qubits"]["q1"]["qt"] = {
            "digital_output": "#/ports/digital_outputs/con1/4/1"}
        wiring["wiring"]["qubits"]["q3"] = {
            "xy": {"opx_output": "#/ports/mw_outputs/con1/1/4"},
            "qt": {"digital_output": "#/ports/digital_outputs/con1/4/1"}}
        return state, wiring

    def test_the_existing_trigger_port_is_carried(self):
        state, wiring = self._shared_trigger_chip()
        rec = reconstruct_spec(state, wiring)
        qs = rec.spec["qdac"]["qubits"]
        # Same port for both — a re-generate must not re-cable the bench.
        assert qs["q1"]["trigger_pin"] == {"con": 1, "slot": 4, "port": 1}
        assert qs["q3"]["trigger_pin"] == {"con": 1, "slot": 4, "port": 1}

    def test_a_pin_on_an_undeclared_fem_is_dropped_out_loud(self):
        """An unusable pin degrades to auto-allocation — never a dangling
        reference in the rebuilt chip — and says so."""
        state, wiring = self._shared_trigger_chip()
        wiring["wiring"]["qubits"]["q3"]["qt"] = {
            "digital_output": "#/ports/digital_outputs/con1/9/1"}   # no slot 9
        rec = reconstruct_spec(state, wiring)
        qs = rec.spec["qdac"]["qubits"]
        assert "trigger_pin" not in qs["q3"]
        assert qs["q1"]["trigger_pin"] == {"con": 1, "slot": 4, "port": 1}
        assert any("q3" in n and "re-allocated" in n for n in rec.notes), rec.notes

    def test_a_qubit_with_no_trigger_wiring_gets_no_pin(self):
        state, wiring = self._qdac_state_wiring()   # q1 has no qt line
        rec = reconstruct_spec(state, wiring)
        assert "trigger_pin" not in rec.spec["qdac"]["qubits"]["q1"]
        assert not rec.notes, rec.notes


class TestTheChipsOwnRootClassIsCarried:
    """docs/135 ⑤ review (CRITICAL): the build derived the QPU ROOT class from
    line types alone. A chip rooted at a customer subclass — often the very
    reason the subclass exists — was rebuilt onto the stock class, and the
    result could not be loaded: stock FluxTunableQuam types `qubits` as
    Dict[str, FluxTunableTransmon] (so the first QdacBiasedFixedFrequencyTransmon
    fails validation) and declares no `qdac` field (so the injected top-level
    entry raises too). Build and merge both reported success."""

    def test_the_root_class_rides_the_spec(self):
        state, wiring = _tiny()
        state["__class__"] = "quam_config.my_quam.Quam"
        rec = reconstruct_spec(state, wiring)
        assert rec.spec["quam_class"] == "quam_config.my_quam.Quam"
        assert config_generator.validate_spec(rec.spec) == []

    def test_a_chip_with_no_root_marker_carries_none(self):
        state, wiring = _tiny()
        rec = reconstruct_spec(state, wiring)
        assert rec.spec["quam_class"] is None
        assert config_generator.validate_spec(rec.spec) == []

    def test_a_bare_name_is_not_treated_as_an_import_path(self):
        state, wiring = _tiny()
        state["__class__"] = "FluxTunableQuam"      # no module — unusable
        rec = reconstruct_spec(state, wiring)
        assert rec.spec["quam_class"] is None


class TestQdacTriggerPinsAreHonoured:
    """docs/135 ⑤: the build's isolated trigger pass allocates a DEDICATED
    port per biased qubit. That re-cables a bench that shares one OPX digital
    output per QDAC ext input, so a pin carried by reconstruct wins."""

    def _spec(self, **pins):
        return {
            "qdac": {"qubits": {
                q: ({"channel": i + 1} | ({"trigger_pin": p} if p else {}))
                for i, (q, p) in enumerate(pins.items())}},
            # A pin is only honoured on a slot the chassis declares (review
            # finding: the reconstruct-time guard cannot see a step-3 edit).
            "instruments": {"controllers": [
                {"con": 1, "fems": [{"slot": 4, "fem": "lf"}]}]},
        }

    def test_all_pinned_needs_no_allocator_at_all(self):
        from quam_state_manager.generator import run_build
        spec = self._spec(q1={"con": 1, "slot": 4, "port": 1},
                          q9={"con": 1, "slot": 4, "port": 1},
                          q3={"con": 1, "slot": 4, "port": 2})
        # instruments=None proves the allocator was never reached — this path
        # must not depend on qualang_tools being importable at all.
        pins, warnings, allocation = run_build._allocate_qdac_triggers(spec, None)
        assert pins == {"q1": (1, 4, 1), "q9": (1, 4, 1), "q3": (1, 4, 2)}
        assert warnings == []
        # ...and the dry run can draw them: read_allocation's own shape.
        assert set(allocation) == {"q1", "q9", "q3"}
        ch = allocation["q1"]["qt"][0]
        assert (ch["con"], ch["slot"], ch["port"]) == (1, 4, 1)
        assert ch["io_type"] == "digital"
        assert ch["instrument_id"] == "lf-fem"

    def test_sharing_survives(self):
        from quam_state_manager.generator import run_build
        spec = self._spec(q1={"con": 1, "slot": 4, "port": 1},
                          q9={"con": 1, "slot": 4, "port": 1})
        pins, _, _ = run_build._allocate_qdac_triggers(spec, None)
        assert len(set(pins.values())) == 1, "two qubits must keep ONE port"

    def test_a_malformed_pin_falls_back_and_says_so(self):
        from quam_state_manager.generator import run_build
        spec = {"qdac": {"qubits": {"q1": {"channel": 1, "trigger_pin": {"con": 1}}}}}
        pins, warnings, _ = run_build._allocate_qdac_triggers(spec, None)
        assert "q1" not in pins
        assert any("auto-allocated" in w for w in warnings), warnings

    def test_no_qdac_qubits_is_still_a_clean_no_op(self):
        from quam_state_manager.generator import run_build
        assert run_build._allocate_qdac_triggers({}, None) == ({}, [], {})

    def test_a_pin_on_a_slot_the_chassis_no_longer_declares_is_refused(self):
        """The reconstruct-time guard runs ONCE, at hydration — step 3 lets the
        user delete a FEM afterwards. Re-tested at the point of use, or the
        pin reaches `create=True` and lands a digital port on a FEM the built
        chip does not declare."""
        from quam_state_manager.generator import run_build
        spec = self._spec(q1={"con": 1, "slot": 9, "port": 1})
        spec["instruments"] = {"controllers": [{"con": 1, "fems": [{"slot": 4, "fem": "lf"}]}]}
        pins, warnings, allocation = run_build._allocate_qdac_triggers(spec, None)
        assert "q1" not in pins
        assert allocation == {}
        assert any("no longer declares" in w for w in warnings), warnings


class TestAllocateModeShowsTheTriggers:
    """docs/135 ⑤: the dry run behind the wizard's diagram used to skip the
    isolated QDAC trigger pass entirely, so a chip whose biased qubits each
    get an OPX digital output previewed with no digital column at all while
    /instrument showed one. Pinned here because the merge is the only thing
    standing between the two surfaces agreeing."""

    def _patch(self, monkeypatch, qt_alloc):
        from quam_state_manager.generator import run_build
        monkeypatch.setattr(run_build, "build_instruments", lambda spec: object())
        monkeypatch.setattr(run_build, "allocate_full",
                            lambda spec, instr: (object(), ["main-warning"]))
        monkeypatch.setattr(run_build, "read_allocation", lambda conn: {
            "q1": {"xy": [{"con": 1, "slot": 1, "port": 1, "io_type": "output"}]}})
        monkeypatch.setattr(run_build, "_allocate_qdac_triggers",
                            lambda spec, instr: ({}, ["qdac-warning"], qt_alloc))
        return run_build

    def test_the_trigger_pass_rides_along(self, monkeypatch):
        qt = {"q1": {"qt": [{"con": 1, "slot": 4, "port": 1, "io_type": "digital"}]},
              "q9": {"qt": [{"con": 1, "slot": 4, "port": 1, "io_type": "digital"}]}}
        run_build = self._patch(monkeypatch, qt)
        out = run_build.run_allocate({"qdac": {"qubits": {"q1": {}, "q9": {}}}})
        a = out["allocation"]
        # The qt line is an ADDITION to a qubit that already has analog lines,
        # never a replacement — that is what made the diagram whole.
        assert a["q1"]["xy"], "the main allocation must survive the merge"
        assert a["q1"]["qt"][0]["port"] == 1
        assert a["q9"]["qt"][0]["port"] == 1, "a qubit with only a trigger still appears"
        assert "main-warning" in out["warnings"]
        assert "qdac-warning" in out["warnings"]

    def test_a_chip_with_no_qdac_is_byte_identical(self, monkeypatch):
        run_build = self._patch(monkeypatch, {})
        out = run_build.run_allocate({})
        assert out["allocation"] == {
            "q1": {"xy": [{"con": 1, "slot": 1, "port": 1, "io_type": "output"}]}}
        assert out["warnings"] == ["main-warning", "qdac-warning"]


class TestQdacTriggerPinsAreReserved:
    """docs/135 ⑤ review: the auto pass cannot see a pin. The main allocation
    consumes only ANALOG lines, so the digital pool it walks is fresh and it
    hands the first unpinned qubit the first free digital output — the very
    port the pinned qubits are cabled to. A QDAC channel waits on the ext
    input its own cable drives, so a double-booked port means one qubit
    pulses the wrong ext line and its bias never arms."""

    def _spec(self, pinned, unpinned, ext=None):
        ext = ext or {}
        qubits = {q: {"channel": i + 1, "trigger_pin": p}
                  for i, (q, p) in enumerate(pinned.items())}
        for j, q in enumerate(unpinned):
            qubits[q] = {"channel": 90 + j}
        for q, e in ext.items():
            qubits.setdefault(q, {})["trigger_port"] = e
        return {"qdac": {"qubits": qubits},
                "instruments": {"controllers": [
                    {"con": 1, "fems": [{"slot": 4, "fem": "lf"}]}]}}

    def test_same_ext_input_means_the_same_cable(self):
        """A qubit armed on ext1 belongs on the port already cabled to ext1 —
        the allocator's idea of "free" is irrelevant to physical cabling, and
        the earlier fix's relocate-to-any-free-port produced exactly the
        extN↔portN mismatch it existed to prevent."""
        from quam_state_manager.generator import run_build
        spec = self._spec({"q1": {"con": 1, "slot": 4, "port": 2}}, ["qNEW"],
                          ext={"q1": "ext2", "qNEW": "ext2"})
        # instruments=None: this resolves without the allocator at all.
        pins, warnings, allocation = run_build._allocate_qdac_triggers(spec, None)
        assert pins["qNEW"] == (1, 4, 2), pins
        assert any("already cabled to ext2" in w for w in warnings), warnings
        assert allocation["qNEW"]["qt"][0]["port"] == 2

    def test_a_different_ext_input_does_not_join_the_cable(self):
        from quam_state_manager.generator import run_build
        spec = self._spec({"q1": {"con": 1, "slot": 4, "port": 2}}, ["qNEW"],
                          ext={"q1": "ext2", "qNEW": "ext3"})
        pins, _, _ = run_build._allocate_qdac_triggers(spec, None)
        assert pins.get("qNEW") != (1, 4, 2)

    def _fake_alloc(self, monkeypatch, giving):
        """Stand in for the allocator, which needs qualang_tools + a real
        instruments pool. `giving` is what it hands each unpinned qubit."""
        from quam_state_manager.generator import run_build
        monkeypatch.setattr(run_build, "read_allocation", lambda conn: {
            q: {"qt": [{"instrument_id": "lf-fem", "con": c, "slot": s,
                        "port": p, "io_type": "output", "signal_type": "digital"}]}
            for q, (c, s, p) in giving.items()})

    def test_a_collision_is_moved_and_announced(self, monkeypatch):
        pytest.importorskip("qualang_tools.wirer")
        from quam_state_manager.generator import run_build
        import qualang_tools.wirer as _w
        monkeypatch.setattr(_w, "allocate_wiring", lambda *a, **k: None)
        spec = self._spec({"q1": {"con": 1, "slot": 4, "port": 1}}, ["qNEW"])
        self._fake_alloc(monkeypatch, {"qNEW": (1, 4, 1)})   # onto q1's cable
        pins, warnings, allocation = run_build._allocate_qdac_triggers(spec, object())
        assert pins["q1"] == (1, 4, 1), "the pinned cable never moves"
        assert pins["qNEW"] != (1, 4, 1), "an unpinned trigger must not share a pinned cable"
        assert pins["qNEW"][:2] == (1, 4), "it stays on the same FEM"
        assert any("already cabled to a pinned trigger" in w for w in warnings), warnings
        # and the preview must show where it actually went, not where the
        # allocator first put it
        ch = allocation["qNEW"]["qt"][0]
        assert (ch["con"], ch["slot"], ch["port"]) == pins["qNEW"]

    def test_two_unpinned_qubits_do_not_collide_with_each_other(self, monkeypatch):
        pytest.importorskip("qualang_tools.wirer")
        from quam_state_manager.generator import run_build
        import qualang_tools.wirer as _w
        monkeypatch.setattr(_w, "allocate_wiring", lambda *a, **k: None)
        spec = self._spec({"q1": {"con": 1, "slot": 4, "port": 1}}, ["qA", "qB"])
        self._fake_alloc(monkeypatch, {"qA": (1, 4, 1), "qB": (1, 4, 1)})
        pins, _, _ = run_build._allocate_qdac_triggers(spec, object())
        assert len({pins["q1"], pins["qA"], pins["qB"]}) == 3, pins

    def test_a_full_fem_degrades_instead_of_double_booking(self, monkeypatch):
        pytest.importorskip("qualang_tools.wirer")
        from quam_state_manager.generator import run_build
        import qualang_tools.wirer as _w
        monkeypatch.setattr(_w, "allocate_wiring", lambda *a, **k: None)
        spec = self._spec({f"q{i}": {"con": 1, "slot": 4, "port": i}
                           for i in range(1, 9)}, ["qNEW"])
        self._fake_alloc(monkeypatch, {"qNEW": (1, 4, 1)})
        pins, warnings, allocation = run_build._allocate_qdac_triggers(spec, object())
        assert "qNEW" not in pins
        assert "qNEW" not in allocation, "never draw a port it will not get"
        assert any("could not be placed" in w for w in warnings), warnings
