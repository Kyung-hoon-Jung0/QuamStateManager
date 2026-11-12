"""Drive the port play rack selfcheck (D-23..D-26) under jsdom."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest


def test_rack_selfcheck_jsdom():
    if not shutil.which("node"):
        pytest.skip("node not installed")
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, NODE_PATH=os.environ.get("NODE_PATH", "D:/work_laptop/statemanager-cfb/node_modules"))
    r = subprocess.run(["node", str(root / "tests" / "port_play_rack_selfcheck.cjs")],
                       capture_output=True, text=True, encoding="utf-8", env=env, timeout=300)
    if "Cannot find module 'jsdom'" in (r.stderr or ""):
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "selfcheck ok" in r.stdout


def test_the_chip_payload_carries_an_lf_port_offset(tmp_path):
    """D-25: /api/instrument/data must carry an LF port's offset, or the popup's
    LF offset prefill (source.chipPort) is always null."""
    import json as _json
    from quam_state_manager.web.app import create_app
    state = {"qubits": {"q1": {"z": {"opx_output": "#/wiring/qubits/q1/z/opx_output"}}},
             "active_qubit_names": ["q1"]}
    wiring = {"wiring": {"qubits": {"q1": {"z": {"opx_output": "#/ports/analog_outputs/con1/4/2"}}}},
              "ports": {"analog_outputs": {"con1": {"4": {"2": {"offset": 0.125, "output_mode": "direct"}}}}},
              "network": {"host": "10.0.0.1"}}
    (tmp_path / "state.json").write_text(_json.dumps(state), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(_json.dumps(wiring), encoding="utf-8")
    c = create_app(testing=True, instance_path=str(tmp_path / "_inst")).test_client()
    c.post("/load", data={"folder": str(tmp_path)})
    fems = c.get("/api/instrument/data").get_json()["instrument"]["controllers"]["con1"]["fems"]
    a = fems["4"]["output_ports"]["2"][0]
    assert a["offset"] == 0.125 and a["output_mode"] == "direct"
