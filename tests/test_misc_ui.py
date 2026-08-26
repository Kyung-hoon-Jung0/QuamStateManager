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


class TestSidebarActiveSync:
    """docs/126 r3: three independent active-setters used to fight — the
    htmx:pushedIntoHistory handler compared query-carrying hrefs against the
    bare path (subnav links never toggled; same-href parent+child both lit),
    chipNavView's manual push/replaceState fired no htmx history event (the
    OLD menu's active never cleared), and chip-status's _setActiveTab only
    touched its own group. One canonical sync now re-derives the active set
    from the URL on every navigation path."""

    def test_one_canonical_sync_wired_into_every_nav_path(self):
        js = _read("quam_state_manager/web/static/app.js")
        assert "window.syncSidebarNavActive = function" in js
        i = js.index("window.syncSidebarNavActive = function")
        block = js[i:i + 4000]
        # clears everything first, prefers the child on same-href twins
        assert 'classList.remove("active")' in block
        assert "nav-subitems" in block
        # wired into all three navigation paths
        assert 'addEventListener("htmx:pushedIntoHistory", window.syncSidebarNavActive)' in js
        assert 'addEventListener("htmx:replacedInHistory", window.syncSidebarNavActive)' in js
        assert '"popstate"' in js
        # chipNavView (manual history writes) calls it on BOTH branches
        j = js.index("window.chipNavView")
        cnv = js[j:j + 1600]
        assert cnv.count("syncSidebarNavActive") >= 2
        # the old broken matcher (first path segment vs query-carrying href)
        # must be gone
        assert 'split("/")[0] || "home"' not in js


class TestNavProgress:
    """docs/126 r3 제안: a heavy first open (Param History on a big chip) gave
    no sign of life. The brand area becomes a blue indeterminate sweep + an
    elapsed-seconds counter after a 400 ms grace — honest by construction
    (the server reports no progress, so no percentage is invented)."""

    def test_brand_carries_the_indicator(self):
        h = _read("quam_state_manager/web/templates/base.html")
        assert 'id="nav-progress"' in h
        i = h.index('class="app-title-link"')
        assert 'id="nav-progress"' in h[i:i + 900], "must overlay the brand"

    def test_module_grace_and_dedup(self):
        js = _read("quam_state_manager/web/static/app.js")
        assert "setTimeout(show, 400)" in js          # fast navs never flash
        assert "WeakSet" in js                        # dup terminal events ignored
        assert "'htmx:sendAbort'" in js               # hx-sync replace settles too

    def test_css_sweep_and_reduced_motion(self):
        css = _read("quam_state_manager/web/static/style.css")
        assert "nav-progress-sweep" in css
        assert "tabular-nums" in css                  # the counter must not jitter
        i = css.index(".nav-progress-bar::after")
        assert "prefers-reduced-motion" in css[i:i + 900]


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


class TestConflictTrayButtonRow:
    def test_the_force_button_stays_in_the_row(self):
        """docs/139 follow-up (customer report): margin-left:auto pushed
        'Keep mine' to the far edge, and once the row wrapped it sat alone on
        a second line right-aligned - a layout break, not a choice. It must
        flow in the row with the other three."""
        import re
        from pathlib import Path
        css = Path("quam_state_manager/web/static/style.css").read_text(
            encoding="utf-8")
        m = re.search(
            r"\.tray-conflict-choices \.tray-force-btn \{([^}]*)\}", css)
        assert m, "the force-button rule disappeared"
        assert "margin-left: auto" not in m.group(1)


class TestAppliedLogInline:
    def test_applied_log_no_longer_forces_its_own_row(self):
        """User feedback: even collapsed (nothing to show), the applied-log
        widget forced a full extra row under the topbar tray because the
        OUTER element carried flex: 0 0 100% — the empty space beside
        Auto-Sync on the SAME row went unused. It must be an ordinary inline
        flex item now, with the expandable list floating as an anchored
        dropdown instead of pushing the row."""
        import re
        from pathlib import Path
        css = Path("quam_state_manager/web/static/style.css").read_text(
            encoding="utf-8")
        m = re.search(r"\n\.applied-log \{([^}]*)\}", css)
        assert m, "the .applied-log rule disappeared"
        assert "flex: 0 0 100%" not in m.group(1)
        m2 = re.search(r"\.applied-log-list \{([^}]*)\}", css)
        assert m2, "the .applied-log-list rule disappeared"
        assert "position: absolute" in m2.group(1), (
            "the expandable list must float, not push the row")
