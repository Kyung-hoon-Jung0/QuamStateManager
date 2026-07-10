"""Tests for QuamStore.from_dicts and the diagnostics linter (core/diagnostics.py).

All fixtures are synthetic in-memory dicts — no disk, no QM stack. Port
references use the canonical ``#/ports/<type>/<ctrl>/<fem>/<port>`` pointer
form and the matching ``ports`` section is the source of truth for existence.
"""

from __future__ import annotations

import pytest

from quam_state_manager.core import diagnostics, safe_io
from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.query import QueryEngine


# ---------------------------------------------------------------------------
# Healthy synthetic chip (zero findings)
# ---------------------------------------------------------------------------

def _healthy_state() -> dict:
    return {
        "qubits": {
            "q1": {"id": "q1", "f_01": 5.0e9, "anharmonicity": -2.0e8, "T1": 1.0e-5},
            "q2": {"id": "q2", "f_01": 5.1e9, "anharmonicity": -2.1e8, "T1": 1.1e-5},
        },
        "qubit_pairs": {},
        "ports": {
            "mw_outputs": {"con1": {"1": {
                "1": {"upconverter_frequency": 6.0e9},
                "3": {"upconverter_frequency": 6.0e9},
                "4": {"upconverter_frequency": 6.0e9},
            }}},
            "analog_outputs": {"con2": {"1": {"1": {}, "2": {}}}},
            "mw_inputs": {"con1": {"1": {"1": {
                "downconverter_frequency": "#/ports/mw_outputs/con1/1/1/upconverter_frequency",
            }}}},
        },
    }


def _healthy_wiring() -> dict:
    return {
        "wiring": {
            "qubits": {
                "q1": {
                    "xy": {"opx_output": "#/ports/mw_outputs/con1/1/3"},
                    "rr": {"opx_output": "#/ports/mw_outputs/con1/1/1",
                           "opx_input": "#/ports/mw_inputs/con1/1/1"},
                    "z": {"opx_output": "#/ports/analog_outputs/con2/1/1"},
                },
                "q2": {
                    "xy": {"opx_output": "#/ports/mw_outputs/con1/1/4"},
                    # readout multiplexed onto the same physical ports — legal
                    "rr": {"opx_output": "#/ports/mw_outputs/con1/1/1",
                           "opx_input": "#/ports/mw_inputs/con1/1/1"},
                    "z": {"opx_output": "#/ports/analog_outputs/con2/1/2"},
                },
            },
        },
        "network": {"host": "10.0.0.1", "cluster_name": "test"},
    }


def _store(state=None, wiring=None) -> QuamStore:
    return QuamStore.from_dicts(state or _healthy_state(), wiring or _healthy_wiring())


def _cats(findings):
    out = {}
    for f in findings:
        out.setdefault(f.category, []).append(f)
    return out


def _freq_state(f01=5.0e9, xy_rf=5.0e9, res_f01=7.0e9, res_rf=7.0e9, rf_pointer=False):
    """One qubit carrying the f_01 / RF_frequency pair on both the xy drive and the
    resonator. None drops a field; ``rf_pointer`` hard-links xy.RF_frequency to
    f_01 via a #/ reference (should resolve equal → no finding)."""
    q = {"id": "q1", "f_01": f01}
    xy = {}
    if xy_rf is not None:
        xy["RF_frequency"] = "#/qubits/q1/f_01" if rf_pointer else xy_rf
    if xy:
        q["xy"] = xy
    res = {}
    if res_f01 is not None:
        res["f_01"] = res_f01
    if res_rf is not None:
        res["RF_frequency"] = res_rf
    if res:
        q["resonator"] = res
    return {"qubits": {"q1": q}, "qubit_pairs": {}}


# ---------------------------------------------------------------------------
# QuamStore.from_dicts
# ---------------------------------------------------------------------------

