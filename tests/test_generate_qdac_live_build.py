"""Real-env integration test: build a QDAC-biased chip through conda env
``CQT_20Q`` and verify every QDAC-related key lands in state.json/wiring.json
with the right shape.

CQT_20Q has the customer's own ``quam_config`` package (providing
``quam_config.qdac_components``) editable-installed alongside quam 0.6.0 /
quam_builder 0.4.0 / qualang_tools 0.23.0 — the one env on this machine that
can actually exercise the QDAC-II attach path end to end, rather than just
the DEGRADE fallback. Skips (not fails) when that env isn't present, so this
test stays green on any other machine.

Per the user's explicit scoping: exact calibration VALUES don't need to
match a real chip — only that every QDAC-related KEY generates with the
correct shape/type. Output goes to a pytest tmp_path, never into any real
project's quam_state folder (this repo's own README/CLAUDE.md doctrine: a
generator subprocess must never write over calibrated live data).
"""
import json

import pytest

from quam_state_manager.core import config_generator as cg

_ENV_NAME = "CQT_20Q"


def _find_env_python() -> str | None:
    for env in cg.discover_envs():
        if env.get("name") == _ENV_NAME:
            python = env.get("python")
            probe = cg.probe_env(python)
            if probe.get("usable"):
                return python
    return None


def _qdac_test_spec() -> dict:
    """Two qubits: q1 is QDAC-biased, q2 is a normal flux-tunable qubit —
    proves the mixed-architecture path (real chips mix both)."""
    return {
        "network": {"host": "1.2.3.4", "cluster_name": "TestCluster", "port": None},
        "instruments": {
            "controllers": [
                {"con": 1, "fems": [{"slot": 1, "fem": "mw"}, {"slot": 5, "fem": "lf"}]}
            ],
            "opx_plus": [],
            "octaves": [],
        },
        "qubits": ["q1", "q2"],
        "qubit_pairs": [],
        "twpas": [],
        "qdac": {
            "communication_type": "Ethernet",
            "ip_address": "192.168.88.244",
            "port": 5025,
            "qubits": {
                "q1": {
                    "channel": 13,
                    "trigger_port": "ext1",
                    "output_range": "high",
                    "output_filter": "med",
                    "settle_time": 20000,
                    "dwell": 2e-6,
                    "slew_rate": 2e7,
                    "dc_offset": -0.09,
                },
            },
        },
        "lines": [
            {"element": "q1", "line": "resonator", "group": "f1",
             "channel": {"kind": "mw_fem", "con": 1, "slot": 1}},
            {"element": "q1", "line": "drive", "channel": None},
            # q1 deliberately has NO flux line — it's QDAC-biased.
            {"element": "q2", "line": "resonator", "group": "f1",
             "channel": {"kind": "mw_fem", "con": 1, "slot": 1}},
            {"element": "q2", "line": "drive", "channel": None},
            {"element": "q2", "line": "flux", "channel": {"kind": "lf_fem", "con": 1}},
        ],
        "populate": {},
    }


@pytest.mark.skipif(_find_env_python() is None,
                     reason=f"conda env {_ENV_NAME!r} not found or not QM-usable on this machine")
def test_qdac_build_produces_every_qdac_key(tmp_path):
    python_path = _find_env_python()
    spec = _qdac_test_spec()
    assert cg.validate_spec(spec) == []

    outcome = cg.run_generator(python_path, mode="build", spec=spec, out_dir=tmp_path,
                               timeout=300)
    assert outcome.get("ok"), (outcome.get("error"), outcome.get("stderr"))
    result = outcome["result"]

    state_path = tmp_path / "state.json"
    wiring_path = tmp_path / "wiring.json"
    assert state_path.exists() and wiring_path.exists()

    state = json.loads(state_path.read_text(encoding="utf-8"))
    wiring = json.loads(wiring_path.read_text(encoding="utf-8"))

    # -- top-level QDAC instrument entry --
    qdac = state.get("qdac")
    assert isinstance(qdac, dict), f"no top-level 'qdac' entry in state.json: {result}"
    assert qdac.get("__class__") == "quam_config.qdac_components.QdacInstrument"
    assert qdac.get("communication_type") == "Ethernet"
    assert qdac.get("ip_address") == "192.168.88.244"
    assert qdac.get("port") == 5025

    # -- q1's z is a QdacBiasLine, not a normal flux line --
    q1_z = state["qubits"]["q1"]["z"]
    assert q1_z.get("__class__") == "quam_config.qdac_components.QdacBiasLine"
    assert q1_z.get("channel") == 13
    assert q1_z.get("trigger_port") == "ext1"
    assert q1_z.get("output_range") == "high"
    assert q1_z.get("output_filter") == "med"

    # -- q1's opx_trigger_out digital-marker channel --
    trig = q1_z.get("opx_trigger_out")
    assert isinstance(trig, dict), f"q1.z.opx_trigger_out missing: {q1_z}"
    trig_out = trig["digital_outputs"]["trigger"]["opx_output"]
    assert trig_out == "#/wiring/qubits/q1/qt/digital_output"

    # -- wiring.json: q1 has a qt.digital_output pointer, no z (flux) key --
    q1_wiring = wiring["wiring"]["qubits"]["q1"]
    assert "qt" in q1_wiring and "digital_output" in q1_wiring["qt"]
    import re
    assert re.match(r"^#/ports/digital_outputs/con\d+/\d+/\d+$",
                    q1_wiring["qt"]["digital_output"])
    assert "z" not in q1_wiring

    # -- the pointed-to digital-output port actually exists --
    ref = q1_wiring["qt"]["digital_output"]
    segs = ref.lstrip("#/").split("/")  # ports/digital_outputs/con1/1/1
    node = state
    for seg in segs:
        node = node[seg]
    assert isinstance(node, dict)

    # -- mixed-architecture proof: q2 (not QDAC-biased) keeps a normal flux
    #    line (wiring key "z", matching the qubit's z attribute) --
    q2_wiring = wiring["wiring"]["qubits"]["q2"]
    assert "z" in q2_wiring
    q2_z = state["qubits"]["q2"]["z"]
    assert q2_z.get("__class__") != "quam_config.qdac_components.QdacBiasLine"

    # No degrade warnings about QDAC (a real attach should need none).
    qdac_warnings = [w for w in result.get("warnings", []) if "QDAC" in w or "qdac" in w]
    assert qdac_warnings == [], qdac_warnings
