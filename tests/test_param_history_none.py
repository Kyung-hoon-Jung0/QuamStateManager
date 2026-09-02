"""docs/158 — Param History: None means none, and a filter change does not
shake the page.

Customer: "qubits에서 None을 클릭해도 잠깐 SM이 흔들리더니 다시 전체선택으로
복귀함 / properties는 None을 클릭하면 T1, T2 등 몇개만 선택됨". Two causes:

- the route read an EMPTY selection as "no filter" (``props or DEFAULT``,
  ``qubits or None``), so the None button's request came back as the
  defaults / every qubit and the re-render flipped the chips back on;
- the form swapped the WHOLE ``#param-history-root`` (the header, the
  alignment load-fragment re-fetching its banner, the form itself) with the
  page-load popup flashing over it.

Pinned here: the explicit-empty contract (the form always carries the
parameter; absence alone means default), the chips' lit state under None,
the honest empty grid, the results-only swap, and the JS half (sparklines
redrawn on the results swap, the loader exempting filter requests).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

_ROOT = Path(__file__).resolve().parent.parent
_TPL = _ROOT / "quam_state_manager" / "web" / "templates" / "_param_history.html"
_APP_JS = (_ROOT / "quam_state_manager" / "web" / "static" / "app.js").read_text(encoding="utf-8")


def _state():
    return {
        "qubits": {
            "qA1": {"id": "qA1", "f_01": 6.25e9, "T1": 8834, "T2ramsey": 4000, "T2echo": 6000},
            "qA2": {"id": "qA2", "f_01": 6.31e9, "T1": 9120, "T2ramsey": 4100, "T2echo": 6100},
        },
        "active_qubit_names": ["qA1", "qA2"],
    }


@pytest.fixture
def client(tmp_path):
    folder = tmp_path / "quam_state"
    folder.mkdir()
    (folder / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(
        {"wiring": {"qubits": {"qA1": {}, "qA2": {}}}, "network": {"host": "10.1.1.18"}}), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    c = app.test_client()
    c.post("/load", data={"folder": str(folder)})
    # two snapshots (a value moved between them) so the grid has cells to
    # show — or, under None, to NOT show
    hm = app.config["history_manager"]
    hm.check_and_snapshot(folder, "manual", force=True)
    st = _state()
    st["qubits"]["qA1"]["T1"] = 9000
    (folder / "state.json").write_text(json.dumps(st), encoding="utf-8")
    hm.check_and_snapshot(folder, "manual", force=True)
    return c


def _get(client, qs=""):
    return client.get("/param-history" + qs, headers={"HX-Request": "true"}).get_data(as_text=True)


def _row(html: str, label: str) -> str:
    i = html.index(f">{label}</label>")
    j = html.index('<div class="phf-row">', i)
    return html[i:j]


class TestExplicitEmpty:
    def test_absent_means_the_default_view(self, client):
        html = _get(client)
        assert 'class="phf-chip phf-chip-prop active"' in html          # the T1/T2 defaults lit
        assert 'class="phf-chip phf-chip-qubit active"' in html         # every qubit lit
        assert "Nothing selected." not in html

    def test_props_none_is_none(self, client):
        html = _get(client, "?props=&qubits=&qubits=qA1&qubits=qA2")
        assert 'phf-chip-prop active' not in html, "no property chip may be lit"
        assert "Nothing selected." in html and "No properties are selected" in html
        assert 'class="history-cell"' not in html
        # the qubit row is untouched by the property None
        assert _row(html, "Qubits").count('phf-chip-qubit active') == 2

    def test_qubits_none_is_none(self, client):
        html = _get(client, "?qubits=&props=&props=T1")
        assert 'phf-chip-qubit active' not in html, "no qubit chip may be lit"
        assert "No qubits are selected" in html
        assert 'class="history-cell"' not in html
        assert _row(html, "Properties").count('phf-chip-prop active') == 1

    def test_both_none(self, client):
        html = _get(client, "?props=&qubits=")
        assert "No properties and no qubits are selected" in html
        assert 'phf-chip-qubit active' not in html and 'phf-chip-prop active' not in html

    def test_a_partial_selection_still_works(self, client):
        html = _get(client, "?props=&props=T1&qubits=&qubits=qA2")
        props_row = _row(html, "Properties")
        assert props_row.count("phf-chip-prop active") == 1
        assert 'value="T1" checked' in props_row[props_row.index("phf-chip-prop active"):][:200]
        assert _row(html, "Qubits").count("phf-chip-qubit active") == 1
        assert 'data-qubit="qA2"' in html and 'data-qubit="qA1"' not in html
        assert "Nothing selected." not in html

    def test_the_form_always_carries_the_parameter(self, client):
        """The hidden empty value is what turns "every chip off" into an
        explicit ``props=`` / ``qubits=`` instead of an absent parameter."""
        html = _get(client)
        form = re.search(r'<form id="param-history-filters".*?</form>', html, re.S).group(0)
        assert form.count('<input type="hidden" name="props" value="">') == 1
        assert form.count('<input type="hidden" name="qubits" value="">') == 1
        # and the togglers only flip the real chips, never the hidden marker
        assert ".phf-chips input[type=\"checkbox\"]" in _APP_JS[_APP_JS.index("function paramHistoryFilterSetRow("):][:600]

    def test_reset_still_means_default(self, client):
        html = _get(client)
        assert 'href="/param-history" hx-get="/param-history" hx-target="#param-history-root"' in html


class TestResultsOnlySwap:
    def test_everything_a_filter_changes_is_inside_the_results_container(self, client):
        html = _get(client)
        i = html.index('<div id="param-history-results">')
        j = html.index("{# /#param-history-results #}") if "{# /#param-history-results #}" in html else None
        assert j is None  # Jinja comments never reach the client
        body = html[i:]
        assert 'class="param-history-summary"' in body
        assert 'id="param-history-drawer"' in body
        assert 'class="param-history-grid"' in body or "param-history-empty" in body
        # the form and the alignment slot stay OUTSIDE it (they must not re-render)
        assert html.index('id="param-history-filters"') < i
        assert html.index('id="ph-alignment-slot"') < i

    def test_the_template_closes_the_container_before_the_root(self):
        tpl = _TPL.read_text(encoding="utf-8")
        assert tpl.index('<div id="param-history-results">') < tpl.index('id="param-history-drawer"')
        assert "</div>{# /#param-history-results #}" in tpl
        assert tpl.index("</div>{# /#param-history-results #}") < tpl.index("<script>")

    def test_sparklines_redraw_on_the_results_swap(self):
        """The full render's trailing inline <script> is outside the selected
        element, so the afterSwap hook must draw them for the results swap."""
        i = _APP_JS.index("if (evt.target.id === 'param-history-results') {")
        assert "renderParamHistorySparklines();" in _APP_JS[i:i + 200]
        # …without re-arming the auto-backfill (that is a page-visit concern)
        seg = _APP_JS[i:i + 200]
        assert "paramHistoryMaybeAutoBackfill" not in seg

    def test_the_loader_exempts_filter_requests(self):
        i = _APP_JS.index("function isSlow(detail) {")
        seg = _APP_JS[i:i + 900]
        assert "elt.closest('#param-history-filters')" in seg and "return false" in seg
