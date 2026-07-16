"""Live-env gates for the shared-port CR build (docs/54).

Builds REAL chips through the generator subprocess in a discovered env whose
quam-builder carries the CR-branch capabilities (selected BY CAPABILITY, never
by env name — versions lie). Auto-skips when no such env exists. Asserts the
customer's dual-upconverter layout end-to-end: wiring ports, upconverters
dict, the CR pointer web, the 4-shape library + cancel twins, ZZ + xy_detuned,
and flavor detection on the built output.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from quam_state_manager.core import cr_semantics
from quam_state_manager.core.config_generator import (
    discover_envs,
    probe_capabilities,
    probe_env,
    run_generator,
)

sys.path.insert(0, str(Path(__file__).parent))


def _env_with(cap_ids: tuple[str, ...]) -> str | None:
    """First usable env whose deep probe has every *cap_ids* available."""
    for env in discover_envs():
        if not probe_env(env["python"])["usable"]:
            continue
        probe = probe_capabilities(env["python"])
        caps = probe.get("capabilities") or {}
        if probe.get("ok") and all(
                (caps.get(c) or {}).get("available") for c in cap_ids):
            return env["python"]
    return None


def _shared_cr_zz_spec():
    """3-qubit chain, shared-port CR both ways on the first edge + ZZ."""
    return {
        "network": {"host": "1.2.3.4", "cluster_name": "C", "port": None},
        "instruments": {"controllers": [{"con": 1, "fems": [
            {"slot": 1, "fem": "mw"}]}], "opx_plus": [], "octaves": []},
        "qubits": ["q1", "q2", "q3"],
        "qubit_pairs": [["q1", "q2"], ["q2", "q1"], ["q2", "q3"]],
        "twpas": [],
        "lines": [
            {"element": "q1", "line": "resonator", "group": "f", "channel": {"kind": "mw_fem"}},
            {"element": "q1", "line": "drive", "channel": {"kind": "mw_fem"}},
            {"element": "q2", "line": "resonator", "group": "f", "channel": {"kind": "mw_fem"}},
            {"element": "q2", "line": "drive", "channel": {"kind": "mw_fem"}},
            {"element": "q3", "line": "resonator", "group": "f", "channel": {"kind": "mw_fem"}},
            {"element": "q3", "line": "drive", "channel": {"kind": "mw_fem"}},
            {"element": "q1-q2", "line": "cross_resonance", "channel": None},
            {"element": "q2-q1", "line": "cross_resonance", "channel": None},
            {"element": "q2-q3", "line": "cross_resonance", "channel": None},
            {"element": "q1-q2", "line": "zz_drive", "channel": None},
        ],
        "populate": {
            "qubit": {
                "q1": {"RF_freq": 4.9e9, "LO_frequency": 5.0e9},
                "q2": {"RF_freq": 5.2e9, "LO_frequency": 5.3e9},
                "q3": {"RF_freq": 5.0e9, "LO_frequency": 5.1e9},
            },
            "pairs": {"q1-q2": {"cr_shapes": "full"}},
        },
        "pair_gate": "cr",
        "cr_port_mode": "shared_xy",
    }


_CR_CAPS = ("pair.cr_channel", "wire.alloc_block_reuse",
            "cr.flavor_rf_pointer", "qpu.fixed_frequency_zz")


class TestSharedPortBuild:
    def test_shared_port_dual_upconverter_build(self, tmp_path):
        env = _env_with(_CR_CAPS)
        if env is None:
            pytest.skip("no CR-branch (fa540b6-flavor) env available")
        out_dir = tmp_path / "chip"
        outcome = run_generator(env, "build", _shared_cr_zz_spec(), out_dir,
                                timeout=300)
        assert outcome.get("ok"), outcome.get("error")
        state = json.loads((out_dir / "state.json").read_text())
        wiring = json.loads((out_dir / "wiring.json").read_text())

        # wiring: every CR line rides its CONTROL's xy port
        wq = wiring["wiring"]["qubits"]
        wp = wiring["wiring"]["qubit_pairs"]
        assert wp["q1-2"]["cr"]["opx_output"] == wq["q1"]["xy"]["opx_output"]
        assert wp["q2-1"]["cr"]["opx_output"] == wq["q2"]["xy"]["opx_output"]
        assert wp["q1-2"]["zz"]["opx_output"] == wq["q1"]["xy"]["opx_output"]

        # ports: dual upconverters — LO1 = own drive, LO2 = neighbor mean
        port_ref = wq["q1"]["xy"]["opx_output"]          # "#/ports/mw_outputs/..."
        segs = port_ref[2:].split("/")
        node = state
        for s in segs:
            node = node[s]
        assert "upconverters" in node
        ucs = {str(k): v for k, v in node["upconverters"].items()}
        assert ucs["1"]["frequency"] == pytest.approx(5.0e9)
        assert ucs["2"]["frequency"] == pytest.approx(5.2e9)   # q1's partner: q2

        # channel: pointer web + upconverter 2 + rf flavor
        pair = state["qubit_pairs"]["q1-2"]
        cr = pair["cross_resonance"]
        assert cr["upconverter"] == 2
        assert cr["LO_frequency"].endswith("/xy/opx_output/upconverters/2/frequency")
        assert cr["target_qubit_RF_frequency"].endswith("q2/xy/RF_frequency")
        assert cr["intermediate_frequency"] == "#./inferred_intermediate_frequency"
        # full shape library + cancel twins on the target's xy
        assert {"square", "flattop", "cosine", "gauss"} <= set(cr["operations"])
        t_ops = state["qubits"]["q2"]["xy"]["operations"]
        for shape in ("square", "flattop", "cosine", "gauss"):
            assert f"cr_{shape}_q1-2" in t_ops

        # ZZ family: channel + stark_cz macro + xy_detuned twins on the target
        zz = pair.get("zz_drive") or pair.get("zz")
        assert isinstance(zz, dict)
        assert zz["detuning"] == pytest.approx(-30e6)
        assert "stark_cz" in pair["macros"]
        xy_det = state["qubits"]["q2"].get("xy_detuned")
        assert isinstance(xy_det, dict)
        assert "zz_square_q1-2" in xy_det["operations"]

        # the built output is the rf flavor and SM reads it as such
        report = cr_semantics.detect_flavor(state)
        assert report.flavor == cr_semantics.FLAVOR_RF

        # effective-IF emulation matches quam's own inferred property intent:
        # target RF 5.2 GHz − LO2 5.2 GHz = 0 for q1-2
        from quam_state_manager.core.loader import QuamStore
        store = QuamStore.from_dicts(state, wiring)
        eff = cr_semantics.effective_frequencies(store, "q1-2")
        assert eff is not None and eff.if_hz == pytest.approx(0.0)
        assert eff.valid

    def test_dedicated_mode_unchanged(self, tmp_path):
        """Regression: the same spec in dedicated mode allocates SEPARATE CR
        ports (the legacy layout) — the two-phase refactor must not leak."""
        env = _env_with(("pair.cr_channel",))
        if env is None:
            pytest.skip("no CR-capable env available")
        spec = _shared_cr_zz_spec()
        spec["cr_port_mode"] = "dedicated"
        # dedicated CR ports need more MW ports than one FEM row offers
        spec["instruments"]["controllers"][0]["fems"].append(
            {"slot": 2, "fem": "mw"})
        out_dir = tmp_path / "chip"
        outcome = run_generator(env, "build", spec, out_dir, timeout=300)
        assert outcome.get("ok"), outcome.get("error")
        wiring = json.loads((out_dir / "wiring.json").read_text())
        wq = wiring["wiring"]["qubits"]
        wp = wiring["wiring"]["qubit_pairs"]
        assert wp["q1-2"]["cr"]["opx_output"] != wq["q1"]["xy"]["opx_output"]


class TestCustomerStateLoads:
    def test_fa540b6_env_loads_rf_flavor_state(self, tmp_path):
        """The rf-flavor fixture (the customer schema) must Quam.load in the
        CR-branch env — proven via the config-preview subprocess."""
        env = _env_with(("cr.flavor_rf_pointer",))
        if env is None:
            pytest.skip("no fa540b6-flavor env available")
        from cr_fixtures import make_flavor_b, write_folder
        from quam_state_manager.core.config_generator import run_config_preview

        folder = write_folder(tmp_path / "chip", *make_flavor_b())
        outcome = run_config_preview(env, folder, timeout=300)
        # generate_config on the synthetic fixture may fail on physics, but
        # Quam.load must NOT fail on schema (unknown field / missing class)
        err = (outcome.get("error") or "") + (outcome.get("traceback") or "")
        assert "target_qubit_RF_frequency" not in err
        assert "FixedFrequencyZZDriveTransmon" not in err
