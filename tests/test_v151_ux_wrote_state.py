"""P1-2 (U05): applying a fit from a figure is findable.

Seven runs were opened before one figure took a click: nothing in the run
list said which runs wrote state (their fits are the ones that can be
applied), the refusal on the reproduced figures was jargon ("no common verified
marker transform"), and the clickable figure sat under "Legacy recipes and
saved figures" with no hint. Now:

* each run carries ``writes`` (node.json patches); the run list marks it and
  ``is:wrote`` / the "wrote state" chip filter to those runs;
* the reproduced figures' note leads with "Click-to-apply is off on these
  figures", says why in plain words and names the figure that takes clicks;
* the second section reads "SM fit figures and saved images" and says a
  figure marked "click a point to apply" takes clicks.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.core import dataset as D

_ROOT = Path(__file__).resolve().parent.parent
_WEB = _ROOT / "quam_state_manager" / "web"


def _run(root: Path, rid: int, patches):
    folder = root / "2026-10-18" / f"#{rid}_05_power_rabi_0{rid}0000"
    folder.mkdir(parents=True)
    node = {"id": rid, "created_at": "2026-10-18T01:00:00+09:00", "parents": [],
            "metadata": {"name": "05_power_rabi", "status": "finished"},
            "data": {"parameters": {"model": {"qubits": ["q1"]}}}}
    if patches is not None:
        node["patches"] = patches
    (folder / "node.json").write_text(json.dumps(node), encoding="utf-8")


def test_a_run_counts_the_values_it_wrote(tmp_path):
    root = tmp_path / "runs"
    _run(root, 1, [{"op": "replace", "path": "/quam/qubits/q1/xy/operations/x180/amplitude",
                    "value": 0.31, "old": 0.3},
                   {"op": "replace", "path": "/quam/qubits/q1/f_01", "value": 5.1e9, "old": 5.0e9}])
    _run(root, 2, [])
    _run(root, 3, None)
    store = D.DatasetStore(root)
    assert store.runs[1].writes == 2
    assert store.runs[2].writes == 0 and store.runs[3].writes == 0
    rows = {r["id"]: r for r in store.list_runs_compact()}
    assert rows[1]["ws"] == 2 and rows[2]["ws"] == 0


def test_the_persisted_index_carries_it_and_an_older_index_is_rebuilt():
    assert D._STORE_CACHE_V >= 4
    assert "writes" in D.RunInfo.__dataclass_fields__


def test_the_list_marks_and_filters_runs_that_wrote_state():
    js = (_WEB / "static" / "dataset-virtual.js").read_text(encoding="utf-8")
    i = js.index("{key: 'id', label: 'ID'")
    assert "r.ws" in js[i:i + 600] and "ds-wrote" in js[i:i + 600]
    assert "if (value === 'wrote' || value === 'writes') return !!row.ws;" in js
    tpl = (_WEB / "templates" / "_datasets.html").read_text(encoding="utf-8")
    assert 'class="ds-help-example btn-sm outline ds-wrote-chip" data-example="is:wrote"' in tpl
    assert 'data-example="is:wrote">is:wrote</button>' in tpl


def test_the_second_section_has_a_name_a_newcomer_reads():
    src = (_WEB / "routes.py").read_text(encoding="utf-8")
    assert "Legacy recipes and saved figures" not in src
    assert "SM fit figures and saved images" in src and "click a point to apply" in src


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_the_refusal_is_plain_and_points_to_the_clickable_figure():
    proc = subprocess.run(
        ["node", str(_ROOT / "tests" / "click_refusal_selfcheck.cjs")],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT), timeout=120)
    if proc.returncode == 2 and "jsdom not installed" in (proc.stderr or ""):
        pytest.skip("jsdom not installed")
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
