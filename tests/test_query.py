"""Tests for quam_state_manager.core.query.QueryEngine.

Covers get_qubit, get_pair, list_qubits (with filtering), summary_table,
get_wiring_map, get_topology, and get_port_for.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.query import (
    QueryEngine,
    _eval_filter,
    _extract_pair_gate_fidelities,
)

# ---------------------------------------------------------------------------
# Paths to real quam_state folders
# ---------------------------------------------------------------------------

_EXAMPLECHIP_ROOT = Path(r"<data-root>/qualibration_graphs/superconducting")
SMALL_FOLDER = _EXAMPLECHIP_ROOT / "quam_state"
LARGE_FOLDER = _EXAMPLECHIP_ROOT / "quam_states_arv" / "quam_state_examplechip_variantb"

has_small = SMALL_FOLDER.exists() and (SMALL_FOLDER / "state.json").exists()
has_large = LARGE_FOLDER.exists() and (LARGE_FOLDER / "state.json").exists()

skip_no_small = pytest.mark.skipif(not has_small, reason="Small quam_state not found")
skip_no_large = pytest.mark.skipif(not has_large, reason="Large quam_state not found")


# ---------------------------------------------------------------------------
# Synthetic fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def synthetic_folder(tmp_path: Path) -> Path:
    state = {
        "qubits": {
            "qA1": {
                "id": "qA1",
                "grid_location": "0,2",
                "f_01": 6.25e9,
                "f_12": None,
                "anharmonicity": -220e6,
                "chi": -5.2e6,
                "T1": 8834,
                "T2ramsey": 1.5e-6,
                "T2echo": None,
                "freq_vs_flux_01_quad_term": -1.12e11,
                "phi0_current": 4.85,
                "phi0_voltage": 0.097,
                "xy": {
                    "RF_frequency": 6.25e9,
                    "intermediate_frequency": "#./inferred_intermediate_frequency",
                    "opx_output": "#/wiring/qubits/qA1/xy/opx_output",
                    "operations": {
                        "saturation": {"amplitude": 0.04, "length": 20000},
                        "x180_DragCosine": {"amplitude": 0.115, "length": 40, "alpha": -1.75},
                        "x90_DragCosine": {"amplitude": 0.057, "length": "#../x180_DragCosine/length"},
                    },
                },
                "resonator": {
                    "f_01": 7.64e9,
                    "RF_frequency": 7.64e9,
                    "confusion_matrix": [[0.91, 0.09], [0.12, 0.88]],
                    "time_of_flight": 380,
                    "opx_output": "#/wiring/qubits/qA1/rr/opx_output",
                    "operations": {
                        "readout": {
                            "amplitude": 0.042,
                            "length": 1000,
                            "threshold": -0.00014,
                            "integration_weights_angle": -38.9,
                        },
                    },
                },
                "z": {
                    "joint_offset": 0.081,
                    "independent_offset": 0.0,
                    "flux_point": "joint",
                    "opx_output": "#/wiring/qubits/qA1/z/opx_output",
                },
                "gate_fidelity": {"averaged": 0.991, "x180": 0.986, "x90": 0.986},
            },
            "qA2": {
                "id": "qA2",
                "grid_location": "1,2",
                "f_01": 5.8e9,
                "f_12": None,
                "anharmonicity": -210e6,
                "chi": None,
                "T1": 7500,
                "T2ramsey": 1.2e-6,
                "T2echo": None,
                "xy": {
                    "RF_frequency": 5.8e9,
                    "opx_output": "#/wiring/qubits/qA2/xy/opx_output",
                    "operations": {
                        "x180_DragCosine": {"amplitude": 0.13, "length": 40, "alpha": -1.5},
                        "x90_DragCosine": {"amplitude": 0.065},
                    },
                },
                "resonator": {
                    "f_01": 7.1e9,
                    "operations": {"readout": {"amplitude": 0.05, "length": 1000}},
                },
                "z": {"joint_offset": 0.0, "independent_offset": 0.0, "flux_point": "joint"},
                "gate_fidelity": {"averaged": 0.985, "x180": 0.98, "x90": 0.98},
            },
        },
        "qubit_pairs": {
            "qA1-A2": {
                "id": "qA1-A2",
                "qubit_control": "#/qubits/qA2",
                "qubit_target": "#/qubits/qA1",
                "moving_qubit": "target",
                "macros": {
                    "cz_unipolar": {
                        "fidelity": {},
                        "flux_pulse_qubit": {"amplitude": 0.05, "length": 100},
                        "coupler_flux_pulse": {"amplitude": 0.1, "length": 100},
                        "phase_shift_control": 0.1,
                        "phase_shift_target": 0.2,
                    },
                    "cz_flattop": {
                        "fidelity": {
                            "Bell_State": {"Fidelity": 0.85, "Purity": 0.9},
                            "StandardRB": 0.55,
                            "InterleavedRB": 0.92,
                        },
                        "flux_pulse_qubit": {
                            "amplitude": 0.032, "flat_length": 200,
                            "smoothing_length": 20, "length": "#./inferred_total_length",
                        },
                        "coupler_flux_pulse": {"amplitude": 0.258},
                        "phase_shift_control": 0.0,
                        "phase_shift_target": 0.0,
                    },
                },
                "coupler": {
                    "decouple_offset": 0.48,
                    "interaction_offset": 0.0,
                    "opx_output": "#/wiring/qubit_pairs/qA1-A2/c/opx_output",
                },
                "detuning": 0.032,
                "confusion": [[0.84, 0.11, 0.10, 0.01], [0.09, 0.81, 0.02, 0.09],
                               [0.07, 0.01, 0.80, 0.12], [0.01, 0.07, 0.08, 0.78]],
                "mutual_flux_bias": [0, 0],
            },
        },
        "active_qubit_names": ["qA1", "qA2"],
        "ports": {},
        "__class__": "quam_config.my_quam.Quam",
    }
    wiring = {
        "wiring": {
            "qubits": {
                "qA1": {
                    "xy": {"opx_output": "MW-FEM/1/2"},
                    "rr": {"opx_output": "MW-FEM/1/1", "opx_input": "MW-FEM/1/1"},
                    "z": {"opx_output": "LF-FEM/5/1"},
                },
                "qA2": {
                    "xy": {"opx_output": "MW-FEM/1/3"},
                    "rr": {"opx_output": "MW-FEM/1/1", "opx_input": "MW-FEM/1/1"},
                    "z": {"opx_output": "LF-FEM/5/2"},
                },
            },
            "qubit_pairs": {
                "qA1-A2": {"c": {"opx_output": "LF-FEM/5/4"}},
            },
        },
        "network": {"host": "10.1.1.18", "cluster_name": "test"},
    }
    (tmp_path / "state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps(wiring, indent=2), encoding="utf-8")
    return tmp_path


@pytest.fixture
def engine(synthetic_folder) -> QueryEngine:
    store = QuamStore(synthetic_folder)
    return QueryEngine(store)


# ---------------------------------------------------------------------------
# get_qubit
# ---------------------------------------------------------------------------


class TestGetQubit:
    def test_basic_properties(self, engine):
        q = engine.get_qubit("qA1")
        assert q["id"] == "qA1"
        assert q["f_01"] == 6.25e9
        assert q["anharmonicity"] == -220e6
        assert q["T1"] == 8834
        assert q["T2ramsey"] == 1.5e-6
        assert q["chi"] == -5.2e6
        assert q["grid_location"] == "0,2"

    def test_xy_properties(self, engine):
        q = engine.get_qubit("qA1")
        assert q["x180_amplitude"] == 0.115
        assert q["x180_length"] == 40
        assert q["x180_alpha"] == -1.75
        assert q["x90_amplitude"] == 0.057
        assert q["saturation_amplitude"] == 0.04
        assert q["xy_RF_frequency"] == 6.25e9

    def test_readout_properties(self, engine):
        q = engine.get_qubit("qA1")
        assert q["readout_frequency"] == 7.64e9
        assert q["readout_amplitude"] == 0.042
        assert q["readout_length"] == 1000
        assert q["readout_threshold"] == -0.00014
        assert q["confusion_matrix"] == [[0.91, 0.09], [0.12, 0.88]]

    def test_flux_properties(self, engine):
        q = engine.get_qubit("qA1")
        assert q["z_joint_offset"] == 0.081
        assert q["z_flux_point"] == "joint"

    def test_gate_fidelity(self, engine):
        q = engine.get_qubit("qA1")
        assert q["gate_fidelity_avg"] == 0.991
        assert q["gate_fidelity_x180"] == 0.986

    def test_missing_qubit_raises(self, engine):
        with pytest.raises(KeyError, match="qZZZ"):
            engine.get_qubit("qZZZ")

    def test_none_values_preserved(self, engine):
        q = engine.get_qubit("qA1")
        assert q["T2echo"] is None
        assert q["f_12"] is None


# ---------------------------------------------------------------------------
# get_pair
# ---------------------------------------------------------------------------


class TestGetPair:
    def test_basic_pair_properties(self, engine):
        p = engine.get_pair("qA1-A2")
        assert p["id"] == "qA1-A2"
        assert p["qubit_control"] == "qA2"
        assert p["qubit_target"] == "qA1"
        assert p["moving_qubit"] == "target"

    def test_cz_unipolar(self, engine):
        p = engine.get_pair("qA1-A2")
        assert p["cz_unipolar_amplitude"] == 0.05
        assert p["cz_unipolar_length"] == 100
        assert p["cz_unipolar_phase_shift_control"] == 0.1

    def test_cz_flattop(self, engine):
        p = engine.get_pair("qA1-A2")
        assert p["cz_flattop_amplitude"] == 0.032
        assert p["cz_flattop_flat_length"] == 200
        assert p["cz_flattop_smoothing_length"] == 20
        assert p["cz_flattop_bell_fidelity"] == 0.85
        assert p["cz_flattop_standard_rb"] == 0.55
        assert p["cz_flattop_interleaved_rb"] == 0.92

    def test_coupler(self, engine):
        p = engine.get_pair("qA1-A2")
        assert p["coupler_decouple_offset"] == 0.48
        assert p["coupler_interaction_offset"] == 0.0

    def test_characterization(self, engine):
        p = engine.get_pair("qA1-A2")
        assert p["detuning"] == 0.032
        assert len(p["confusion"]) == 4
        assert p["mutual_flux_bias"] == [0, 0]

    def test_missing_pair_raises(self, engine):
        with pytest.raises(KeyError, match="qZZ-ZZ"):
            engine.get_pair("qZZ-ZZ")

    def test_get_pair_handles_explicit_null_subobjects(self, tmp_path):
        """Regression: real chip data sometimes has nested fields whose
        VALUES are explicitly null in state.json (e.g. coupler_flux_pulse,
        flux_pulse_qubit, fidelity, coupler). dict.get(key, default) does
        NOT fall back to the default when the value is None — it returns
        None — so .get(...) on the result used to crash."""
        from quam_state_manager.core.loader import QuamStore
        from quam_state_manager.core.query import QueryEngine
        import json

        state = {
            "qubits": {
                "qA": {"id": "qA", "f_01": 5e9},
                "qB": {"id": "qB", "f_01": 5.1e9},
            },
            "qubit_pairs": {
                "qA-qB": {
                    "id": "qA-qB",
                    "qubit_control": "#/qubits/qA",
                    "qubit_target": "#/qubits/qB",
                    "moving_qubit": "target",
                    "macros": {
                        "cz_flattop": {
                            # All inner objects explicitly null — the
                            # exact pattern that crashed for LabB data.
                            "flux_pulse_qubit": None,
                            "coupler_flux_pulse": None,
                            "fidelity": None,
                        },
                    },
                    "coupler": None,
                    "detuning": None,
                    "confusion": None,
                    "mutual_flux_bias": None,
                },
            },
        }
        wiring = {"network": {"host": "10.1.1.1"}}
        (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
        (tmp_path / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")

        store = QuamStore(tmp_path)
        engine = QueryEngine(store)
        # Should NOT raise. Resulting fields are None but that's fine.
        pair = engine.get_pair("qA-qB")
        assert pair["id"] == "qA-qB"
        assert pair["cz_flattop_amplitude"] is None
        assert pair["cz_flattop_coupler_amplitude"] is None
        assert pair["coupler_decouple_offset"] is None

    def test_get_topology_handles_explicit_null_subobjects(self, tmp_path):
        """Same regression but for get_topology, which is called by Pairs UI
        and other entry points."""
        from quam_state_manager.core.loader import QuamStore
        from quam_state_manager.core.query import QueryEngine
        import json

        state = {
            "qubits": {
                "qA": {"id": "qA"},
                "qB": {"id": "qB"},
            },
            "qubit_pairs": {
                "qA-qB": {
                    "id": "qA-qB",
                    "qubit_control": "#/qubits/qA",
                    "qubit_target": "#/qubits/qB",
                    "macros": {
                        "cz_flattop": {"fidelity": None},
                    },
                    "coupler": None,
                },
            },
        }
        wiring = {"network": {"host": "10.1.1.1"}}
        (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
        (tmp_path / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")

        store = QuamStore(tmp_path)
        engine = QueryEngine(store)
        topo = engine.get_topology()  # should not raise
        assert any(e["pair_id"] == "qA-qB" for e in topo["edges"])


# ---------------------------------------------------------------------------
# get_port_for
# ---------------------------------------------------------------------------


class TestGetPortFor:
    def test_xy_port(self, engine):
        port = engine.get_port_for("qA1", "xy")
        assert port is not None
        assert "MW-FEM/1/2" in str(port)

    def test_rr_port(self, engine):
        port = engine.get_port_for("qA1", "rr")
        assert port is not None

    def test_z_port(self, engine):
        port = engine.get_port_for("qA1", "z")
        assert port is not None
        assert "LF-FEM/5/1" in str(port)

    def test_missing_qubit(self, engine):
        assert engine.get_port_for("qZZZ", "xy") is None

    def test_null_channel_returns_none_not_crash(self):
        """A channel key present but explicitly ``null`` must return None, not 500.

        Real QUAM data has ``"z": null`` etc.; ``q.get("z", {})`` returns None
        (the default only fires for a *missing* key) so the old code crashed on
        ``None.get("opx_output")``. Regression for the pair/qubit 500-on-click bug.
        """
        store = QuamStore.from_dicts(
            {"qubits": {"qX": {"id": "qX", "z": None, "xy": {}}}}, {}
        )
        eng = QueryEngine(store)
        assert eng.get_port_for("qX", "z") is None
        assert eng.get_port_for("qX", "xy") is None
        assert eng.get_port_for("qX", "rr") is None


class TestGetPortForPair:
    def test_null_coupler_returns_none_not_crash(self):
        """Every LabA pair has ``"coupler": null`` — this 500'd every pair click."""
        store = QuamStore.from_dicts(
            {"qubit_pairs": {"qX-qY": {"id": "qX-qY", "coupler": None}}}, {}
        )
        eng = QueryEngine(store)
        assert eng.get_port_for_pair("qX-qY") is None

    def test_missing_pair_returns_none(self):
        store = QuamStore.from_dicts({"qubit_pairs": {}}, {})
        assert QueryEngine(store).get_port_for_pair("nope") is None


