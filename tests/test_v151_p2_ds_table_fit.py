"""v1.5.1 U P2-2: the Datasets run table fits its pane at 1280.

The default columns summed to 1088 px in a 959 px pane, so TAGS sat
off-screen behind a pane scrollbar. Until the user sizes them, Experiment,
Tags and Folder give up width in proportion to what each has above its floor;
a dragged width is kept. Pinned on the REAL /datasets fragment in
tests/v151_ds_table_fit_selfcheck.cjs.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

ROOT = Path(__file__).resolve().parent.parent


def _run(root: Path, rid: int) -> None:
    run = root / "2026-10-16" / f"#{rid}_12_ramsey_10150{rid}"
    (run / "quam_state").mkdir(parents=True)
    (run / "node.json").write_text(json.dumps({"id": rid, "metadata": {"name": "12_ramsey", "status": "finished"},
                                               "data": {"parameters": {"model": {"qubits": ["q1"]}}}}),
                                   encoding="utf-8")
    (run / "quam_state" / "state.json").write_text('{"qubits": {"q1": {"id": "q1"}}}', encoding="utf-8")
    (run / "quam_state" / "wiring.json").write_text("{}", encoding="utf-8")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_the_run_table_fits_a_1280_pane(tmp_path):
    c = create_app(testing=True, instance_path=str(tmp_path / "_i")).test_client()
    for i, d in enumerate(("data_a", "data_b"), start=1):
        _run(tmp_path / d, i)
        assert c.post("/workspace/add", data={"folder": str(tmp_path / d)}).status_code == 200
    page = c.get("/datasets", headers={"HX-Request": "true"}).get_data(as_text=True)
    assert 'id="ds-rows-data"' in page and 'id="datasets-colgroup"' in page
    fx = tmp_path / "fx.json"
    fx.write_text(json.dumps({"page": page}), encoding="utf-8")
    p = subprocess.run(["node", str(ROOT / "tests" / "v151_ds_table_fit_selfcheck.cjs"), str(fx)],
                       capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT), timeout=120)
    if p.returncode == 2 and "jsdom not installed" in (p.stderr or ""):
        pytest.skip("jsdom not installed")
    assert p.returncode == 0, f"stdout:\n{p.stdout}\nstderr:\n{p.stderr}"
