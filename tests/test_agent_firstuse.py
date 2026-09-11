"""First use of the Agent tab, and what two windows see of each other.

From the multi-user stress round (2026-09-11), every item re-verified on HEAD
before it was touched and each confirmed twice in real Chrome.

The behaviour is proven by `tests/stress_agent_firstuse_after.cjs` (10 checks
in a real browser); these are the wiring pins, because none of this can be
driven under jsdom — focus, layout and an iframe's own load event all need a
layout engine.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_STATIC = _ROOT / "quam_state_manager" / "web" / "static"


def _strip_js_comments(src: str) -> str:
    """Comments out; string literals kept. (Five pins in this project have
    tripped on their own explanation; the code below is commented with the
    very strings these look for.)"""
    out: list[str] = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c == '"' or c == "'":
            q, j = c, i + 1
            while j < n and src[j] != q:
                j += 2 if src[j] == "\\" else 1
            out.append(src[i:j + 1])
            i = j + 1
        elif src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


class TestYouCanTypeWithoutClickingFirst:
    """Measured on HEAD before the fix: `document.activeElement` was BODY at
    0 / 800 / 2500 ms, a plain 19-character sentence went nowhere at all, and a
    leading "/" was taken by the app-wide focus-search shortcut — which moved
    the rest of the line into the topbar box and replaced the whole Agent page
    with "No results for "run …""."""

    def test_the_home_focuses_its_own_composer(self):
        js = _strip_js_comments((_STATIC / "agent.js").read_text(encoding="utf-8"))
        blk = js[js.index("function mount(root, opts)"):]
        blk = blk[:blk.index("return m;")]
        assert "var ta0" in blk and "ta0.focus(" in blk

    def test_but_only_the_full_page_and_only_from_body(self):
        """The float is opened on top of whatever someone was doing, and a
        caret already somewhere else was chosen by the person."""
        js = _strip_js_comments((_STATIC / "agent.js").read_text(encoding="utf-8"))
        blk = js[js.index("function mount(root, opts)"):]
        blk = blk[:blk.index("return m;")]
        i = blk.index("var ta0")
        guard = blk[max(0, i - 120):i + 260]
        assert "!m.compact" in guard
        assert "document.body" in guard

    def test_the_slash_shortcut_prefers_a_visible_composer(self):
        """Belt and braces: a person can click away and come back. This is the
        one place that decides where "/" goes."""
        js = _strip_js_comments((_STATIC / "app.js").read_text(encoding="utf-8"))
        blk = js[js.index("function _primarySearch()"):]
        blk = blk[:blk.index("var SHEET_ID")]
        assert "_agentComposer()" in blk
        assert blk.index("_agentComposer()") < blk.index("global-search")

    def test_the_question_mark_is_a_character_there(self):
        js = _strip_js_comments((_STATIC / "app.js").read_text(encoding="utf-8"))
        i = js.rindex("ev.key === '?'")
        blk = js[i:i + 500]
        # the ACT, not the order of two names: `var comp = _agentComposer();`
        # survives any mutation of the branch below it, so an ordering assert
        # here passed against a branch that had been emptied out.
        assert "_insertAtCaret(comp, '?')" in blk
        assert blk.index("_insertAtCaret") < blk.index("_kbToggleCheatsheet")


class TestTheBadgeAndTheCounterCannotDisagree:
    """They are painted by the same call from the same object and used two
    different rules: measured "CONNECTED  claude … not registered as an MCP
    server" beside "3 setup steps", one of which was `connect_claude` — while
    the composer's own backend select was set to claude."""

    def _block(self) -> str:
        js = _strip_js_comments((_STATIC / "agent.js").read_text(encoding="utf-8"))
        i = js.index("function wirePaint()")
        return js[i:js.index("function wireLoad(", i)]

    def test_the_badge_is_about_the_cli_that_will_be_used(self):
        blk = self._block()
        assert 'var main = d["default"]' in blk
        assert "mainReg" in blk

    def test_it_uses_the_setup_list_as_the_one_rule(self):
        """`todo` is what the counter counts, so the badge counts it too."""
        blk = self._block()
        assert 'todo.indexOf("connect_" + main) < 0' in blk

    def test_a_machine_wide_summary_always_carries_the_count(self):
        blk = self._block()
        assert "OF" in blk and "otherReg" in blk
        # …and a bare CONNECTED is only reachable when nothing is outstanding
        i = blk.rindex('label = "CONNECTED"')
        assert "else" in blk[max(0, i - 60):i]

    def test_the_old_or_rule_is_gone(self):
        blk = self._block()
        assert "anyReg" not in blk


class TestTheOtherWindowSeesAModeChange:
    """Measured 30201 / 30497 / 30315 / 29558 ms over four trials: the mode
    route was the only mutating agent route that never woke the feed, so the
    other window showed a stale mode beside a live Start button — and mode is
    what decides whether the agent writes without asking."""

    def test_the_mode_route_wakes_the_feed_like_every_other_mutation(self):
        src = (_ROOT / "quam_state_manager" / "web" / "agent_api.py").read_text(encoding="utf-8")
        blk = src[src.index("def plan_mode(pid: str):"):]
        blk = blk[:blk.index("@agent_bp.route", 10)]
        assert "_bump()" in blk and "_wake()" in blk

    def test_every_mutating_plan_route_does(self):
        src = (_ROOT / "quam_state_manager" / "web" / "agent_api.py").read_text(encoding="utf-8")
        for name in ("def plan_mode(", "def plan_start("):
            blk = src[src.index(name):]
            blk = blk[:blk.index("@agent_bp.route", 10)]
            assert "_wake()" in blk, name


class TestSetupAnswersBesideTheControl:
    """The error rendered 792 px above the top of the scroller and deleted
    itself after 6 s, so the press was indistinguishable from doing nothing —
    while the message itself was a good one, naming the exact path."""

    def _alert(self) -> str:
        js = (_STATIC / "agent-setup.js").read_text(encoding="utf-8")
        i = js.index("function alertErr(")
        return js[i:js.index("\n  function ", i + 10)]

    def test_it_can_render_into_the_section_that_failed(self):
        blk = self._alert()
        assert "near" in blk and "querySelector(near)" in blk

    def test_both_callers_name_their_section(self):
        js = (_STATIC / "agent-setup.js").read_text(encoding="utf-8")
        assert 'alertErr(r.body.error, "#as-journal")' in js
        assert 'alertErr(r.body.error, "#as-ctx")' in js
        # no caller is left without one
        assert "alertErr(r.body.error);" not in js

    def test_it_does_not_delete_itself(self):
        blk = self._alert()
        assert "6000" not in blk, "a message a person can miss is the defect"
        assert "setTimeout" not in blk

    def test_an_unplaced_message_is_scrolled_to(self):
        blk = self._alert()
        assert "scrollIntoView" in blk
        assert 'role", "alert"' in blk