def _cr_store() -> QuamStore:
    """A synthetic cross-resonance (CR) pair sharing a dual-upconverter port."""
    state = {
        "qubits": {
            "qc": {"id": "qc", "xy": {"opx_output": "#/wiring/qubits/qc/xy/opx_output"}},
            "qt": {"id": "qt", "xy": {"RF_frequency": 8.5e9}},
        },
        "qubit_pairs": {
            "qc-qt": {
                "id": "qc-qt",
                "qubit_control": "#/qubits/qc",
                "qubit_target": "#/qubits/qt",
                "macros": {"cr": {"__class__": "CRGate"}},
                "cross_resonance": {
                    "LO_frequency": "#/qubits/qc/xy/opx_output/upconverters/2/frequency",
                    "target_qubit_RF_frequency": "#/qubits/qt/xy/RF_frequency",
                    "intermediate_frequency": "#./inferred_intermediate_frequency",
                    "upconverter": 2,
                    "operations": {"square": {}, "flattop": {}},
                    "opx_output": "#/wiring/qubit_pairs/qc-qt/cr/opx_output",
                },
            },
        },
        "ports": {"mw_outputs": {"con1": {"1": {"2": {
            "upconverters": {"1": {"frequency": 8.6e9}, "2": {"frequency": 9.095e9}},
        }}}}},
    }
    wiring = {"wiring": {
        "qubits": {"qc": {"xy": {"opx_output": "#/ports/mw_outputs/con1/1/2"}}},
        "qubit_pairs": {"qc-qt": {"cr": {"opx_output": "#/ports/mw_outputs/con1/1/2"}}},
    }}
    return QuamStore.from_dicts(state, wiring)