class TestFromDicts:
    def test_builds_merged(self):
        s = _store()
        assert s.folder_path is None
        assert "qubits" in s.merged and "wiring" in s.merged and "ports" in s.merged
        assert s.qubit_names == ["q1", "q2"]

    def test_repr_does_not_crash_without_path(self):
        assert "in-memory" in repr(_store())

    def test_type_error_on_non_dict(self):
        with pytest.raises(TypeError):
            QuamStore.from_dicts("nope", {})
        with pytest.raises(TypeError):
            QuamStore.from_dicts({}, ["nope"])

    def test_no_disk_io(self, monkeypatch):
        def boom(*a, **k):
            raise AssertionError("from_dicts must not touch disk")
        monkeypatch.setattr(safe_io, "read_state_wiring", boom)
        s = QuamStore.from_dicts(_healthy_state(), _healthy_wiring())
        assert s.qubit_names == ["q1", "q2"]

    def test_instrument_wiring_roundtrips_against_path_load(self, tmp_path):
        import json
        state, wiring = _healthy_state(), _healthy_wiring()
        (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
        (tmp_path / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")
        from_path = QueryEngine(QuamStore(tmp_path)).get_instrument_wiring()
        from_mem = QueryEngine(QuamStore.from_dicts(state, wiring)).get_instrument_wiring()
        assert from_path == from_mem


# ---------------------------------------------------------------------------
# lint_state per-mutation cache (B21)
# ---------------------------------------------------------------------------

class TestLintStateCache:
    def test_repeat_call_served_from_cache(self, monkeypatch):
        """Two consecutive lint_state calls at the same mutation_seq recompute
        the underlying pass only once."""
        store = _store()
        calls = {"n": 0}
        real = diagnostics._lint_state_uncached

        def counting(s):
            calls["n"] += 1
            return real(s)

        monkeypatch.setattr(diagnostics, "_lint_state_uncached", counting)

        a = diagnostics.lint_state(store)
        b = diagnostics.lint_state(store)
        assert calls["n"] == 1  # second call hit the cache
        # Equal contents, but a FRESH list each time (callers sort in place).
        assert [f.as_dict() for f in a] == [f.as_dict() for f in b]
        assert a is not b

    def test_mutation_busts_cache(self):
        """A real edit (modifier.set_value bumps mutation_seq) makes the next
        lint_state reflect the change."""
        from quam_state_manager.core.modifier import Modifier

        # A wiring port reference that points nowhere -> port_missing error.
        w = _healthy_wiring()
        w["wiring"]["qubits"]["q1"]["xy"]["opx_output"] = "#/ports/mw_outputs/con1/9/9"
        store = _store(wiring=w)

        before = _cats(diagnostics.lint_state(store))
        assert "port_missing" in before

        # Repoint xy back onto an existing port; mutation_seq increments.
        seq0 = store.mutation_seq
        Modifier(store).set_value(
            "wiring.qubits.q1.xy.opx_output", "#/ports/mw_outputs/con1/1/3")
        assert store.mutation_seq > seq0

        after = _cats(diagnostics.lint_state(store))
        assert "port_missing" not in after  # cache busted, finding cleared

    def test_pointer_warnings_cache_busts_on_mutation(self):
        """_validate_pointers_cached reuses the walk at one seq, recomputes after."""
        store = _store()
        a = diagnostics._validate_pointers_cached(store)
        b = diagnostics._validate_pointers_cached(store)
        assert a is b  # same cached object at the same mutation_seq
        store.mutation_seq += 1
        c = diagnostics._validate_pointers_cached(store)
        assert c is not a


# ---------------------------------------------------------------------------
# lint_state
# ---------------------------------------------------------------------------

class TestLintState:
    def test_healthy_chip_has_no_findings(self):
        assert diagnostics.lint_state(_store()) == []

    def test_port_missing(self):
        w = _healthy_wiring()
        w["wiring"]["qubits"]["q1"]["xy"]["opx_output"] = "#/ports/mw_outputs/con1/9/9"
        cats = _cats(diagnostics.lint_state(_store(wiring=w)))
        assert "port_missing" in cats
        f = cats["port_missing"][0]
        assert f.severity == "error"
        assert f.port_key == {"ctrl": "con1", "fem": "9", "port": "9",
                              "port_type": "mw_outputs", "io": "out"}
        assert f.jump_path == "wiring.qubits.q1.xy.opx_output"

    def test_port_collision_flagged(self):
        w = _healthy_wiring()
        # two z (flux) lines collide on one analog port — illegal
        w["wiring"]["qubits"]["q2"]["z"]["opx_output"] = "#/ports/analog_outputs/con2/1/1"
        cats = _cats(diagnostics.lint_state(_store(wiring=w)))
        assert "port_collision" in cats
        assert cats["port_collision"][0].severity == "error"

    def test_readout_multiplex_not_flagged(self):
        # The healthy fixture already multiplexes q1/q2 readout on one port.
        cats = _cats(diagnostics.lint_state(_store()))
        assert "port_collision" not in cats

    def test_cr_shared_port_not_flagged(self):
        # CR topology: a qubit xy (upconverter 1) + one-or-more cross-resonance
        # drives (upconverter 2, IF-multiplexed) legitimately share ONE MW-FEM
        # output port. Must NOT be flagged as a collision.
        s = _healthy_state()
        s["ports"]["mw_outputs"]["con1"]["1"]["3"] = {
            "upconverters": {"1": {"frequency": 6.0e9}, "2": {"frequency": 6.2e9}}}
        s["qubit_pairs"] = {
            "q1-2": {"cross_resonance": {"upconverter": 2}},
            "q1-3": {"cross_resonance": {"upconverter": 2}},  # 2nd CR drive, same uc (IF-mux)
        }
        w = _healthy_wiring()
        w["wiring"]["qubit_pairs"] = {
            "q1-2": {"cr": {"opx_output": "#/ports/mw_outputs/con1/1/3"}},  # q1.xy's port
            "q1-3": {"cr": {"opx_output": "#/ports/mw_outputs/con1/1/3"}},
        }
        cats = _cats(diagnostics.lint_state(_store(state=s, wiring=w)))
        assert "port_collision" not in cats

    def test_two_drives_same_port_upconverter_flagged(self):
        # A genuine clash: two qubit xy drives on the SAME output port +
        # upconverter (both implicit uc1) is still an error.
        w = _healthy_wiring()
        w["wiring"]["qubits"]["q2"]["xy"]["opx_output"] = "#/ports/mw_outputs/con1/1/3"
        cats = _cats(diagnostics.lint_state(_store(wiring=w)))
        assert "port_collision" in cats
        assert cats["port_collision"][0].severity == "error"

    def test_multi_upconverter_band_range_checked(self):
        # A MULTI-upconverter port (upconverters dict) must have EACH upconverter
        # frequency band-checked — the scalar upconverter_frequency check skips it.
        s = _healthy_state()
        s["ports"]["mw_outputs"]["con1"]["1"]["5"] = {
            "band": 3,  # band 3 range ~[6.5, 10.5] GHz
            "upconverters": {"1": {"frequency": 9.0e9},      # in band
                             "2": {"frequency": 12.0e9}},     # OUT of band 3
        }
        fr = _cats(diagnostics.lint_state(_store(state=s))).get("connectivity_freq", [])
        assert any("upconverter 2" in f.message for f in fr)
        assert not any("upconverter 1" in f.message for f in fr)

    def test_dangling_pointer(self):
        s = _healthy_state()
        s["qubits"]["q1"]["anharmonicity"] = "#/qubits/NOPE/f_01"
        cats = _cats(diagnostics.lint_state(_store(state=s)))
        assert "dangling_pointer" in cats
        # a missing port is reported as port_missing, never doubled as dangling
        for f in cats["dangling_pointer"]:
            assert not f.detail.startswith("#/ports/")

    def test_value_nan(self):
        s = _healthy_state()
        s["qubits"]["q1"]["T1"] = float("nan")
        cats = _cats(diagnostics.lint_state(_store(state=s)))
        assert "value_nan" in cats and cats["value_nan"][0].severity == "error"

    def test_value_type_mismatch(self):
        s = _healthy_state()
        s["qubits"]["q3"] = {"id": "q3", "f_01": 5.2e9}
        s["qubits"]["q4"] = {"id": "q4", "f_01": "oops"}  # text where 3 siblings are numeric
        cats = _cats(diagnostics.lint_state(_store(state=s)))
        assert "value_type" in cats
        f = cats["value_type"][0]
        assert f.severity == "warning" and f.location == "qubits.q4.f_01"

    def test_downconverter_literal_info(self):
        s = _healthy_state()
        s["ports"]["mw_inputs"]["con1"]["1"]["1"]["downconverter_frequency"] = 6.0e9
        cats = _cats(diagnostics.lint_state(_store(state=s)))
        assert "downconverter_literal" in cats
        assert cats["downconverter_literal"][0].severity == "info"


# ---------------------------------------------------------------------------
# lint_config
# ---------------------------------------------------------------------------

def _healthy_config() -> dict:
    return {
        "version": 1,
        "controllers": {"con1": {}},
        "elements": {"q1.xy": {"operations": {"x180": "x180.pulse"},
                               "mixInputs": {"I": ["con1", 1], "Q": ["con1", 2], "mixer": "mx1"}}},
        "pulses": {"x180.pulse": {"waveforms": {"I": "wf_i", "Q": "wf_q"},
                                  "integration_weights": {"w": "iw1"}}},
        "waveforms": {"wf_i": {"type": "arbitrary", "samples": [0.1]},
                      "wf_q": {"type": "constant", "sample": 0.0}},
        "integration_weights": {"iw1": {}},
        "mixers": {"mx1": []},
    }


class TestLintConfig:
    def test_healthy_config_has_no_errors(self):
        findings = diagnostics.lint_config(_healthy_config())
        assert [f for f in findings if f.severity == "error"] == []

    def test_missing_refs(self):
        cfg = _healthy_config()
        cfg["elements"]["q1.xy"]["operations"]["x180"] = "ghost.pulse"   # missing pulse
        cfg["pulses"]["x180.pulse"]["waveforms"]["I"] = "ghost_wf"       # missing waveform
        cfg["pulses"]["x180.pulse"]["integration_weights"]["w"] = "ghost_iw"  # missing iw
        cfg["elements"]["q1.xy"]["mixInputs"]["mixer"] = "ghost_mx"      # missing mixer
        cfg["elements"]["q1.xy"]["mixInputs"]["I"] = ["conZ", 1]         # missing controller
        cats = _cats(diagnostics.lint_config(cfg))
        for cat in ("config_missing_pulse", "config_missing_waveform",
                    "config_missing_iw", "config_missing_mixer", "config_missing_controller"):
            assert cat in cats, cat
            assert cats[cat][0].severity == "error"

    def test_orphans_are_warnings(self):
        cfg = _healthy_config()
        cfg["waveforms"]["wf_unused"] = {"type": "constant", "sample": 0.0}
        cfg["pulses"]["unused.pulse"] = {"waveforms": {"single": "wf_unused"}}
        cats = _cats(diagnostics.lint_config(cfg))
        assert "config_orphan_pulse" in cats
        assert cats["config_orphan_pulse"][0].severity == "warning"

    def test_no_version_is_not_flagged(self):
        # A missing top-level 'version' key is no longer flagged — the in-house
        # generator output doesn't carry it and it isn't required (feedback
        # Config #1). It must NOT produce a finding of any severity.
        cfg = _healthy_config()
        del cfg["version"]
        cats = _cats(diagnostics.lint_config(cfg))
        assert "config_no_version" not in cats


# ---------------------------------------------------------------------------
# summarize / ordering
# ---------------------------------------------------------------------------

def test_summarize_counts():
    s = _healthy_state()
    s["qubits"]["q1"]["T1"] = float("inf")          # error
    s["qubits"]["q1"]["anharmonicity"] = "#/x/NOPE"  # error
    s["ports"]["mw_inputs"]["con1"]["1"]["1"]["downconverter_frequency"] = 6e9  # info
    findings = diagnostics.lint_state(_store(state=s))
    summ = diagnostics.summarize(findings)
    assert summ["total"] == len(findings)
    assert summ["error"] >= 2 and summ["info"] >= 1
    # errors are ordered before info
    sev = [f.severity for f in findings]
    assert sev == sorted(sev, key=lambda x: {"error": 0, "warning": 1, "info": 2}[x])


def test_summarize_buckets_advisory_separately():
    """A14: an advisory (severity='warning' + advisory=True) must NOT inflate the
    'warning' count — it belongs to its own 'advisory' bucket so the header/tray
    badge stops contradicting the Diagnostics page."""
    findings = [
        diagnostics.Finding("warning", "value_spec_range", "q1.x", "real defect"),
        diagnostics.Finding("warning", "connectivity_band_edge", "con1/p1",
                             "near edge — optional", advisory=True),
        diagnostics.Finding("warning", "connectivity_band_edge", "con1/p2",
                             "near edge — optional", advisory=True),
    ]
    summ = diagnostics.summarize(findings)
    assert summ["warning"] == 1      # only the real defect
    assert summ["advisory"] == 2     # the two recommendations
    assert summ["total"] == 3
    # buckets partition the findings exactly (no double counting / no drops)
    assert summ["error"] + summ["warning"] + summ["info"] + summ["advisory"] == summ["total"]


# ---------------------------------------------------------------------------
# Hardware value-spec (core/spec_constraints via diagnostics._spec_findings)
# ---------------------------------------------------------------------------

def _with_resonator(**fields) -> dict:
    s = _healthy_state()
    s["qubits"]["q1"]["resonator"] = fields
    return s


class TestValueSpec:
    def test_tof_not_multiple_of_4(self):
        cats = _cats(diagnostics.lint_state(_store(state=_with_resonator(time_of_flight=421))))
        assert "value_spec_tof" in cats and cats["value_spec_tof"][0].severity == "warning"
        assert cats["value_spec_tof"][0].jump_path == "qubits.q1.resonator.time_of_flight"

    def test_tof_below_min(self):
        cats = _cats(diagnostics.lint_state(_store(state=_with_resonator(time_of_flight=12))))
        assert "value_spec_tof" in cats

    def test_tof_valid_not_flagged(self):
        cats = _cats(diagnostics.lint_state(_store(state=_with_resonator(time_of_flight=280))))
        assert "value_spec_tof" not in cats

    def test_tof_pointer_skipped(self):
        cats = _cats(diagnostics.lint_state(_store(state=_with_resonator(time_of_flight="#./tof"))))
        assert "value_spec_tof" not in cats

    def test_full_scale_power_out_of_range(self):
        s = _healthy_state()
        s["ports"]["mw_outputs"]["con1"]["1"]["1"]["full_scale_power_dbm"] = 25
        cats = _cats(diagnostics.lint_state(_store(state=s)))
        assert "value_spec_power" in cats

    @pytest.mark.parametrize("dbm", [-11, -2, 0, 10, 18])
    def test_full_scale_power_in_range_ok(self, dbm):
        s = _healthy_state()
        s["ports"]["mw_outputs"]["con1"]["1"]["1"]["full_scale_power_dbm"] = dbm
        cats = _cats(diagnostics.lint_state(_store(state=s)))
        assert "value_spec_power" not in cats

    def test_band_invalid_is_error(self):
        s = _healthy_state()
        s["ports"]["mw_outputs"]["con1"]["1"]["1"]["band"] = 4
        cats = _cats(diagnostics.lint_state(_store(state=s)))
        assert "value_spec_band" in cats and cats["value_spec_band"][0].severity == "error"

    @pytest.mark.parametrize("band,freq", [(1, 1.0e9), (2, 5.0e9), (3, 7.0e9)])
    def test_band_valid_ok(self, band, freq):
        s = _healthy_state()
        p = s["ports"]["mw_outputs"]["con1"]["1"]["1"]
        p["band"] = band
        p["upconverter_frequency"] = freq  # in-band so connectivity_freq stays quiet
        cats = _cats(diagnostics.lint_state(_store(state=s)))
        assert "value_spec_band" not in cats

    def test_xy_if_too_high(self):
        # Ceiling is the MW-FEM/anti-alias limit of 500 MHz; 600 MHz is over it.
        s = _healthy_state()
        s["qubits"]["q1"]["xy"] = {"intermediate_frequency": 600e6}
        cats = _cats(diagnostics.lint_state(_store(state=s)))
        assert "value_spec_if" in cats

    def test_if_ceiling_is_500mhz_for_both(self):
        # Raised from the old 400/440 MHz: real production resonators reach
        # |IF| ≈ 485 MHz, so 485 must stay clean on xy AND resonator; 600 flags.
        for chan in ("xy", "resonator"):
            s = _healthy_state()
            s["qubits"]["q1"][chan] = {"intermediate_frequency": 485e6}
            assert "value_spec_if" not in _cats(diagnostics.lint_state(_store(state=s)))
        bad = _cats(diagnostics.lint_state(_store(state=_with_resonator(intermediate_frequency=600e6))))
        assert "value_spec_if" in bad

    def test_smearing_exceeds_tof_minus_8(self):
        cats = _cats(diagnostics.lint_state(_store(state=_with_resonator(time_of_flight=100, smearing=96))))
        assert "value_spec_smearing" in cats

    def test_smearing_within_bound_ok(self):
        cats = _cats(diagnostics.lint_state(_store(state=_with_resonator(time_of_flight=100, smearing=20))))
        assert "value_spec_smearing" not in cats

    def test_pulse_length_not_multiple_of_4(self):
        s = _healthy_state()
        s["qubits"]["q1"]["xy"] = {"operations": {"x180": {"length": 18}}}
        cats = _cats(diagnostics.lint_state(_store(state=s)))
        assert "value_spec_length" in cats

    def test_pulse_length_pointer_and_large_ok(self):
        s = _healthy_state()
        s["qubits"]["q1"]["xy"] = {"operations": {
            "x90": {"length": "#../x180/length"},   # pointer → skipped
            "saturation": {"length": 20000},         # large but %4 → ok
        }}
        cats = _cats(diagnostics.lint_state(_store(state=s)))
        assert "value_spec_length" not in cats

    def test_physics_params_never_flagged(self):
        # legitimately out-of-"range" physics values must NOT produce findings
        s = _healthy_state()
        s["qubits"]["q1"]["T2echo"] = -3.0e-5         # negative T2 is legal
        s["qubits"]["q1"]["anharmonicity"] = 1.0e9
        s["qubits"]["q1"]["resonator"] = {"f_01": 7.0e9, "time_of_flight": 280}
        cats = _cats(diagnostics.lint_state(_store(state=s)))
        assert not any(c.startswith("value_spec") for c in cats)


# ---------------------------------------------------------------------------
# OPX1000 connectivity coupling (diagnostics._coupling_findings)
# ---------------------------------------------------------------------------

class TestCoupling:
    @staticmethod
    def _coupled(band_a, band_b, freq=5.0e9):
        s = _healthy_state()
        # On one FEM, Out2 & Out3 are a coupled pair → must share a band.
        s["ports"]["mw_outputs"]["con1"]["1"]["2"] = {"band": band_a, "upconverter_frequency": freq}
        s["ports"]["mw_outputs"]["con1"]["1"]["3"] = {"band": band_b, "upconverter_frequency": freq}
        return s

    def test_coupled_pair_incompatible_bands(self):
        cats = _cats(diagnostics.lint_state(_store(state=self._coupled(1, 2))))  # 1&2 NOT compatible
        assert "connectivity_band" in cats
        f = cats["connectivity_band"][0]
        assert f.severity == "warning" and f.port_key is not None
        # both ports of the pair should be flagged so the diagram rings both
        assert len(cats["connectivity_band"]) == 2

    def test_coupled_pair_compatible_bands_ok(self):
        s = self._coupled(1, 3)                       # 1&3 ARE compatible
        s["ports"]["mw_outputs"]["con1"]["1"]["2"]["upconverter_frequency"] = 5.0e9   # band1 ok
        s["ports"]["mw_outputs"]["con1"]["1"]["3"]["upconverter_frequency"] = 7.0e9   # band3 ok
        cats = _cats(diagnostics.lint_state(_store(state=s)))
        assert "connectivity_band" not in cats

    def test_band_freq_out_of_range(self):
        s = _healthy_state()
        p = s["ports"]["mw_outputs"]["con1"]["1"]["1"]
        p["band"] = 1
        p["upconverter_frequency"] = 9.0e9            # band 1 max is 5.5e9
        cats = _cats(diagnostics.lint_state(_store(state=s)))
        assert "connectivity_freq" in cats
        assert cats["connectivity_freq"][0].port_key is not None


# ---------------------------------------------------------------------------
# TWPA pump/pump_ self-share (one element legitimately on one port)
# ---------------------------------------------------------------------------

class TestTwpaPumpShare:
    @staticmethod
    def _store_with_twpas(twpas: dict) -> QuamStore:
        s = _healthy_state()
        # the shared pump ports must exist so port_missing doesn't fire
        s["ports"]["mw_outputs"]["con1"]["1"]["6"] = {}
        s["ports"]["mw_outputs"]["con1"]["1"]["7"] = {}
        w = _healthy_wiring()
        w["wiring"]["twpas"] = twpas
        return QuamStore.from_dicts(s, w)

    def test_same_twpa_pump_and_pump_underscore_share_one_port_ok(self):
        # the real-data pattern: twpa0.pump and twpa0.pump_ both on con1/1/6
        store = self._store_with_twpas({
            "twpa0": {
                "pump":  {"opx_output": "#/ports/mw_outputs/con1/1/6"},
                "pump_": {"opx_output": "#/ports/mw_outputs/con1/1/6"},
            },
        })
        cats = _cats(diagnostics.lint_state(store))
        assert "port_collision" not in cats

    def test_two_different_twpas_on_one_port_still_collide(self):
        # different elements sharing a port is a real collision, still flagged
        store = self._store_with_twpas({
            "twpa0": {"pump": {"opx_output": "#/ports/mw_outputs/con1/1/6"}},
            "twpa1": {"pump": {"opx_output": "#/ports/mw_outputs/con1/1/6"}},
        })
        cats = _cats(diagnostics.lint_state(store))
        assert "port_collision" in cats
        assert cats["port_collision"][0].severity == "error"


# ---------------------------------------------------------------------------
# Downconverter literal -> pointer suggestion (compare to paired upconverter)
# ---------------------------------------------------------------------------

class TestDownconverterFix:
    # healthy fixture: q1.rr pairs mw_inputs/con1/1/1 <-> mw_outputs/con1/1/1,
    # the output upconverter is 6.0e9, the input downconverter is a pointer.

    def test_pointer_downconverter_not_flagged(self):
        assert "downconverter_literal" not in _cats(diagnostics.lint_state(_store()))

    def test_literal_equal_to_paired_output_is_info_with_fix(self):
        s = _healthy_state()
        s["ports"]["mw_inputs"]["con1"]["1"]["1"]["downconverter_frequency"] = 6.0e9  # == paired up
        f = _cats(diagnostics.lint_state(_store(state=s)))["downconverter_literal"][0]
        assert f.severity == "info"
        assert f.fix and f.fix["action"] == "set_pointer"
        assert f.fix["pointer"] == "#/ports/mw_outputs/con1/1/1/upconverter_frequency"
        assert f.fix["matches"] is True
        assert f.jump_path == "ports.mw_inputs.con1.1.1.downconverter_frequency"

    def test_literal_differs_is_warning_with_fix(self):
        s = _healthy_state()
        s["ports"]["mw_inputs"]["con1"]["1"]["1"]["downconverter_frequency"] = 6.1e9  # != 6.0e9
        f = _cats(diagnostics.lint_state(_store(state=s)))["downconverter_literal"][0]
        assert f.severity == "warning"
        assert f.fix["matches"] is False
        assert f.fix["pointer"] == "#/ports/mw_outputs/con1/1/1/upconverter_frequency"

    def test_no_paired_output_keeps_info_without_fix(self):
        s = _healthy_state()
        s["ports"]["mw_inputs"]["con1"]["1"]["1"]["downconverter_frequency"] = 6.0e9
        w = _healthy_wiring()
        # break the pairing: remove the readout input refs so no output is found
        for q in w["wiring"]["qubits"].values():
            q["rr"].pop("opx_input", None)
        f = _cats(diagnostics.lint_state(_store(state=s, wiring=w)))["downconverter_literal"][0]
        assert f.severity == "info" and f.fix is None


# ---------------------------------------------------------------------------
# f_01 ↔ RF_frequency consistency
# ---------------------------------------------------------------------------

class TestFrequencyConsistency:
    """RF_frequency is what the hardware plays; f_01 is bookkeeping the calibration
    keeps equal to it. Flag drift advisorily (info/warning, never error), tiered by
    magnitude, on both the xy drive and the resonator readout."""

    def _find(self, **kw):
        store = QuamStore.from_dicts(_freq_state(**kw), {})
        return diagnostics._frequency_consistency_findings(store)

    def test_equal_no_finding(self):
        assert self._find(f01=5e9, xy_rf=5e9, res_f01=7e9, res_rf=7e9) == []

    def test_qubit_drive_mismatch_warns(self):
        fs = self._find(f01=5.002e9, xy_rf=5.0e9, res_f01=7e9, res_rf=7e9)
        assert len(fs) == 1
        f = fs[0]
        assert f.severity == "warning"
        assert f.category == "value_freq_consistency"
        assert f.jump_path == "qubits.q1.f_01"
        assert "qubit drive" in f.message

    def test_small_mismatch_is_info(self):
        fs = self._find(f01=5.0e9 + 50, xy_rf=5.0e9, res_f01=7e9, res_rf=7e9)
        assert len(fs) == 1 and fs[0].severity == "info"

    def test_below_floor_no_finding(self):
        # < 1 Hz is float/rounding noise — not surfaced at all.
        assert self._find(f01=5.0e9 + 0.5, xy_rf=5.0e9, res_f01=7e9, res_rf=7e9) == []

    def test_resonator_mismatch_labeled_readout(self):
        fs = self._find(f01=5e9, xy_rf=5e9, res_f01=7.0e9, res_rf=7.0012e9)
        assert len(fs) == 1
        assert fs[0].severity == "warning"
        assert fs[0].jump_path == "qubits.q1.resonator.f_01"
        assert "readout" in fs[0].message

    def test_both_pairs_can_fire(self):
        fs = self._find(f01=5.01e9, xy_rf=5.0e9, res_f01=7.01e9, res_rf=7.0e9)
        assert {f.jump_path for f in fs} == {"qubits.q1.f_01", "qubits.q1.resonator.f_01"}

    def test_pointer_rf_resolves_equal_no_finding(self):
        # xy.RF_frequency = "#/qubits/q1/f_01" → resolves to f_01 → consistent.
        assert self._find(f01=5.0e9, rf_pointer=True, res_f01=7e9, res_rf=7e9) == []

    def test_missing_fields_skipped(self):
        assert self._find(f01=5e9, xy_rf=None, res_f01=None, res_rf=None) == []

    def test_tier_boundary(self):
        assert self._find(f01=5.0e9 + 1000, xy_rf=5.0e9, res_f01=7e9, res_rf=7e9)[0].severity == "warning"
        assert self._find(f01=5.0e9 + 999, xy_rf=5.0e9, res_f01=7e9, res_rf=7e9)[0].severity == "info"

    def test_wired_into_lint_state_and_values_domain(self):
        store = QuamStore.from_dicts(_freq_state(f01=5.01e9, xy_rf=5.0e9), {})
        cats = {f.category for f in diagnostics.lint_state(store)}
        assert "value_freq_consistency" in cats
        assert diagnostics.domain_of("value_freq_consistency") == "values"

    def test_healthy_fixture_unaffected(self):
        # The shared healthy chip has f_01 but no xy/resonator freq pair → silent.
        assert diagnostics.lint_state(_store()) == []
