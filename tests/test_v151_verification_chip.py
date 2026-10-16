"""v1.5.1 (D3 class C): one honest verification status per recipe tile.

Recipe tiles carry no reproduction status; the map tools showed "not verified on this run" AND
"verified on this run" for one click contract. Drives tests/verification_chip_selfcheck.cjs (the
real map-tools.js under jsdom) with recipe payloads gated by the real recipe gate.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from quam_state_manager.core.interactive_plots import learn_click as lc, recipe_gate
from quam_state_manager.core.interactive_plots.recipes.base import Bundle, FigureSpec

ROOT = Path(__file__).resolve().parents[1]


def _node_env():
    env = dict(os.environ)
    for cand in (ROOT / "node_modules", ROOT.parent / "statemanager" / "node_modules"):
        if (cand / "jsdom").is_dir():
            env["NODE_PATH"] = str(cand)
            return env
    return None


def _payload(tmp_path, *, applied=True, evidence=True):
    """A recipe payload as registry.build_interactive_figure ships it, after the real gate."""
    recipe_gate._VERIFIED.clear()
    entries = [{"op": "replace", "path": "/quam/qubits/q0/xy/amplitude", "old": 0.2, "value": 0.3}] if evidence else []
    node = {"patches": entries if applied else []}
    (tmp_path / "node.json").write_text(json.dumps(node))
    if not applied:
        (tmp_path / "state_updates.json").write_text(json.dumps({"operations": entries}))
    bundle = Bundle(run=SimpleNamespace(folder_path=str(tmp_path), fit_results={}, uid="run-1"), node_meta=node)
    spec = FigureSpec("amplitude::q0", "Amplitude", figure={"data": [], "layout": {"shapes": [
        {"type": "line", "x0": 300, "x1": 300, "y0": 0, "y1": 1, "xref": "x", "yref": "paper"}]}},
        clickable={"axis": "x", "qubit": "q0", "targets": [{"path": "qubits.q0.xy.amplitude", "scale": 0.001}]})
    spec = lc.gate(bundle, spec, "revision-1")
    recipe_gate._VERIFIED.clear()
    return {"clickable": spec.clickable,
            "display_only_reason": spec.reason if spec.clickable is None else "",
            "notices": [spec.reason] if spec.reason else []}


def test_one_verification_chip_per_recipe_tile(tmp_path):
    env = _node_env()
    if shutil.which("node") is None or env is None:
        pytest.skip("node + jsdom not installed")
    cases = {}
    for name, kwargs in (("no_evidence", {"evidence": False}), ("applied", {}), ("proposed", {"applied": False})):
        folder = tmp_path / name
        folder.mkdir()
        cases[name] = _payload(folder, **kwargs)
    assert cases["no_evidence"]["clickable"].get("verification_note")   # the case D3 found
    out = tmp_path / "cases.json"
    out.write_text(json.dumps(cases), encoding="utf-8")
    r = subprocess.run(["node", str(ROOT / "tests" / "verification_chip_selfcheck.cjs"), str(out)],
                       capture_output=True, text=True, env=env, encoding="utf-8", timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "verification_chip_selfcheck: 25 assertions passed" in r.stdout, r.stdout
