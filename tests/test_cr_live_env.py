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


def _shared_cr_spec_no_zz():
    """The shared-port chain WITHOUT the ZZ line: q2 is both a CR control
    (q2-q1, q2-q3) and a CR target (q1-q2) — the chained layout where a
    target's xy port also carries the dual-upconverter dict."""
    spec = _shared_cr_zz_spec()
    spec["lines"] = [ln for ln in spec["lines"] if ln["line"] != "zz_drive"]
    # resonators populated too, so the WHOLE config can be schema-checked
    spec["populate"]["resonator"] = {
        f"q{i}": {"RF_freq": 7.1e9 + 0.1e9 * i, "LO_frequency": 7.35e9}
        for i in (1, 2, 3)}
    return spec


_QM_SCHEMA_CHECK = r"""
import json, sys
from qm import QuantumMachinesManager
from qm.program._qua_config_schema import load_config
QuantumMachinesManager.set_capabilities_offline()
cfg = json.load(open(sys.argv[1], encoding="utf-8"))
try:
    load_config(cfg)
except Exception as exc:
    print("SCHEMA_FAIL", type(exc).__name__, exc)
    sys.exit(3)
print("SCHEMA_OK")
"""


def _xy_port(state: dict, wiring: dict, qubit: str) -> dict:
    """The port dict *qubit*'s xy drives, following ``#/...`` pointers through
    the merged state + wiring documents (QUAM splits one tree over both)."""
    doc = {**state, **wiring}
    node = doc["qubits"][qubit]["xy"]["opx_output"]
    for _ in range(8):
        if not (isinstance(node, str) and node.startswith("#/")):
            break
        cur = doc
        for part in node[2:].split("/"):
            cur = cur[part] if isinstance(cur, dict) else cur[int(part)]
        node = cur
    assert isinstance(node, dict), node
    return node


class TestSharedXyConfigValid:
    """generate-r2-01: a shared_xy CR build must produce a config the OPX
    accepts. quam_builder>=0.4 XYDriveMW.upconverter_frequency reads only
    the port's scalar LO, which the dual-upconverter surgery clears — so
    unless the build pins each control's xy LO to its upconverter, every
    CR-control xy IF ships as the literal '#./inferred_intermediate_frequency'
    and every chained CR target reads 'CR target frequency unknown'.
    Gated on the shared-port capabilities only (NOT cr.flavor_rf_pointer), so
    it runs in the released-quam_builder envs where the defect lives. It
    discriminates only in an env whose XYDriveMW reads the port SCALAR (the
    first capable env found; mutation-checked red there without the LO pin):
    one that still reads upconverters[n] passes with or without the fix."""

    def test_shared_xy_build_generates_a_valid_config(self, tmp_path):
        import subprocess

        from quam_state_manager.core.config_generator import run_config_preview

        env = _env_with(("pair.cr_channel", "wire.alloc_block_reuse"))
        if env is None:
            pytest.skip("no shared-port-CR-capable env available")
        out_dir = tmp_path / "chip"
        outcome = run_generator(env, "build", _shared_cr_spec_no_zz(), out_dir,
                                timeout=300)
        assert outcome.get("ok"), outcome.get("error")
        warns = (outcome.get("result") or {}).get("warnings") or []
        assert not [w for w in warns if "CR target frequency unknown" in w], warns

        # the PREMISE, asserted directly: q2 (a CR control) really got the
        # dual-upconverter port -- an `upconverters` dict and NO scalar
        # upconverter_frequency. Without it there is nothing for the LO pin to
        # rescue and every check below would pass with or without the fix.
        state = json.loads((out_dir / "state.json").read_text(encoding="utf-8"))
        wiring = json.loads((out_dir / "wiring.json").read_text(encoding="utf-8"))
        port = _xy_port(state, wiring, "q2")
        ups = port.get("upconverters")
        assert isinstance(ups, dict) and {"1", "2"} <= {str(k) for k in ups}, port
        assert port.get("upconverter_frequency") is None, port
        # the FIX: the control's own xy LO is pinned to its upconverter 1
        assert state["qubits"]["q2"]["xy"]["LO_frequency"].endswith(
            "/opx_output/upconverters/1/frequency")

        prev = run_config_preview(env, out_dir, timeout=300)
        assert prev.get("ok"), prev.get("error")
        cfg = (prev.get("result") or {}).get("config") or {}
        elements = cfg.get("elements") or {}
        checked = [n for n in elements if n.endswith(".xy") or n.startswith("cr_")]
        assert checked, sorted(elements)
        for name in checked:
            if_ = elements[name].get("intermediate_frequency")
            assert isinstance(if_, (int, float)) and not isinstance(if_, bool), (
                name, if_)

        # and the OPX's own config schema accepts it
        cfg_path = tmp_path / "cfg.json"
        cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
        chk = tmp_path / "chk.py"
        chk.write_text(_QM_SCHEMA_CHECK, encoding="utf-8")
        proc = subprocess.run([env, str(chk), str(cfg_path)],
                              capture_output=True, text=True, timeout=300)
        if "No module named 'qm" in (proc.stderr or ""):
            pytest.skip("env has no qm package to validate the config with")
        assert proc.returncode == 0 and "SCHEMA_OK" in proc.stdout, (
            proc.stdout[-2000:], proc.stderr[-2000:])
