"""The /code-review sweep over docs/156–161 — the findings outside undo_live.

- docs/159: a list's `old_value_disp` is the grid's cut preview; the
  inspector/tree consumers and the Undo trail take `old_value_json`.
- docs/159: the pair grid's list cell ships the RESOLVED leaf as
  `data-resolved` (the undo repaint matches on it).
- docs/158: the Param History filter form carries `chip_key` on an archived
  chip's view, and the busy-index banner lives inside the results.
- docs/156: the calculator page asks a live window before opening a new one
  (a same-name window.open NAVIGATES, wiping typed values).
- app.js: a `level: "warning"` cellsReverted is not a green toast.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

_ROOT = Path(__file__).resolve().parent.parent
_STATIC = _ROOT / "quam_state_manager" / "web" / "static"
_TPL = _ROOT / "quam_state_manager" / "web" / "templates"
_APP_JS = (_STATIC / "app.js").read_text(encoding="utf-8")
_CALC_JS = (_STATIC / "calc.js").read_text(encoding="utf-8")
_TRAIL_JS = (_STATIC / "undo-trail.js").read_text(encoding="utf-8")


class TestListPayloadIsLosslessForConsumersThatNeedIt:
    def test_old_value_json_rides_beside_the_preview(self):
        long = [[0.25, 99.0], [0.1, 5.0], [0.02, 700.0]]
        p = routes._revert_entry_payload("x.y", long)
        assert p["old_value_disp"].endswith("…") and len(p["old_value_disp"]) == 25
        assert p["old_value_json"] == json.dumps(long, separators=(",", ":"))
        assert "old_value_json" not in routes._revert_entry_payload("x.y", 1.5)

    def test_the_inspector_revert_and_the_trail_prefer_it(self):
        i = _APP_JS.index('document.addEventListener("cellsReverted", function(evt) {')
        seg = _APP_JS[i:i + 1200]
        assert "e.old_kind === 'list' && e.old_value_json != null ? e.old_value_json" in seg
        assert "e.old_kind === 'list' && e.old_value_json != null" in _TRAIL_JS

    def test_a_warning_level_is_not_a_success_toast(self):
        i = _APP_JS.index('document.addEventListener("cellsReverted", function(evt) {')
        seg = _APP_JS[i:i + 5000]
        assert 'd.level === "warning" ? "warning" : "success"' in seg


class TestPairListCellResolved:
    def test_a_pointer_aliased_pair_list_cell_names_its_leaf(self):
        merged = {
            "qubit_pairs": {"p1": {"coupler": {"opx_output": "#/ports/analog_outputs/con1/4/1"}}},
            "ports": {"analog_outputs": {"con1": {"4": {"1": {"exponential_filter": [[0.5, 123.0]]}}}}},
        }
        cell = routes._list_pair_cell(merged, "p1", "qubit_pairs.p1.coupler.opx_output.exponential_filter")
        assert cell["display"] == "▦ 1×2"
        assert cell["dot_path"] == "qubit_pairs.p1.coupler.opx_output.exponential_filter"
        assert cell["resolved_path"] == "ports.analog_outputs.con1.4.1.exponential_filter"


class TestParamHistoryChipKey:
    def _client(self, tmp_path):
        folder = tmp_path / "quam_state"
        folder.mkdir()
        (folder / "state.json").write_text(json.dumps({
            "qubits": {"qA1": {"id": "qA1", "f_01": 5e9, "T1": 10.0}}, "active_qubit_names": ["qA1"]}), encoding="utf-8")
        (folder / "wiring.json").write_text(json.dumps({"network": {"host": "10.0.0.1"}}), encoding="utf-8")
        app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
        c = app.test_client()
        c.post("/load", data={"folder": str(folder)})
        return app, c

    def test_the_loaded_chip_carries_no_chip_key_and_an_archived_view_does(self, tmp_path):
        app, c = self._client(tmp_path)
        html = c.get("/param-history", headers={"HX-Request": "true"}).get_data(as_text=True)
        form = re.search(r'<form id="param-history-filters".*?</form>', html, re.S).group(0)
        assert 'name="chip_key"' not in form
        html = c.get("/param-history?chip_key=SomeOtherChip", headers={"HX-Request": "true"}).get_data(as_text=True)
        form = re.search(r'<form id="param-history-filters".*?</form>', html, re.S).group(0)
        assert '<input type="hidden" name="chip_key" value="SomeOtherChip">' in form

    def test_the_busy_index_banner_is_inside_the_results(self):
        tpl = (_TPL / "_param_history.html").read_text(encoding="utf-8")
        assert tpl.index('<div id="param-history-results">') < tpl.index("{% if index_error %}")


class TestPairColdSearchTextIsTheBadge:
    """Round 2, F10: the pair grid renders a list as `▦ N×M`, so the badge is
    that column's SEARCH TEXT. The cold-map patch wrote the qubit grid's
    24-char JSON preview instead — a search for the badge then missed the row,
    and a search for a number the cell never shows hit it."""

    def test_the_cold_map_patch_prefers_the_badge(self):
        js = (_STATIC / "pair-edit.js").read_text(encoding="utf-8")
        i = js.index("_pgv.patchColdValue(")
        seg = js[max(0, i - 700):i + 200]
        assert "old_value_badge" in seg and "old_kind === 'list'" in seg
        # the payload the server ships for a list carries both
        p = routes._revert_entry_payload("x.y", [[1, 2]])
        assert p["old_value_badge"] == "▦ 1×2" and p["old_value_disp"] == "[[1,2]]"


class TestCalcWindowAsksBeforeOpening:
    def test_the_page_pings_a_live_window_before_a_same_name_open(self):
        i = _CALC_JS.index("window.openCalcWindow = function (trigger) {")
        seg = _CALC_JS[i:i + 1500]
        assert "new BroadcastChannel(CH_NAME)" in _CALC_JS
        assert "ch.postMessage({ type: 'calc-ping' })" in seg
        assert "ev.data.type !== 'calc-here'" in seg
        # a silence opens; an answer never calls window.open
        assert "_openNew(trigger)" in seg
        j = _CALC_JS.index("function wireStandalone()")
        seg2 = _CALC_JS[j:j + 2000]
        assert "ch.postMessage({ type: 'calc-here' })" in seg2
        assert "ev.key !== 'quam_theme'" in seg2        # the theme follows the page