class TestCrossResonancePair:
    def test_get_pair_surfaces_cr_channel(self):
        eng = QueryEngine(_cr_store())
        pd = eng.get_pair("qc-qt")
        assert pd["cr_lo_frequency"] == 9.095e9          # resolved through the shared port
        assert pd["cr_target_qubit_rf"] == 8.5e9
        assert pd["cr_upconverter"] == 2
        assert pd["cr_operations"] == "square, flattop"
        # the CR macro must NOT emit blank CZ-shaped fields
        assert "cr_amplitude" not in pd and "cr_coupler_amplitude" not in pd

    def test_get_port_for_pair_falls_back_to_cr(self):
        port = QueryEngine(_cr_store()).get_port_for_pair("qc-qt")
        assert isinstance(port, dict) and "upconverters" in port

    def test_pair_sections_show_cr_not_blank_coupler_or_phantom(self):
        from quam_state_manager.web import routes
        store = _cr_store()
        eng = QueryEngine(store)
        secs = routes._build_pair_sections("qc-qt", eng.get_pair("qc-qt"), store)
        names = [s["name"] for s in secs]
        assert "Cross Resonance" in names
        assert "Coupler" not in names      # no empty coupler section on a CR pair
        assert "Cr" not in names           # no phantom CZ section from the cr macro
        cr_sec = next(s for s in secs if s["name"] == "Cross Resonance")
        # dynamic section (docs/54): lever rows carry bare names + real paths
        uc = next(p for p in cr_sec["props"] if p["key"] == "upconverter")
        assert uc["editable"] and uc["dot_path"] == "qubit_pairs.qc-qt.cross_resonance.upconverter"


