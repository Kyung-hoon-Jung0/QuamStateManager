"""docs/242: the step-5 readout-feedline panel.

Drives tests/generate_readout_pool_selfcheck.cjs under node + jsdom (pool,
feedline cards, neighbor/crossing input rule, keyboard + click assignment,
undo, review text), and pins the server-side half of the pool contract: a
resonator line still in the wizard's pool has no feedline, so validate_spec
refuses it rather than letting run_build multiplex every pooled qubit onto one
unnamed group. Skips the node half without node + jsdom.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.core.config_generator import validate_spec

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "generate_readout_pool_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_generate_readout_pool_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT), timeout=120,
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)


def _spec(lines):
    return {
        "network": {"host": "10.0.0.1", "cluster_name": "C"},
        "instruments": {"controllers": [{"con": 1, "fems": [{"slot": 1, "fem": "mw"}]}],
                        "opx_plus": [], "octaves": []},
        "qubits": ["q1", "q2"], "qubit_pairs": [], "twpas": [], "lines": lines,
        "populate": {"qubits": {}, "pairs": {}},
    }


def test_a_pooled_readout_line_is_refused():
    errs = validate_spec(_spec([
        {"element": "q1", "line": "resonator", "group": "feedline1",
         "channel": {"kind": "mw_fem", "out_port": 8, "in_port": 2}},
        {"element": "q2", "line": "resonator", "pool": True, "channel": None},
    ]))
    pooled = [e for e in errs if "not on a feedline" in e]
    assert len(pooled) == 1 and "'q2'" in pooled[0], errs


def test_feedlined_readout_lines_pass():
    errs = validate_spec(_spec([
        {"element": "q1", "line": "resonator", "group": "feedline1",
         "channel": {"kind": "mw_fem", "out_port": 8, "in_port": 2}},
        {"element": "q2", "line": "resonator", "group": "feedline1",
         "channel": {"kind": "mw_fem", "out_port": 8, "in_port": 2}},
    ]))
    assert not [e for e in errs if "not on a feedline" in e], errs
