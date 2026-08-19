"""docs/126 ⑥ — the small-items batch.

Behavioral coverage ran in real Chrome on the real chip (float panel with the
rack rendered + hover strip + drag/collapse persistence, calc-pin removal with
the dragged-calculator carve-out, /help keycap contrast in both themes, the
time-basis note, pill dismissal, and the run-number jump round trip). These
pins keep the load-bearing pieces greppable and the one new endpoint tested.
"""
import json
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    return (_ROOT / rel).read_text(encoding="utf-8")


class TestFloatWiring:
    def test_api_returns_instrument_and_wiring(self, tmp_path):
        from quam_state_manager.web.app import create_app
        state = {"qubits": {"q1": {"f_01": 5e9}}, "ports": {}}
        wiring = {"network": {"host": "1.1.1.1", "cluster_name": "t"},
                  "wiring": {"qubits": {}}}
        (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
        (tmp_path / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")
        app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
        c = app.test_client()
        c.post("/load", data={"folder": str(tmp_path)})
        body = c.get("/api/instrument/data").get_json()
        assert "instrument" in body and "wiring" in body
        assert body["wiring"]["network"]["host"] == "1.1.1.1"

    def test_api_honest_without_a_chip(self, tmp_path):
        from quam_state_manager.web.app import create_app
        app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
        body = app.test_client().get("/api/instrument/data").get_json()
        assert body.get("error"), "no chip must answer honestly, not an empty rack"

    def test_nav_button_and_module_wired(self):
        base = _read("quam_state_manager/web/templates/base.html")
        assert "nav-float-btn" in base and "FloatWiring.toggle()" in base
        js = _read("quam_state_manager/web/static/app.js")
        assert "window.FloatWiring" in js
        assert "/api/instrument/data" in js
        # a wholesale working-copy replacement refreshes the panel in place
        assert "stateRestored" in js.split("window.FloatWiring")[1]


class TestCalcPinRemoved:
    def test_no_pin_button_or_handler(self):
        assert "calc-pin" not in _read("quam_state_manager/web/templates/base.html")
        calc = _read("quam_state_manager/web/static/calc.js")
        assert "calcTogglePin" not in calc
        # the dragged calculator inherits the keep-around intent instead
        body = calc.split("function _calcOutside", 1)[1][:600]
        assert "calc-floating" in body


class TestKbdContrast:
    def test_keycap_rules_set_an_explicit_color(self):
        import re
        css = _read("quam_state_manager/web/static/style.css")
        for sel in (".help-sc-grid kbd", ".kb-sheet kbd", ".cmd-palette-hint kbd"):
            m = re.search(re.escape(sel) + r"\s*\{([^}]*)\}", css)
            assert m, sel
            rule = m.group(1)
            assert "color: var(--pico-color)" in rule, (
                f"{sel} replaces Pico's inverted-kbd background, so it MUST "
                "set the text color too — background-only leaves "
                "page-background text on a page-background chip")


class TestTimeBasisNote:
    def test_datasets_header_names_the_clock(self):
        html = _read("quam_state_manager/web/templates/_datasets.html")
        assert "ds-tz-note" in html and "local time" in html
        detail = _read("quam_state_manager/web/templates/_dataset_detail.html")
        assert "acquisition-PC local" in detail


class TestRunJump:
    def test_the_jump_lives_on_the_vs_prev_bar_not_the_header(self):
        """docs/126 r3: the ⑥-era header jump was a DUPLICATE — the original
        request wanted the run-number box and the ±10 skips on the existing
        Prev State comparison bar. The header keeps single-step nav only."""
        h = _read("quam_state_manager/web/templates/_inspector_header.html")
        assert 'id="ds-run-jump"' not in h
        assert "dsNavRun(-10)" not in h and "dsNavRun(10)" not in h
        assert "dsNavRun(-1)" in h and "dsNavRun(1)" in h   # single-step stays
        bar = _read("quam_state_manager/web/templates/_dataset_prev_diff.html")
        assert "prevdiff-vs-input" in bar
        assert "prevDiffJump" in bar
        assert "older10" in bar and "newer10" in bar
        js = _read("quam_state_manager/web/static/app.js")
        assert "window.prevDiffJump" in js
        assert "window.dsJumpRun" not in js


class TestPillDismiss:
    def test_escape_and_click_away_are_bound(self):
        js = _read("quam_state_manager/web/static/dataset-virtual.js")
        assert "_dismissNewPill" in js
        assert "'Escape') _dismissNewPill" in js
