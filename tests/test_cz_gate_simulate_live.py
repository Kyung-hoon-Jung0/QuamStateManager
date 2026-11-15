"""Live gate simulation on the QM-SaaS cloud simulator.

Skips unless ``SM_GATE_SIM_PYTHON`` (a lab env python), ``SM_GATE_SIM_CHIP``
(a chip folder; the test runs on a temporary copy), ``SM_GATE_SIM_MACRO``
(e.g. ``qubit_pairs.<pair>.macros.<gate>``) and ``SAAS_EMAIL`` /
``SAAS_PASSWORD`` / ``SAAS_HOST`` are set. The password rides stdin only.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

NEED = ("SM_GATE_SIM_PYTHON", "SM_GATE_SIM_CHIP", "SM_GATE_SIM_MACRO",
        "SAAS_EMAIL", "SAAS_PASSWORD", "SAAS_HOST")
pytestmark = pytest.mark.skipif(not all(os.environ.get(k) for k in NEED),
                                reason="no live simulator configured")
RUNNER = Path(__file__).resolve().parent.parent / "quam_state_manager" / "generator" / "run_gate_sim.py"


def test_live_gate_simulation_returns_port_traces(tmp_path):
    chip = tmp_path / "chip"
    shutil.copytree(os.environ["SM_GATE_SIM_CHIP"], chip)
    work = tmp_path / "work"
    work.mkdir()
    job = {"folder": str(chip), "root_class": None, "macro": os.environ["SM_GATE_SIM_MACRO"],
           "max_ns": 20000, "pad_ns": 200, "start_ns": 300, "limit_s": 240}
    (work / "job.json").write_text(json.dumps(job), encoding="utf-8")
    req = {"target": {"local": {}, "saas": {"email": os.environ["SAAS_EMAIL"],
                                            "host": os.environ["SAAS_HOST"],
                                            "password": os.environ["SAAS_PASSWORD"]}}}
    env = dict(os.environ, QUAM_STATE_PATH=str(chip), PYTHONUTF8="1")
    p = subprocess.Popen([os.environ["SM_GATE_SIM_PYTHON"], str(RUNNER), str(work / "job.json"),
                          str(work)], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, text=True, env=env)
    p.stdin.write(json.dumps(req) + "\n")
    p.stdin.flush()
    try:
        p.wait(timeout=400)
    finally:
        if p.poll() is None:
            p.kill()
    text = (work / "_result.json").read_text(encoding="utf-8")
    assert os.environ["SAAS_PASSWORD"] not in text
    res = json.loads(text)
    assert res["ok"], res.get("error")
    assert res["ports"] and all(pt["volts"] for pt in res["ports"])
    assert not any(pt["cut_at_end"] for pt in res["ports"]), res.get("cut_note")
    assert max(abs(v) for pt in res["ports"] for v in pt["volts"]) > 0
