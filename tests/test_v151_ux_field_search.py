"""B1 (U25): the header Search finds a parameter by the words of its path.

"x180 amplitude" listed only the y180 pulses' ``amplitude`` -- POINTERS whose
value spells ``#../x180_DragCosine/amplitude`` -- and never the x180
amplitudes themselves, because each word was tested against the leaf key,
the parent id and the value only. Now:

* every word is matched against the full dot path too;
* a leaf whose own path holds every word ranks above a pointer whose value
  merely mentions one;
* a pointer row says "points to <target>";
* a result click opens the field's inspector AT the field (focus + reveal);
* Ctrl+K finds pages AND actions (export, table, version, settings, help,
  CZ gate plan, chip report) and hands an unknown name to the field search.

The docs/96 grammar (space = AND, standalone | = OR) is unchanged.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.core.search_index import SearchIndex
from quam_state_manager.web.app import create_app

_ROOT = Path(__file__).resolve().parent.parent


def _merged():
    qs = {}
    for n in ("q1", "q2"):
        qs[n] = {"id": n, "xy": {"operations": {
            "x180_DragCosine": {"amplitude": 0.3001, "length": 40, "alpha": 0.5},
            "y180_DragCosine": {"amplitude": "#../x180_DragCosine/amplitude",
                                "length": "#../x180_DragCosine/length"},
            "x90_DragCosine": {"amplitude": 0.15, "length": 40},
        }}}
    return {"qubits": qs, "qubit_pairs": {}, "active_qubit_names": ["q1", "q2"]}


X180 = {f"qubits.{n}.xy.operations.x180_DragCosine.amplitude" for n in ("q1", "q2")}
Y180 = {f"qubits.{n}.xy.operations.y180_DragCosine.amplitude" for n in ("q1", "q2")}


class TestEngine:
    def test_the_x180_amplitudes_are_found_and_rank_first(self):
        idx = SearchIndex.build(_merged())
        hits = [r.dot_path for r in idx.search("x180 amplitude", limit=50)]
        assert X180 <= set(hits), hits
        top = set(hits[:len(X180)])
        assert top == X180, ("the leaves named by their own path must rank above the "
                             "pointers that mention them", hits)

    def test_the_pulse_name_spelled_out_finds_them_too(self):
        idx = SearchIndex.build(_merged())
        hits = [r.dot_path for r in idx.search("x180_dragcosine amplitude", limit=50)]
        assert set(hits[:2]) == X180, hits

    def test_a_dotted_word_spanning_into_the_leaf(self):
        idx = SearchIndex.build(_merged())
        hits = {r.dot_path for r in idx.search("x180_dragcosine.amplitude", limit=50)}
        assert X180 <= hits

    def test_grammar_unchanged_or_and_and(self):
        idx = SearchIndex.build(_merged())
        both = {r.dot_path for r in idx.search("x90 | y180 amplitude", limit=50)}
        assert {f"qubits.{n}.xy.operations.x90_DragCosine.amplitude" for n in ("q1", "q2")} <= both
        assert Y180 <= both
        assert idx.search("x180 zzznothing", limit=50) == []

    def test_an_added_and_removed_leaf_tracks_the_path_index(self):
        idx = SearchIndex.build(_merged())
        idx.search("x180")                                   # builds the lazy indexes
        p = "qubits.q1.xy.operations.x180_DragCosine.sigma"
        idx.add_entry(p, 4)
        assert p in {r.dot_path for r in idx.search("x180 sigma", limit=50)}
        idx.remove_entry(p)
        assert p not in {r.dot_path for r in idx.search("x180 sigma", limit=50)}


@pytest.fixture
def client(tmp_path):
    chip = tmp_path / "chip"
    chip.mkdir()
    (chip / "state.json").write_text(json.dumps(_merged()), encoding="utf-8")
    (chip / "wiring.json").write_text("{}", encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(chip)}).status_code < 400
    return c


class TestResultsPanel:
    def test_pointer_rows_say_where_they_point(self, client):
        html = client.get("/search?q=x180+amplitude", headers={"HX-Request": "true"}).get_data(as_text=True)
        rows = re.findall(r'<tr class="clickable-row" data-dot-path="([^"]+)"(.*?)</tr>', html, re.S)
        by = dict(rows)
        y = "qubits.q1.xy.operations.y180_DragCosine.amplitude"
        assert y in by
        assert "points to <code>x180_DragCosine.amplitude</code>" in by[y], by[y]
        x = "qubits.q1.xy.operations.x180_DragCosine.amplitude"
        assert "points to" not in by[x]
        order = [p for p, _ in rows]
        assert order.index(x) < order.index(y)

    def test_a_click_opens_the_inspector_at_the_field(self, client):
        html = client.get("/search?q=x180+amplitude", headers={"HX-Request": "true"}).get_data(as_text=True)
        x = "qubits.q1.xy.operations.x180_DragCosine.amplitude"
        m = re.search(r'data-dot-path="' + re.escape(x) + r'" hx-get="([^"]+)"', html)
        assert m, "the row does not open the inspector"
        url = m.group(1).replace("&amp;", "&")
        assert url.startswith("/qubit/q1?") and "focus=" in url and "flash=1" in url
        detail = client.get(url, headers={"HX-Request": "true"}).get_data(as_text=True)
        assert 'window.focusEditInput("' + x + '", {reveal: true})' in detail
        # the plain inspector (no search) never reveals
        plain = client.get("/qubit/q1?focus=" + x, headers={"HX-Request": "true"}).get_data(as_text=True)
        assert "{reveal: true}" not in plain


class TestPalette:
    def test_pages_and_actions(self, client):
        html = client.get("/").get_data(as_text=True)
        m = re.search(r'<script id="cmd-palette-data"[^>]*>(.*?)</script>', html, re.S)
        pages = json.loads(m.group(1))["pages"]
        labels = " | ".join(p["label"] for p in pages).lower()
        for word in ("export", "parameter table", "version", "settings", "help",
                     "cz gate plan", "chip report"):
            assert word in labels, (word, labels)
        settings = next(p for p in pages if p["label"] == "Settings")
        assert settings.get("action") == "settings"
        report = next(p for p in pages if "chip report" in p["label"].lower())
        assert report.get("action") == "newtab" and report["url"] == "/chip-status/report"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_palette_and_reveal_in_the_real_app_js():
    proc = subprocess.run(
        ["node", str(_ROOT / "tests" / "field_search_selfcheck.cjs")],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT), timeout=120)
    if proc.returncode == 2 and "jsdom not installed" in (proc.stderr or ""):
        pytest.skip("jsdom not installed")
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
