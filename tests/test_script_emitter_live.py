"""End-to-end fidelity of the emitted Python bundle: running the exported
01 + 02 scripts in a real QM env must rebuild the SAME state.json +
wiring.json as the wizard's own subprocess build, and 03 must pass a
generate_config() sanity run.

Auto-skips when no QM-capable env is present (same gating as
test_pair_gates_seed.py). Slow — three subprocess runs of the QM stack.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.core import script_emitter
from quam_state_manager.core.config_generator import (
    discover_envs,
    probe_env,
    run_generator,
)


def _first_usable_qm_env():
    for env in discover_envs():
        if probe_env(env["python"])["usable"]:
            return env["python"]
    return None


def _spec():
    """CZ-tunable 2q chip with populate values — exercises wiring order,
    populate, gate seeding and the downconverter fix-up in one build."""
    return {
        "network": {"host": "10.1.1.1", "cluster_name": "CL", "port": None},
        "instruments": {"controllers": [{"con": 1, "fems": [
            {"slot": 1, "fem": "mw"}, {"slot": 2, "fem": "lf"}]}],
            "opx_plus": [], "octaves": []},
        "qubits": ["q1", "q2"],
        "qubit_pairs": [["q1", "q2"]],
        "twpas": [],
        "pair_gate": "cz_tunable",
        "lines": [
            {"element": "q1", "line": "resonator", "group": "feedline1",
             "channel": {"kind": "mw_fem", "out_port": 8, "in_port": 1}},
            {"element": "q2", "line": "resonator", "group": "feedline1",
             "channel": {"kind": "mw_fem", "out_port": 8, "in_port": 1}},
            {"element": "q1", "line": "drive", "channel": None},
            {"element": "q2", "line": "drive", "channel": None},
            {"element": "q1", "line": "flux", "channel": None},
            {"element": "q2", "line": "flux", "channel": None},
            {"element": "q1-q2", "line": "coupler", "channel": None},
        ],
        "populate": {
            "qubit": {"q1": {"RF_freq": 5.2e9, "LO_frequency": 5.0e9},
                      "q2": {"RF_freq": 4.9e9, "LO_frequency": 5.0e9}},
            "resonator": {"q1": {"RF_freq": 7.2e9, "LO_frequency": 7.35e9},
                          "q2": {"RF_freq": 7.3e9, "LO_frequency": 7.35e9}},
            "pulses": {"q1": {"x180_amplitude": 0.12, "x180_length": 48}},
            "pairs": {"q1-q2": {"cz_amplitude": 0.1, "cz_variant": "unipolar",
                                "cz_interaction_duration": 100}},
        },
    }


def test_emitted_bundle_rebuilds_identical_state(tmp_path):
    python = _first_usable_qm_env()
    if not python:
        pytest.skip("no usable QM env found")

    spec = _spec()

    # 1. Wizard-path build → A.
    out_a = tmp_path / "a"
    outcome = run_generator(python, "build", spec, out_a, timeout=600)
    assert outcome.get("ok"), outcome
    result = outcome.get("result") or {}

    # 2. Emit the bundle from the same spec + build allocation → run 01/02 → B.
    bundle = script_emitter.emit_bundle(
        spec, result.get("allocation") or {}, result.get("versions") or {},
        "fidelity", stamp="2026-01-01")
    sdir = tmp_path / "scripts"
    script_emitter.write_bundle(sdir, bundle)
    out_b = tmp_path / "b"
    out_b.mkdir()
    for script in ("01_make_wiring.py", "02_build_machine.py"):
        r = subprocess.run(
            [python, str(sdir / script), str(out_b)],
            capture_output=True, text=True, cwd=str(sdir), timeout=600,
        )
        assert r.returncode == 0, f"{script}: {r.stdout}\n{r.stderr}"

    # 3. Parsed-JSON equality of both artefacts (the fidelity contract).
    for name in ("state.json", "wiring.json"):
        a = json.loads((out_a / name).read_text(encoding="utf-8"))
        b = json.loads((out_b / name).read_text(encoding="utf-8"))
        assert a == b, f"{name} differs between wizard build and emitted scripts"

    # 4. 03 sanity-runs generate_config on the rebuilt chip.
    r = subprocess.run(
        [python, str(sdir / "03_generate_config.py"), str(out_b)],
        capture_output=True, text=True, cwd=str(sdir), timeout=600,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "generate_config() OK" in r.stdout
