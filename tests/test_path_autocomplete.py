"""docs/301 F34: the sidebar's folder-path autocomplete.

Drives tests/path_autocomplete_selfcheck.cjs (the real app.js under jsdom):
a typed path that IS a chip folder offers itself first, labelled, and picking
it does not drill into it; the list never covers the form's own submit button
(it opens below it); submitting closes the list; Enter with nothing
highlighted still submits. Skips without node + jsdom.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _node_env():
    env = dict(os.environ)
    for cand in (ROOT / "node_modules", ROOT.parent / "statemanager" / "node_modules"):
        if (cand / "jsdom").is_dir():
            env["NODE_PATH"] = str(cand)
            return env
    return env


def test_path_autocomplete_selfcheck():
    if shutil.which("node") is None:
        pytest.skip("node not on PATH")
    r = subprocess.run(["node", str(ROOT / "tests" / "path_autocomplete_selfcheck.cjs")],
                       capture_output=True, text=True, env=_node_env(), encoding="utf-8",
                       timeout=180, cwd=str(ROOT))
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "all 20 checks passed" in r.stdout, r.stdout + r.stderr


def test_browse_reports_a_chip_folder_to_the_autocomplete(tmp_path):
    """The client's "chip folder" row rides on the route's own answer."""
    from quam_state_manager.web.app import create_app
    chip = tmp_path / "chipX" / "quam_state"
    (chip / "state_gen_scripts").mkdir(parents=True)
    (chip / "state.json").write_text("{}", encoding="utf-8")
    (chip / "wiring.json").write_text("{}", encoding="utf-8")
    c = create_app(testing=True, instance_path=str(tmp_path / "_i")).test_client()
    d = c.get("/browse", query_string={"path": str(chip), "complete": "1"}).get_json()
    assert d["has_quam_state"] is True and d["path"] == str(chip), d
    assert d["dirs"] == [str(chip / "state_gen_scripts")], d
