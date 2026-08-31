"""docs/141 §4s — the ⚯ Pairs picker beside ⚏ Qubits, and the popover fix.

The user's screenshot: the Properties / Qubits popovers looked cut off at
the chip bar. Cause: §4q gave every toolbar row `will-change: transform`,
which made each its own stacking context, so the chip bar (a later sibling)
painted OVER the menus. The rows are now ordered explicitly (toolbar rows
above the chip bar, both above the sticky table header) and carry no
will-change. And the ask: a Pairs button right of Qubits that picks the pair
grid's rows the way Qubits picks the qubit grid's.
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
CSS = (ROOT / "quam_state_manager/web/static/style.css").read_text(encoding="utf-8")
JS = (ROOT / "quam_state_manager/web/static/bulk-edit.js").read_text(encoding="utf-8")
TPL = (ROOT / "quam_state_manager/web/templates/_bulkedit.html").read_text(encoding="utf-8")


def _client(tmp_path: Path, with_pairs: bool):
    def _q(i):
        return {"id": f"q{i}", "f_01": 5e9 + i * 1e6,
                "xy": {"operations": {"x180": {"amplitude": 0.1}}},
                "resonator": {"operations": {"readout": {"amplitude": 0.04}}}}
    state = {"qubits": {f"q{i}": _q(i) for i in range(1, 4)}, "qubit_pairs": {}, "active_qubit_names": ["q1", "q2", "q3"]}
    if with_pairs:
        state["qubit_pairs"] = {"q1-2": {"id": "q1-2", "qubit_control": "#/qubits/q1", "qubit_target": "#/qubits/q2",
                                         "gates": {"CZ": {"amplitude": 0.12, "length": 60}}}}
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps({"wiring": {}, "network": {"host": "1.1.1.1"}}), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(tmp_path)}).status_code in (200, 302)
    return c


class TestPopoversAreNotCovered:
    def test_no_will_change_on_the_bars_and_an_explicit_order(self):
        i = CSS.index("/* NO will-change here:")
        block = CSS[i:i + 900]
        assert "will-change" not in block.split("*/", 1)[1]
        assert ".bulk-panel .bulk-toolbar { position: relative; z-index: 8; }" in block
        assert ".bulk-panel .bulk-chipbar, .bulk-panel .bulk-dyn-truncated, .bulk-panel .bulk-virt-note { position: relative; z-index: 6; }" in block
        # the popover itself sits above the sticky header (2 / 4) inside the toolbar's context
        assert "z-index: 30" in CSS[CSS.index(".bulk-colvis-menu {"):CSS.index(".bulk-colvis-menu {") + 200]


class TestPairsPicker:
    def test_the_button_sits_right_of_qubits_and_only_with_pair_rows(self, tmp_path):
        assert TPL.index('id="bulk-qubitvis-menu"') < TPL.index('id="bulk-pairvis-menu"') < TPL.index('id="bulk-search"')
        assert 'onclick="BulkEdit.showAllPairs()"' in TPL
        with_pairs = _client(tmp_path / "a", True).get("/bulk", headers={"HX-Request": "true"}).get_data(as_text=True)
        assert 'id="bulk-pairvis-menu"' in with_pairs and "&#9903; Pairs" in with_pairs and 'id="bulk-pair-pill"' in with_pairs
        no_pairs = _client(tmp_path / "b", False).get("/bulk", headers={"HX-Request": "true"}).get_data(as_text=True)
        assert 'id="bulk-pairvis-menu"' not in no_pairs and 'id="bulk-pair-pill"' not in no_pairs
        assert 'id="bulk-qubitvis-menu"' in no_pairs

    def test_the_client_owns_a_per_chip_pair_set(self):
        assert "function _pKey() { return QHIDE_PREFIX + 'pairs:' + (QMETA.chip || 'chip'); }" in JS
        assert "function _buildPairMenu() {" in JS and "showAllPairs: function () {" in JS
        # the follow rule reads the pair set on top of the qubit set, and a dirty row never hides
        i = JS.index("function _applyPairFollow(hid) {")
        body = JS[i:i + 900]
        assert "|| phid.has(pid)" in body and "if (off && _rowDirty(r)) off = false;" in body
        # built wherever the Qubits menu is built, and when the Qubits picker changes
        assert JS.count("_buildPairMenu();") >= 5


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_bulk_pairs_picker_selfcheck():
    node = shutil.which("node")
    try:
        subprocess.run([node, "-e", "require('jsdom')"], check=True, capture_output=True, timeout=30)
    except Exception:
        pytest.skip("jsdom not installed")
    r = subprocess.run([node, str(ROOT / "tests" / "bulk_pairs_picker_selfcheck.cjs")],
                       capture_output=True, text=True, encoding="utf-8", timeout=180, cwd=str(ROOT))
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.count("ok - ") >= 12, r.stdout
