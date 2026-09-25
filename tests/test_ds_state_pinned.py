"""QA r2-08 -- Pin & Browse: each column's State tab acts on ITS OWN run.

The detail's inline script used to assign window-global
``switchDatasetStateTab`` / ``_dsStateActiveId`` over the fixed ids
``#ds-state-file-tabs`` / ``ds-state-tree-*`` / ``ds-state-search``. In the
split both columns' scripts run and the last one wins, and the pinned clone's
ids are "pinned-"-prefixed -- so the pinned column's file tabs and tree search
switched and filtered the OTHER column, the pinned run's other files could not
be opened at all, and the pinned script's first fetch rendered the pinned run's
state.json into the current column. Pinned against the REAL app.js (the real
togglePinDataset + pinned-swap interceptor) on the markup the REAL template
renders here, by ``tests/ds_state_pinned_fragcheck.cjs``.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

_ROOT = Path(__file__).resolve().parent.parent


def _seed(root: Path, run_id: int) -> None:
    d = root / "2026-09-15" / f"#{run_id}_03_resonator_spectroscopy_single_0{run_id % 10}0000"
    (d / "quam_state").mkdir(parents=True)
    (d / "node.json").write_text(json.dumps({
        "metadata": {"name": "03_resonator_spectroscopy_single", "status": "successful",
                     "run_start": "2026-09-15T01:00:00", "run_end": "2026-09-15T01:00:01"},
        "data": {"parameters": {"model": {"qubits": ["q3"]}}, "outcomes": {}},
        "id": run_id, "parents": [], "created_at": "2026-09-15T01:00:00",
    }), encoding="utf-8")
    (d / "data.json").write_text(json.dumps({"fit_results": {"q3": {"f": 1.0}}}),
                                 encoding="utf-8")
    (d / "quam_state" / "state.json").write_text(json.dumps({"qubits": {"q3": {"f_01": run_id}}}),
                                                 encoding="utf-8")
    (d / "quam_state" / "wiring.json").write_text(json.dumps({"wiring": {}}), encoding="utf-8")


@pytest.fixture
def rendered(tmp_path):
    f = tmp_path / "data"
    _seed(f, 4111)
    _seed(f, 4112)
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    c.post("/workspace/add", data={"folder": str(f)})
    with app.app_context():
        key = routes._folder_key(f)
    out = {}
    for rid in (4112, 4111):
        out[str(rid)] = c.get(f"/dataset/{key}:{rid}",
                              headers={"HX-Request": "true"}).get_data(as_text=True)
    out["uids"] = [f"{key}:4112", f"{key}:4111"]
    return out


def test_state_tab_markup_names_its_own_column(rendered):
    html = rendered["4112"]
    assert "switchDatasetStateTab('wiring', this)" in html
    assert "jsonTreeSearch(_dsStateActiveId(this), this.value)" in html
    assert 'id="ds-state-tree-wiring"' in html


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_state_tab_pinned_client_selfcheck(rendered, tmp_path):
    page = tmp_path / "details.json"
    page.write_text(json.dumps(rendered), encoding="utf-8")
    proc = subprocess.run(
        ["node", str(_ROOT / "tests" / "ds_state_pinned_fragcheck.cjs"), str(page)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT), timeout=120)
    if proc.returncode == 2 and "jsdom not installed" in (proc.stderr or ""):
        pytest.skip("jsdom not installed")
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert proc.stdout.count("ok - ") >= 12, proc.stdout
