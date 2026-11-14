"""Real builds: a coupler LF-FEM port gets its pair's moving-qubit z delay, after
the band fix-up has re-set the z delays, both in state.json and in the config
``machine.generate_config()`` produces from it. Skips without a usable QM env.
"""

import copy
import json

import pytest

from quam_state_manager.core.config_generator import run_config_preview, run_generator
from tests.test_script_emitter_live import _first_usable_qm_env, _spec


def _resolve(state, wiring, ptr):
    node = {"wiring": wiring.get("wiring", {}), **state}
    for part in ptr[2:].split("/"):
        node = node[part]
    return node


def _build(tmp_path, moving):
    python = _first_usable_qm_env()
    if not python:
        pytest.skip("no usable QM env found")
    spec = copy.deepcopy(_spec())
    spec["populate"]["pairs"]["q1-q2"]["moving_qubit"] = moving
    out = tmp_path / moving
    outcome = run_generator(python, "build", spec, out, timeout=600)
    assert outcome.get("ok"), outcome
    state = json.loads((out / "state.json").read_text(encoding="utf-8"))
    wiring = json.loads((out / "wiring.json").read_text(encoding="utf-8"))
    return python, out, state, wiring


def _ports(state, wiring):
    w = wiring["wiring"]
    z = {q: w["qubits"][q]["z"]["opx_output"] for q in ("q1", "q2")}
    pair = next(iter(w["qubit_pairs"].values()))
    return z, pair["c"]["opx_output"]


@pytest.mark.parametrize("moving,qid", [("control", "q1"), ("target", "q2")])
def test_coupler_delay_equals_the_moving_qubit_z_delay(tmp_path, moving, qid):
    python, out, state, wiring = _build(tmp_path, moving)
    z, c = _ports(state, wiring)
    zd = {q: _resolve(state, wiring, p)["delay"] for q, p in z.items()}
    # the spec's band fix-up gives the two qubits different z delays
    assert zd["q1"] != zd["q2"], zd
    assert _resolve(state, wiring, c)["delay"] == zd[qid], (zd, c)

    # the same in the config generated from the saved chip
    cfg = run_config_preview(python, out, timeout=300)
    assert cfg.get("ok"), cfg
    config = (cfg.get("result") or {}).get("config") or {}

    def cfg_delay(ptr):
        parts = ptr[2:].split("/")          # ports/analog_outputs/con1/<fem>/<port>
        con, fem, port = parts[2], parts[3], parts[4]
        fems = config["controllers"][con]["fems"]
        fem_cfg = fems.get(fem) or fems.get(int(fem))
        outs = fem_cfg["analog_outputs"]
        entry = outs.get(port) or outs.get(int(port))
        return entry.get("delay", 0)

    assert cfg_delay(c) == cfg_delay(z[qid]) == zd[qid]