# ---------------------------------------------------------------------------
# list_qubits
# ---------------------------------------------------------------------------


class TestListQubits:
    def test_list_all(self, engine):
        qubits = engine.list_qubits()
        assert len(qubits) == 2
        ids = [q["id"] for q in qubits]
        assert "qA1" in ids
        assert "qA2" in ids

    def test_filter_by_frequency(self, engine):
        qubits = engine.list_qubits("f_01 > 6e9")
        assert len(qubits) == 1
        assert qubits[0]["id"] == "qA1"

    def test_filter_by_t2(self, engine):
        qubits = engine.list_qubits("T2ramsey > 1.3e-6")
        assert len(qubits) == 1
        assert qubits[0]["id"] == "qA1"

    def test_filter_combined(self, engine):
        qubits = engine.list_qubits("f_01 > 5e9 and T1 > 8000")
        assert len(qubits) == 1
        assert qubits[0]["id"] == "qA1"

    def test_filter_no_match(self, engine):
        qubits = engine.list_qubits("f_01 > 1e12")
        assert len(qubits) == 0

    def test_filter_invalid_expr(self, engine):
        qubits = engine.list_qubits("import os")
        assert len(qubits) == 2


# ---------------------------------------------------------------------------
# summary_table
# ---------------------------------------------------------------------------


