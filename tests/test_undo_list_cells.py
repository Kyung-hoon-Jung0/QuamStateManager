"""docs/159 — Ctrl+Z on a LIST cell (exponential_filter) shows on screen.

Customer: Ctrl+Z / Ctrl+Shift+Z on `exponential_filter` reverted the value
in the working copy while the Live State Edit cell kept the old preview, so
the user read it as "undo does not work". Two causes, pinned here:

- /undo names the RESOLVED path (`ports.analog_outputs.con1.4.1.
  exponential_filter` for `qubits.q2.z.opx_output.exponential_filter`, a
  port field behind the io pointer) while the list-preview span carried only
  its alias in `data-path` — so the repaint found no cell (`missing`) and,
  per the docs/141 §4e contract, scheduled no rebuild either;
- even when found, a list cell was deliberately left uncovered for a rebuild
  (no honest string to write), and the payload's `old_value_disp` for a list
  was `str(list)` — Python's repr, which no cell renders.

Now the span carries `data-resolved`, the payload ships the very strings the
two grids render (`_list_preview` / `_list_badge`, ONE function each for the
page and the payload), and the 🕘 docks on the span too.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

_ROOT = Path(__file__).resolve().parent.parent
_MACROS = (_ROOT / "quam_state_manager" / "web" / "templates" / "_bulk_cell_macros.html").read_text(encoding="utf-8")
_BULK_JS = (_ROOT / "quam_state_manager" / "web" / "static" / "bulk-edit.js").read_text(encoding="utf-8")
_PAIR_JS = (_ROOT / "quam_state_manager" / "web" / "static" / "pair-edit.js").read_text(encoding="utf-8")
_APP_JS = (_ROOT / "quam_state_manager" / "web" / "static" / "app.js").read_text(encoding="utf-8")


class TestPayload:
    def test_a_list_ships_the_grid_strings(self):
        p = routes._revert_entry_payload("x.y", [[0.5, 123.0], [0.1, 5.0]])
        assert p["old_kind"] == "list"
        assert p["old_value_disp"] == "[[0.5,123.0],[0.1,5.0]]"
        assert p["old_value_badge"] == "▦ 2×2"
        v = routes._revert_entry_payload("x.y", [1, 2, 3])
        assert v["old_value_disp"] == "[1,2,3]" and v["old_value_badge"] == "[ 3 ]"

    def test_the_preview_is_the_page_render(self):
        """ONE function for the page cell and the payload: the truncation and
        separators must agree or a repainted cell differs from a fresh one."""
        long = [[0.123456, 1234.5678], [0.2, 3.0], [0.3, 4.0]]
        assert routes._list_preview(long) == json.dumps(long, separators=(",", ":"))[:24] + "…"
        assert routes._list_preview(None) == ""
        assert routes._list_badge([]) == "[ 0 ]" and routes._list_badge(None) == ""
        src = routes._list_json_cell.__code__.co_names
        assert "_list_preview" in src
        assert "_list_badge" in routes._list_pair_cell.__code__.co_names

    def test_scalars_are_untouched(self):
        p = routes._revert_entry_payload("x.y", 5075187484.52453)
        assert p["old_kind"] == "num" and p["old_value_disp"] == "5,075,187,484.52453"
        assert "old_value_badge" not in p
        assert routes._revert_entry_payload("x.y", None)["old_kind"] == "null"
        assert routes._revert_entry_payload("x.y", {"a": 1})["old_kind"] == "other"


class TestMarkup:
    def test_the_list_span_carries_the_resolved_leaf_and_is_focusable(self):
        span = re.search(r'<span class="bulk-cell-list[^>]*>', _MACROS).group(0)
        assert 'data-path="{{ cell.dot_path }}"' in span
        assert 'data-resolved="{{ cell.resolved_path }}"' in span
        assert 'tabindex="0"' in span

    def test_the_pair_list_input_is_marked(self):
        inp = re.search(r'\{%- if cell.kind == \'list\' -%\}\s*<input[^>]*>', _MACROS).group(0)
        assert 'data-list="1"' in inp and "readonly" in inp


class TestClient:
    def test_the_qubit_grid_matches_the_span_on_both_axes_and_repaints_a_list(self):
        i = _BULK_JS.index("function _revertPaths(entries)")
        seg = _BULK_JS[i:i + 6000]
        assert ".bulk-cell-list[data-path=" in seg and ".bulk-cell-list[data-resolved=" in seg
        assert "if (c.classList.contains('bulk-cell-list')) {" in seg
        assert "if (e.old_kind !== 'list') return;" in seg
        assert "c.textContent = v;" in seg

    def test_the_pair_grid_repaints_the_badge(self):
        i = _PAIR_JS.index("function _revertPaths(entries)")
        seg = _PAIR_JS[i:i + 6000]
        assert "if (c.hasAttribute('data-list')) {" in seg
        assert "c.value = String(e.old_value_badge);" in seg

    def test_the_clock_docks_on_the_span(self):
        i = _APP_JS.index('document.addEventListener("focusin", function (e) {', _APP_JS.index("function showCellBtn("))
        seg = _APP_JS[i:i + 600]
        assert 't.classList.contains("bulk-cell-list")' in seg
        assert "input.value !== undefined ? input.value : input.textContent" in _APP_JS


def _state():
    # a flux qubit whose z port is reached through the io pointer -- the
    # customer's exponential_filter shape (a port field behind an alias)
    return {
        "qubits": {"q1": {"id": "q1", "f_01": 5e9,
                          "z": {"opx_output": "#/ports/analog_outputs/con1/4/1"}}},
        "ports": {"analog_outputs": {"con1": {"4": {"1": {
            "exponential_filter": [[0.5, 123.0]], "offset": 0.0}}}}},
        "active_qubit_names": ["q1"],
    }


@pytest.fixture
def client(tmp_path):
    folder = tmp_path / "quam_state"
    folder.mkdir()
    (folder / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(
        {"wiring": {"qubits": {"q1": {}}}, "network": {"host": "10.0.0.1"}}), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    c = app.test_client()
    c.post("/load", data={"folder": str(folder)})
    return c


class TestEndToEnd:
    def test_undo_names_the_resolved_leaf_and_the_span_carries_it(self, client):
        alias = "qubits.q1.z.opx_output.exponential_filter"
        resolved = "ports.analog_outputs.con1.4.1.exponential_filter"
        r = client.post("/field/edit-batch", json={"updates": [{"dot_path": alias, "value": [[0.25, 99.0], [0.1, 5.0]]}],
                                                  "expect_chip": ""})
        assert r.status_code == 200 and r.get_json()["ok"], r.get_data(as_text=True)
        u = client.post("/undo", headers={"HX-Request": "true"})
        assert u.status_code == 200
        trig = json.loads(u.headers["HX-Trigger"])["cellsReverted"]
        (e,) = trig["entries"]
        assert e["dot_path"] == resolved
        assert e["old_kind"] == "list"
        assert e["old_value_disp"] == "[[0.5,123.0]]"
        assert e["old_value_badge"] == "▦ 1×2"
        # the grid's span names the same leaf, so the repaint can find it
        html = client.get("/bulk", headers={"HX-Request": "true"}).get_data(as_text=True)
        m = re.search(r'<span class="bulk-cell-list[^>]*data-path="' + re.escape(alias) + r'"[^>]*>([^<]*)</span>', html)
        assert m, "the exponential_filter list cell renders"
        assert f'data-resolved="{resolved}"' in m.group(0)
        assert m.group(1) == "[[0.5,123.0]]"          # the reverted value, as the page renders it
