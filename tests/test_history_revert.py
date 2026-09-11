"""docs/186 — the point of a value history is putting a value back.

Customer, on-site, with a screenshot of the applied-to-live log open over the
grid:

    "auto mode를 켜고나서 수정하면, 그림처럼 자동으로 수정이력 pop list가 뜬다.
     이거 default로 계속 접혀진 상태로 두자. -- 그리고 가장 중요한 고객 피드백.
     지금 X표시가 있는데, 중요한건 이게 아니라, 지난 history를 보면서 이전 값으로
     되돌릴 수있는 UI가 있어야 한다는 점. 즉, 이전 value와의 diff에서 revert라는
     버튼이 있어야하는것. 그게 진짜 이것의 순기능."

Two things, and the second is the real one.

**(a)** ``applyLogState`` read an **absent** preference as OPEN, so arming auto
mode and editing popped a list over the page every time. Absent means collapsed
now; only a deliberate open is remembered.

**(b)** The ✕ undoes the **last** write, one step, this session only. What was
missing is what the history panel exists for: look back, see what a value used
to be, and put it back. The panel had ``Use``, which *fills an edit input* — it
needs one on screen, it needs Enter afterwards, and it does nothing at all where
the panel is opened without one (the Calibration log calls
``FieldHistory.open(el, path, null)``).

``↺ Revert`` stages the old value through ``/field/edit`` — the one door, with
its type coercion, its FSP compensation offer, its chip-identity gate, its tray
row and its Ctrl+Z. Nothing reaches the live chip until Apply.

And the **diff** the customer asked for is the one that answers *what will this
button do*: current → this value. The delta already on the row is a different
question (what that point introduced when it happened), so both are shown and
both are labelled.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

_ROOT = Path(__file__).resolve().parent.parent
_STATIC = _ROOT / "quam_state_manager" / "web" / "static"
_TPL = _ROOT / "quam_state_manager" / "web" / "templates"

_WIRING = {"network": {"host": "1.1.1.1", "cluster_name": "C1"}}


def _chip(folder: Path, t1=2.0e-5):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps({
        "qubits": {"q1": {"id": "q1", "f_01": 5.0e9, "T1": t1}},
        "qubit_pairs": {}, "active_qubit_names": ["q1"],
    }), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")


@pytest.fixture
def env(tmp_path):
    live = tmp_path / "chip"
    _chip(live)
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    return {"app": app, "client": c, "live": live}


class TestTheLogStaysCollapsed:
    """(a) — an absent preference is COLLAPSED."""

    def test_absent_means_collapsed(self):
        js = (_STATIC / "auto-apply.js").read_text(encoding="utf-8")
        i = js.index("function applyLogState")
        blk = js[i:i + 700]
        assert "=== '1'" in blk, blk
        assert "!== '0'" not in blk, "an absent preference still reads as open"
        assert "var open = false" in blk

    def test_a_deliberate_open_is_still_remembered(self):
        """Collapsed by default must not mean 'collapses again every render' —
        the toggle still persists, which is the half that makes the default
        tolerable."""
        js = (_STATIC / "auto-apply.js").read_text(encoding="utf-8")
        i = js.index("toggleLog:")
        blk = js[i:i + 400]
        assert "sessionStorage.setItem" in blk
        assert "'0' : '1'" in blk


class TestTheRevertButton:
    """(b) — the panel's primary action."""

    def _panel(self, env, path="qubits.q1.T1"):
        r = env["client"].get("/field/history?path=" + path)
        assert r.status_code == 200, r.data[:200]
        return r.data.decode()

    def test_the_template_offers_revert_on_a_past_value(self, env):
        """With REAL history behind it: snapshot, change the value, snapshot
        again. Skipping here for lack of snapshots would have meant the row
        assertions never ran at all."""
        c = env["client"]
        assert c.post("/state-history/snapshot").status_code == 200
        assert c.post("/field/edit", data={"dot_path": "qubits.q1.T1",
                                           "value": "9.9e-5"}).status_code == 200
        assert c.post("/state/apply-to-live").status_code == 200
        assert c.post("/state-history/snapshot").status_code == 200

        html = self._panel(env)
        assert "fh-table" in html, "the fixture still produced no history rows"
        assert "fh-revert" in html
        assert "FieldHistory.revertTo(" in html
        # the button carries a value that is NOT the current one
        vals = re.findall(r'class="fh-revert" data-value="([^"]*)"', html)
        assert vals, html[:400]
        assert any("2" in v for v in vals), vals

    def test_a_valueless_point_offers_no_revert(self, env):
        """`pt.fill` is "" for a point whose value was not set. Reverting to
        "not set" is not the same act as writing an empty string, and
        /field/edit would take the latter — so the row offers nothing, the way
        a suggestion that cannot work is never offered (docs/175)."""
        t = (_TPL / "_field_history.html").read_text(encoding="utf-8")
        assert "not pt.is_current and pt.fill" in t

    def test_the_revert_delta_renders_beside_the_button(self, env):
        """The customer's actual ask, in the rendered panel rather than in the
        template source: a diff against the current value, next to Revert."""
        c = env["client"]
        assert c.post("/state-history/snapshot").status_code == 200
        assert c.post("/field/edit", data={"dot_path": "qubits.q1.T1",
                                           "value": "9.9e-5"}).status_code == 200
        assert c.post("/state/apply-to-live").status_code == 200
        assert c.post("/state-history/snapshot").status_code == 200

        html = self._panel(env)
        assert "fh-revert-delta" in html, "no current-vs-this delta rendered"
        i = html.index("fh-revert")
        j = html.index("fh-revert-delta")
        assert j > i and j - i < 700, "the delta is not beside its button"

    def test_revert_is_offered_on_every_non_current_row(self):
        """Source-level, because a chip with no snapshots renders no rows at
        all and the route pin above would skip. The row template is where the
        rule lives."""
        t = (_TPL / "_field_history.html").read_text(encoding="utf-8")
        i = t.index("{% if not pt.is_current and pt.fill %}")
        blk = t[i:t.index("{% endif %}", i)]
        assert "FieldHistory.revertTo(this)" in blk
        assert 'data-path="{{ hist.dot_path }}"' in blk, \
            "the button must carry its own path — the panel can be opened with no input"

    def test_the_current_row_offers_no_revert(self):
        """Reverting to the value you already have is a no-op that would still
        stage a tray row."""
        t = (_TPL / "_field_history.html").read_text(encoding="utf-8")
        assert t.count("fh-revert\"") == 1
        i = t.index("fh-revert\"")
        guard = t.rfind("{% if not pt.is_current and pt.fill %}", 0, i)
        assert guard != -1 and guard < i

    def test_use_is_still_there_and_says_what_it_does(self):
        """Revert stages; Use fills the box so you can adjust first. Two jobs,
        and the labels now say which is which."""
        t = (_TPL / "_field_history.html").read_text(encoding="utf-8")
        assert "FieldHistory.useValue(this)" in t
        assert "without staging it" in t

    def test_the_revert_delta_is_against_the_current_value(self):
        """The row already carried a delta, and it answers a DIFFERENT question
        (what this point introduced). The customer asked for the one beside the
        button: what reverting would do now."""
        t = (_TPL / "_field_history.html").read_text(encoding="utf-8")
        assert "delta_pct(current_value, pt.value, 'fh-revert-delta')" in t,             "the revert delta must use the COMPACT variant — the full-precision "            "text buries the button in a 99px column (docs/186)"
        assert "delta_chip(_prev.value, pt.value, 'fh-delta')" in t, \
            "the introduced-delta must survive — they are two different facts"

    def test_the_route_ships_the_current_value(self, env):
        html = self._panel(env)
        assert html is not None
        import quam_state_manager.web.routes as routes_mod
        src = Path(routes_mod.__file__).read_text(encoding="utf-8")
        assert "current_value=current," in src


