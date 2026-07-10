"""Pins web/static/topo-graph.js conventions: runs the Node selfcheck (normalizeGrid
matches BOTH chip-status paths; pairGridPositions) and cross-checks the JS quamPairId
against the REAL Python run_build._quam_pair_id (the spec "q1-q2" -> QUAM "q1-2"
transform a topology preview MUST apply to match get_topology). Skips without node.
"""
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "topo_graph_selfcheck.cjs"


def _python_quam_pair_id():
    """Import run_build._quam_pair_id (stdlib-only at module level — no QM stack)."""
    p = _ROOT / "quam_state_manager" / "generator" / "run_build.py"
    if str(p.parent) not in sys.path:
        sys.path.insert(0, str(p.parent))   # for the sibling _script_common import
    spec = importlib.util.spec_from_file_location("_run_build_for_topo_test", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod._quam_pair_id


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_topo_graph_selfcheck_and_pair_id_parity():
    r = subprocess.run(["node", str(_SELFCHECK)], capture_output=True, text=True, cwd=str(_ROOT))
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)

    # Cross-check the JS quamPairId map against the real Python _quam_pair_id.
    lines = [ln for ln in r.stdout.splitlines() if ln.startswith("__QUAMPAIRID__")]
    assert lines, "selfcheck did not emit the quamPairId map"
    js_map = json.loads(lines[0][len("__QUAMPAIRID__ "):])

    qpid = _python_quam_pair_id()
    for spec_pair, js_val in js_map.items():
        assert js_val == qpid(spec_pair), (
            f"pair_id parity broken for {spec_pair!r}: JS={js_val!r} Python={qpid(spec_pair)!r}")