class TestSummaryTable:
    def test_basic(self, engine):
        table = engine.summary_table(["f_01", "T1"])
        assert len(table) == 2
        assert table[0]["id"] == "qA1"
        assert table[0]["f_01"] == 6.25e9
        assert table[0]["T1"] == 8834

    def test_missing_property(self, engine):
        table = engine.summary_table(["nonexistent"])
        assert table[0]["nonexistent"] is None

    def test_always_has_id(self, engine):
        table = engine.summary_table([])
        assert all("id" in row for row in table)


# ---------------------------------------------------------------------------
# get_wiring_map
# ---------------------------------------------------------------------------


class TestGetWiringMap:
    def test_wiring_map(self, engine):
        wmap = engine.get_wiring_map()
        assert len(wmap) == 2
        qa1 = [w for w in wmap if w["qubit"] == "qA1"][0]
        assert qa1["xy_opx_output"] == "MW-FEM/1/2"
        assert qa1["z_opx_output"] == "LF-FEM/5/1"


# ---------------------------------------------------------------------------
# get_topology
# ---------------------------------------------------------------------------


class TestGetTopology:
    def test_topology_nodes(self, engine):
        topo = engine.get_topology()
        assert len(topo["nodes"]) == 2
        ids = {n["id"] for n in topo["nodes"]}
        assert ids == {"qA1", "qA2"}

    def test_topology_is_cached_until_invalidated(self, engine):
        """get_topology() is recomputed only after invalidate_cache() (called on
        every store mutation), so repeated /topology renders don't rebuild it."""
        t1 = engine.get_topology()
        assert engine.get_topology() is t1          # cached: same object
        engine.invalidate_cache()
        t2 = engine.get_topology()
        assert t2 is not t1                          # recomputed after invalidation
        assert t2 == t1                              # ...but identical content

    def test_topology_chain(self, engine):
        topo = engine.get_topology()
        chains = {n["id"]: n["chain"] for n in topo["nodes"]}
        assert chains["qA1"] == "A"

    def test_topology_edges(self, engine):
        topo = engine.get_topology()
        assert len(topo["edges"]) == 1
        edge = topo["edges"][0]
        assert edge["pair_id"] == "qA1-A2"
        assert edge["source"] == "qA2"
        assert edge["target"] == "qA1"
        assert edge["has_cz"] is True
        assert edge["cz_fidelity"] == 0.85


