"""docs/141 4aj — one search hint, and the three tool windows' frames.

Four user reports in one round: the Settings window's top corners were cut,
the Calculator could not be resized, its sections were flat grey, and the diff
search box drew two magnifiers over its own placeholder. The last one came with
a directive: every search box in SM says the same compact thing.

What is pinned here is the part a stylesheet edit or a new template can quietly
break — the ONE placeholder source, the surfaces that must be able to keep the
promise it makes, and the four CSS contracts behind the three window fixes.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.core import search_query
from quam_state_manager.web.app import create_app

_ROOT = Path(__file__).resolve().parent.parent
_TPL = _ROOT / "quam_state_manager/web/templates"
_CSS = (_ROOT / "quam_state_manager/web/static/style.css").read_text(encoding="utf-8")


class TestTheHint:
    def test_the_house_text(self):
        assert search_query.search_hint() == "Search: space = AND, | = OR"

    def test_scopes_are_appended_never_substituted(self):
        # a surface with its own scopes still names the grammar FIRST: the
        # operators are what nobody can guess, the scopes are the extra
        out = search_query.search_hint("tag:", "is:")
        assert out.startswith("Search: space = AND, | = OR")
        assert out.endswith("tag:, is:")

    def test_empty_extras_change_nothing(self):
        assert search_query.search_hint("", None) == search_query.search_hint()  # type: ignore[arg-type]

    def test_the_title_carries_the_full_sentence(self):
        t = search_query.search_title()
        assert "AND" in t and "standalone |" in t and "literal" in t
        assert search_query.search_title("tag:").endswith("Scopes here: tag:.")

    def test_it_describes_the_grammar_the_module_implements(self):
        """The hint is a claim about search_query itself — check it holds."""
        assert search_query.matches_hay("alpha beta", search_query.groups("alpha beta"))
        assert not search_query.matches_hay("alpha", search_query.groups("alpha beta"))
        assert search_query.matches_hay("beta", search_query.groups("alpha | beta"))


class TestEveryBoxUsesIt:
    """A template that writes its own placeholder is the drift this stops."""

    # Not search boxes: narrow PICKERS over a short list of names (which sort
    # key, which experiment class), whose own aria-label already says what they
    # pick. Exempt by id, so a new box cannot inherit the exemption by copying
    # a placeholder string.
    PICKERS = {"sort-key-filter", "sort-param-filter", "sched-lib-filter"}
    ALLOWED = {"= e.g. 0.5*10^(-25/20)"}

    def test_no_template_hand_writes_a_search_placeholder(self):
        offenders = []
        for path in sorted(_TPL.glob("*.html")):
            for tag in re.findall(r"<(?:input|textarea)\b[^>]*>", path.read_text(encoding="utf-8"), re.S):
                m = re.search(r'placeholder="([^"]*)"', tag)
                if not m:
                    continue
                ph = m.group(1)
                el_id = (re.search(r'id="([^"]*)"', tag) or [None, ""])[1]
                if ph in self.ALLOWED or "{{" in ph or el_id in self.PICKERS:
                    continue
                if re.search(r"search|filter", ph, re.I):
                    offenders.append(f"{path.name}: {ph}")
        assert not offenders, "these still hand-write a search placeholder:\n" + "\n".join(offenders)

    def test_the_boxes_that_matter_render_it(self, tmp_path):
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        client = app.test_client()
        hint = search_query.search_hint()
        for url in ("/", "/datasets", "/diff"):
            html = client.get(url).data.decode()
            assert hint in html, f"{url} renders no search hint"

    def test_the_exempt_pickers_are_still_only_three(self):
        """The exemption is a list, not a loophole: if one of these grows into
        a real search box it should join the house text, and if a fourth
        appears someone has to decide which it is."""
        found = {pid for path in _TPL.glob("*.html")
                 for pid in self.PICKERS
                 if f'id="{pid}"' in path.read_text(encoding="utf-8")}
        assert found == self.PICKERS

    def test_a_scoped_box_names_its_scopes(self):
        """The Datasets box really has tag:/is:, so it says so — after the
        grammar, never instead of it. Pinned on the template because the box
        renders only once a dataset folder is configured."""
        html = (_TPL / "_datasets.html").read_text(encoding="utf-8")
        i = html.index("search_hint('tag:', 'is:')")
        assert "search_title(" in html[i:i + 400]


class TestTheGrammarIsRealEverywhere:
    """The placeholder promises OR on boxes that were AND-only until 4aj."""

    def test_the_client_surfaces_compose_through_searchquery(self):
        app_js = (_ROOT / "quam_state_manager/web/static/app.js").read_text(encoding="utf-8")
        av_js = (_ROOT / "quam_state_manager/web/static/all-values.js").read_text(encoding="utf-8")
        # filterTable + filterDetailPanel
        assert app_js.count("window.SearchQuery.groups(raw)") == 2
        assert "if (grps) match = window.SearchQuery.matchesHay(texts[i], grps);" in app_js
        assert "if (dGrps) matched = window.SearchQuery.matchesHay(hay, dGrps);" in app_js
        # the all-values grid composes at the GROUP level (its tokens are scoped)
        assert "window.SearchQuery.groupBy(tokens" in av_js
        # the guard and the call must name the SAME thing (docs/125 standing
        # rule: a bare global read behind a window.X guard throws in a Node realm)
        for js, name in ((app_js, "app.js"), (av_js, "all-values.js")):
            for m in re.finditer(r"window\.SearchQuery\s*\?\s*([A-Za-z.]+)", js):
                assert m.group(1).startswith("window."), f"{name}: bare {m.group(1)} behind a window guard"

    def test_selfcheck(self):
        node = shutil.which("node")
        if node is None or subprocess.run([node, "-e", "require('jsdom')"],
                                          capture_output=True, cwd=str(_ROOT)).returncode != 0:
            pytest.skip("jsdom not installed for node")
        res = subprocess.run([node, str(_ROOT / "tests" / "search_hint_selfcheck.cjs")],
                             capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT))
        assert res.returncode == 0, res.stdout + res.stderr
        assert "ok - filterTable: a standalone | is OR" in res.stdout
        assert "ok - detail panel: a standalone | is OR" in res.stdout


class TestTheThreeWindows:
    """The CSS contracts behind the three reported window defects."""

    def test_a_square_header_no_longer_paints_over_the_rounded_corner(self):
        """The panels are radius 10 with a 1.5px border and only two of the
        three clip their children; the header rounds itself instead."""
        i = _CSS.index(".settings-header, .calc-header, .manual-header {")
        rule = _CSS[i:_CSS.index("}", i)]
        assert "border-top-left-radius: 8.5px" in rule and "border-top-right-radius: 8.5px" in rule

    def test_the_calculator_is_a_resizable_window(self):
        i = _CSS.index("\n.calc-popover {")
        rule = _CSS[i:_CSS.index("}", i)]
        assert "resize: both" in rule
        # CSS resize is inert while overflow is visible, and the body must be
        # what scrolls or the header/footer scroll away with it
        assert "overflow: hidden" in rule and "display: flex" in rule
        assert "min-width" in rule and "min-height" in rule
        j = _CSS.index(".calc-body {")
        assert "overflow-y: auto" in _CSS[j:_CSS.index("}", j)]

    def test_the_size_is_remembered(self):
        js = (_ROOT / "quam_state_manager/web/static/calc.js").read_text(encoding="utf-8")
        assert "quam_calc_size" in js and "ResizeObserver" in js
        assert "_calcApplied" in js, "a viewport clamp must not overwrite the user's size"

    def test_section_titles_are_not_muted_grey(self):
        """`.calc-sec-label { color: var(--pico-contrast) }` (0,1,0) never won:
        Pico paints `details summary:not([role])` (0,1,1) and
        `details[open] > summary:not([role]):not(:focus)` (0,3,1) from its
        accordion variables. Fixed by setting those variables, not by a
        specificity war — so this pins the variables, not the color."""
        i = _CSS.index(".calc-popover {\n    --pico-accordion")
        rule = _CSS[i:_CSS.index("}", i)]
        assert "--pico-accordion-close-summary-color: var(--pico-color)" in rule
        assert "--pico-accordion-open-summary-color: var(--pico-color)" in rule

    def test_sections_read_as_sections(self):
        i = _CSS.index("\n.calc-sec {")
        rule = _CSS[i:_CSS.index("}", i)]
        assert "border:" in rule and "border-radius" in rule, "a hairline was not a boundary"
        assert ".calc-sec[open] > .calc-sec-label {" in _CSS, "the open section must be marked"
        # one disclosure mark, not Pico's chevron AND the app's caret
        assert ".calc-sec-label::after { display: none; }" in _CSS

    def test_grey_is_kept_for_annotations_only(self):
        for sel, want in ((".calc-field {", "var(--pico-color)"),
                          (".calc-rlabel {", "var(--pico-color)"),
                          (".calc-help {", "var(--pico-muted-color)"),
                          (".calc-unit {", "var(--pico-muted-color)")):
            i = _CSS.index(sel)
            assert want in _CSS[i:_CSS.index("}", i)], sel


class TestOneMagnifier:
    def test_an_input_with_our_icon_never_draws_picos(self):
        """Pico gives every input[type=search] a background magnifier plus the
        padding that reserves room for it; a compact padding shorthand keeps
        the icon and drops the reserve, which is how it landed on top of the
        placeholder's first letter."""
        i = _CSS.index('.tree-search-icon + input[type="search"] {')
        rule = _CSS[i:_CSS.index("}", i)]
        assert "background-image: none" in rule and "padding-inline-start" in rule

    def test_the_diff_box_is_that_pairing(self):
        html = (_TPL / "_diff_search.html").read_text(encoding="utf-8")
        assert "tree-search-icon" in html and 'type="search"' in html
        # icon immediately before the input — the selector is a sibling one
        assert html.index("tree-search-icon") < html.index('type="search"')
        assert re.search(r'tree-search-icon[^>]*>[^<]*</span>\s*<input type="search"', html)
