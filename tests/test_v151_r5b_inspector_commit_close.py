"""R3B M6: an inspector closed by a navigation does not come back when the
value typed into it commits on the way out -- and the edit still lands.

Type a value into the q2 inspector and click a sidebar page without pressing
Enter: the blur commits (a POST that answers into ``#inspector-pane``), the
click closes the inspector for a page that cannot show a qubit, and the
commit's answer used to swap the q2 inspector back over the new page. The
browser side runs in ``tests/v151_inspector_commit_close_selfcheck.cjs`` on
the REAL markup and the REAL edit answer captured here; this driver also
checks that the server applied the edit.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

ROOT = Path(__file__).resolve().parent.parent
HX = {"HX-Request": "true"}


def _client(tmp_path):
    chip = tmp_path / "chip"
    chip.mkdir()
    q = lambda n, f: {"id": n, "f_01": f, "T1": 2e-5}  # noqa: E731
    (chip / "state.json").write_text(json.dumps({
        "qubits": {"q1": q("q1", 5.1e9), "q2": q("q2", 5.2e9)}, "qubit_pairs": {},
        "active_qubit_names": ["q1", "q2"]}), encoding="utf-8")
    (chip / "wiring.json").write_text("{}", encoding="utf-8")
    c = create_app(testing=True, instance_path=str(tmp_path / "_i")).test_client()
    assert c.post("/load", data={"folder": str(chip)}).status_code < 400
    return c


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_a_commit_answering_after_a_close_by_navigation_stays_closed(tmp_path):
    c = _client(tmp_path)
    full = c.get("/qubits").get_data(as_text=True)
    link = re.search(r'<a [^>]*href="/datasets"[^>]*>.*?</a>', full, re.S).group(0)
    pane_open = re.search(r'<div id="inspector-pane"[^>]*>', full).group(0)
    tray = c.get("/state/tray", headers=HX).get_data(as_text=True)
    assert 'id="pending-tray"' in tray and 'data-change-count="0"' in tray
    table = c.get("/qubits", headers=HX).get_data(as_text=True)
    inspector = c.get("/qubit/q2", headers=HX).get_data(as_text=True)
    assert 'name="dot_path" value="qubits.q2.f_01"' in inspector
    datasets = c.get("/datasets", headers=HX).get_data(as_text=True)
    # the edit, as the commit sends it: the server applies it
    r = c.post("/qubit/q2/edit", data={"dot_path": "qubits.q2.f_01", "value": "5.3e9"}, headers=HX)
    assert r.status_code == 200
    edit = r.get_data(as_text=True)
    assert 'hx-swap-oob="outerHTML:#pending-tray"' in edit and 'data-change-count="1"' in edit
    after = c.get("/qubit/q2", headers=HX).get_data(as_text=True)
    assert re.search(r'name="dot_path" value="qubits\.q2\.f_01">\s*<input[^>]*value="5,?300,?000,?000', after), \
        "the edit landed"
    tray_after = c.get("/state/tray", headers=HX).get_data(as_text=True)
    assert 'data-change-count="1"' in tray_after
    headers = {k: v for k, v in r.headers.items() if k.lower().startswith("hx-")}
    fx = tmp_path / "fx.json"
    fx.write_text(json.dumps({
        "link": link, "pane_open": pane_open, "tray": tray, "table": table, "inspector": inspector,
        "datasets": datasets, "edit_response": edit, "edit_headers": headers, "tray_after": tray_after,
        "edit_url": "/qubit/q2/edit", "dot_path": "qubits.q2.f_01", "value": "5.3e9"}), encoding="utf-8")
    p = subprocess.run(["node", str(ROOT / "tests" / "v151_inspector_commit_close_selfcheck.cjs"), str(fx)],
                       capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT), timeout=180)
    if p.returncode == 2 and "jsdom not installed" in (p.stderr or ""):
        pytest.skip("jsdom not installed")
    assert p.returncode == 0, f"stdout:\n{p.stdout}\nstderr:\n{p.stderr}"