# ---------------------------------------------------------------------------
# _extract_pair_gate_fidelities — canonical `value` across schema shapes
# ---------------------------------------------------------------------------


class TestExtractPairGateFidelities:
    """The UI (2Q RB panels, Overview tiles) reads a single ``value`` per metric.

    Two schemas exist in the wild: older data stores a bare float per metric,
    newer LabA data nests a dict (StandardRB.average_gate_fidelity,
    Bell_State.Fidelity). Both must yield a canonical numeric ``value``.
    """

    def _by_metric(self, macros):
        return {e["metric"]: e for e in _extract_pair_gate_fidelities(macros)}

    def test_scalar_metric_keeps_value(self):
        out = self._by_metric({"cz_flattop": {"fidelity": {"StandardRB": 0.55}}})
        assert out["StandardRB"]["value"] == 0.55

    def test_nested_standard_rb_uses_average_gate_fidelity(self):
        macros = {
            "cz_unipolar": {
                "fidelity": {
                    "StandardRB": {
                        "alpha": 0.91,
                        "error_per_gate": 0.0128,
                        "average_gate_fidelity": 0.9871,
                    }
                }
            }
        }
        entry = self._by_metric(macros)["StandardRB"]
        assert entry["value"] == 0.9871
        # the other numerics are still carried through
        assert entry["error_per_gate"] == 0.0128

    def test_nested_bell_state_uses_fidelity(self):
        macros = {"cz_unipolar": {"fidelity": {"Bell_State": {"Purity": 0.95, "Fidelity": 0.9508}}}}
        assert self._by_metric(macros)["Bell_State"]["value"] == 0.9508

    def test_dict_without_known_key_has_no_value(self):
        # A dict metric with numerics but no average_gate_fidelity/Fidelity is
        # still kept, just without a canonical value.
        macros = {"cz_unipolar": {"fidelity": {"Weird": {"some_number": 0.5}}}}
        entry = self._by_metric(macros)["Weird"]
        assert "value" not in entry
        assert entry["some_number"] == 0.5

    def test_load_id_keys_are_not_emitted_as_rows(self):
        # *_load_id is provenance, not a measurement — it must NOT become a
        # "fidelity" row (it used to render as e.g. "529.0000").
        macros = {"cz_flattop": {"fidelity": {
            "StandardRB": 0.97, "StandardRB_load_id": 529,
            "Bell_State": {"Fidelity": 0.96},
        }}}
        rows = _extract_pair_gate_fidelities(macros)
        metrics = {r["metric"] for r in rows}
        assert "StandardRB_load_id" not in metrics
        assert metrics == {"StandardRB", "Bell_State"}

    def test_load_id_attached_to_each_row_of_the_gate(self):
        macros = {"cz_flattop": {"fidelity": {
            "StandardRB": 0.97, "StandardRB_load_id": 529,
            "Bell_State": {"Fidelity": 0.96},
        }}}
        by = self._by_metric(macros)
        assert by["StandardRB"]["load_id"] == 529
        assert by["Bell_State"]["load_id"] == 529

    def test_interleaved_load_id_fallback(self):
        macros = {"cz_x": {"fidelity": {"InterleavedRB": 0.9, "InterleavedRB_load_id": 77}}}
        assert self._by_metric(macros)["InterleavedRB"]["load_id"] == 77

    def test_load_id_is_none_when_absent(self):
        # No *_load_id recorded → rows carry load_id None (not missing/crash).
        macros = {"cz_x": {"fidelity": {"StandardRB": 0.9}}}
        assert self._by_metric(macros)["StandardRB"]["load_id"] is None


# ---------------------------------------------------------------------------
# Safe filter evaluator
# ---------------------------------------------------------------------------


