"""v1.5.1 (r6 P2-2): the Datasets ID column fits its run ids and the
wrote-state mark.

The column was a fixed 58 px: at 1280 every 4-digit id read "#175..." and
the mark beside it (the run wrote values into the chip state) was clipped
away; at 1600 the cell was 66 px for 73 px of content. Now the column is
sized from the longest id the table holds and whether any run carries the
mark, refits when a poll brings a longer id, keeps a width the user
dragged, and every id cell names the run and its writes on hover.

Pinned on the REAL /datasets fragment and a REAL /datasets/changes-since
answer; the SHIPPED dataset-virtual.js builds the table
(tests/v151_r6_ds_id_column_selfcheck.cjs).
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
_PATCH = {"op": "replace", "path": "/quam/qubits/q1/f_01", "value": 5.1e9, "old": 5.0e9}


def _run(root: Path, rid: int, writes: int, day: str = "2026-10-16") -> None:
    run = root / day / f"#{rid}_12_ramsey_101500"
    (run / "quam_state").mkdir(parents=True)
    node = {"id": rid, "metadata": {"name": "12_ramsey", "status": "finished"},
            "data": {"parameters": {"model": {"qubits": ["q1"]}}}}
    if writes:
        node["patches"] = [dict(_PATCH) for _ in range(writes)]
    (run / "node.json").write_text(json.dumps(node), encoding="utf-8")
    (run / "quam_state" / "state.json").write_text('{"qubits": {"q1": {"id": "q1"}}}', encoding="utf-8")
    (run / "quam_state" / "wiring.json").write_text("{}", encoding="utf-8")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_the_id_column_fits_the_ids_and_the_wrote_mark(tmp_path):
    c = create_app(testing=True, instance_path=str(tmp_path / "_i")).test_client()
    _run(tmp_path / "data_a", 1757, 4)
    _run(tmp_path / "data_a", 1754, 0)
    _run(tmp_path / "data_b", 12, 0)
    for d in ("data_a", "data_b"):
        assert c.post("/workspace/add", data={"folder": str(tmp_path / d)}).status_code == 200
    page = c.get("/datasets", headers=HX).get_data(as_text=True)
    assert 'id="ds-rows-data"' in page and 'id="datasets-colgroup"' in page
    now = re.search(r'id="ds-rows-data"[^>]*data-now="([^"]+)"', page)
    assert now, "the rows carry the poll cursor"
    # a later run with a 5-digit id arrives through the delta poll
    _run(tmp_path / "data_a", 10001, 1, day="2026-10-17")
    delta = c.get(f"/datasets/changes-since?ts={now.group(1)}").get_json()
    assert any(r.get("id") == 10001 for r in delta.get("updated") or []), delta
    fx = tmp_path / "fx.json"
    fx.write_text(json.dumps({"page": page, "delta": delta}), encoding="utf-8")
    p = subprocess.run(["node", str(ROOT / "tests" / "v151_r6_ds_id_column_selfcheck.cjs"), str(fx)],
                       capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT), timeout=120)
    if p.returncode == 2 and "jsdom not installed" in (p.stderr or ""):
        pytest.skip("jsdom not installed")
    assert p.returncode == 0, f"stdout:\n{p.stdout}\nstderr:\n{p.stderr}"