class TestRevertGoesThroughTheOneDoor:
    """A revert must not be a side door around any gate the other editors pass."""

    def _blk(self):
        js = (_STATIC / "app.js").read_text(encoding="utf-8")
        i = js.index("function revertTo(btn)")
        return js[i:i + 3200]

    def test_it_posts_to_field_edit(self):
        assert '"/field/edit"' in self._blk()

    def test_it_declares_the_chip_token(self):
        assert 'body.append("expect_chip"' in self._blk()

    def test_it_answers_the_fsp_offer(self):
        blk = self._blk()
        assert "fsp_compensation" in blk and "_openFspPopup" in blk
        assert "fsp_ack" in blk

    def test_it_answers_the_type_fix_offer(self):
        blk = self._blk()
        assert "type_fix" in blk and "_confirmTypeFix" in blk

    def test_it_refreshes_the_tray(self):
        assert "_swapPendingTray" in self._blk()

    def test_it_says_the_live_chip_is_untouched(self):
        """The covenant in the toast: staged, not written."""
        blk = self._blk()
        assert "staged" in blk and "not yet on the live chip" in blk

    def test_it_is_exported(self):
        js = (_STATIC / "app.js").read_text(encoding="utf-8")
        i = js.index("return { open: open, close: close, useValue: useValue,")
        assert "revertTo: revertTo" in js[i:i + 200]

    def test_a_revert_really_stages_through_that_door(self, env):
        """End to end: what the button posts, posted."""
        c = env["client"]
        assert 'data-change-count="0"' in c.get("/state/tray").data.decode()
        r = c.post("/field/edit", data={"dot_path": "qubits.q1.T1",
                                        "value": "9.9e-5"})
        assert r.status_code == 200
        tray = c.get("/state/tray").data.decode()
        assert 'data-change-count="1"' in tray
        assert "qubits.q1.T1" in tray
        # …and the live chip is untouched until Apply.
        doc = json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))
        assert doc["qubits"]["q1"]["T1"] == 2.0e-5