class TestEvalFilter:
    def test_gt(self):
        assert _eval_filter({"x": 10}, "x > 5") is True
        assert _eval_filter({"x": 3}, "x > 5") is False

    def test_eq_string(self):
        assert _eval_filter({"z_flux_point": "joint"}, "z_flux_point == 'joint'") is True

    def test_and(self):
        assert _eval_filter({"a": 10, "b": 20}, "a > 5 and b > 15") is True
        assert _eval_filter({"a": 10, "b": 10}, "a > 5 and b > 15") is False

    def test_or(self):
        assert _eval_filter({"a": 3, "b": 20}, "a > 5 or b > 15") is True

    def test_not(self):
        assert _eval_filter({"a": 3}, "not a > 5") is True

    def test_none_value(self):
        assert _eval_filter({"a": None}, "a > 5") is False

    def test_invalid_syntax(self):
        assert _eval_filter({"a": 1}, "a >>> 5") is True

    def test_unknown_property(self):
        assert _eval_filter({"a": 1}, "unknown_prop > 5") is True


# ---------------------------------------------------------------------------
# Real data tests
# ---------------------------------------------------------------------------


@skip_no_small
class TestSmallRealData:
    def test_get_qubit_q1(self):
        store = QuamStore(SMALL_FOLDER)
        eng = QueryEngine(store)
        q = eng.get_qubit("q1")
        assert q["id"] == "q1"
        assert q["f_01"] is not None

    def test_list_all(self):
        store = QuamStore(SMALL_FOLDER)
        eng = QueryEngine(store)
        qubits = eng.list_qubits()
        assert len(qubits) == 3

    def test_wiring_map(self):
        store = QuamStore(SMALL_FOLDER)
        eng = QueryEngine(store)
        wmap = eng.get_wiring_map()
        assert len(wmap) == 3

    def test_topology(self):
        store = QuamStore(SMALL_FOLDER)
        eng = QueryEngine(store)
        topo = eng.get_topology()
        assert len(topo["nodes"]) == 3
        assert len(topo["edges"]) >= 2


@skip_no_large
class TestLargeRealData:
    def test_get_qubit_qA1(self):
        store = QuamStore(LARGE_FOLDER)
        eng = QueryEngine(store)
        q = eng.get_qubit("qA1")
        assert q["id"] == "qA1"
        assert q["f_01"] == 6255526125.489208
        assert q["anharmonicity"] == 258250000.0
        assert q["x180_amplitude"] == 0.11452461948325875
        assert q["readout_amplitude"] == 0.042260574977507265
        assert q["readout_threshold"] == -0.00013888722194480328
        assert q["gate_fidelity_avg"] == 0.9909329428914645

    def test_get_pair_qA1_A2(self):
        store = QuamStore(LARGE_FOLDER)
        eng = QueryEngine(store)
        p = eng.get_pair("qA1-A2")
        assert p["qubit_control"] == "qA2"
        assert p["qubit_target"] == "qA1"
        assert p["cz_flattop_amplitude"] == 0.03200058441840985
        assert p["coupler_decouple_offset"] == 0.4800000000000003

    def test_list_all_qubits(self):
        store = QuamStore(LARGE_FOLDER)
        eng = QueryEngine(store)
        qubits = eng.list_qubits()
        assert len(qubits) >= 16

    def test_filter_by_frequency(self):
        store = QuamStore(LARGE_FOLDER)
        eng = QueryEngine(store)
        qubits = eng.list_qubits("f_01 > 6e9")
        assert len(qubits) >= 1
        assert all(q["f_01"] > 6e9 for q in qubits)

    def test_summary_table(self):
        store = QuamStore(LARGE_FOLDER)
        eng = QueryEngine(store)
        table = eng.summary_table(["f_01", "T2ramsey", "readout_amplitude"])
        assert len(table) >= 16
        assert all("f_01" in row for row in table)

    def test_wiring_map(self):
        store = QuamStore(LARGE_FOLDER)
        eng = QueryEngine(store)
        wmap = eng.get_wiring_map()
        assert len(wmap) >= 16

    def test_topology_full(self):
        store = QuamStore(LARGE_FOLDER)
        eng = QueryEngine(store)
        topo = eng.get_topology()
        assert len(topo["nodes"]) >= 16
        assert len(topo["edges"]) >= 20
        chains = {n["chain"] for n in topo["nodes"]}
        assert "A" in chains
        assert "B" in chains
