"""docs/166 — the Pulses page's chip map is its row filter.

Customer: "지금 pulses 메뉴를 열면 너무 많은 pulse 리스트가 있다 ... chip
component처럼 살짝 축소된 크기로, qubit과 pair를 모두 표시해주는 diagram을 그리고,
마우스로 클릭하면 그 qubit 혹은 pair의 pulse list만 나오게끔."

The drawing is the SAME one the component pages use — there is no second chip
layout to keep in step — mounted with three additive knobs (a smaller cell, the
pick flag, its own collapse memory) that every other caller leaves unset.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_STATIC = _ROOT / "quam_state_manager" / "web" / "static"
_TPL = _ROOT / "quam_state_manager" / "web" / "templates"


def _css() -> str:
    return (_STATIC / "style.css").read_text(encoding="utf-8")


def _routes() -> str:
    return (_ROOT / "quam_state_manager" / "web" / "routes.py").read_text(encoding="utf-8")


class TestOwnerFilter:
    """The pick is an entity id, so it must match exactly."""

    def test_a_pick_is_exact_and_q1_never_means_q10(self):
        from quam_state_manager.web.routes import _pulse_rows_filter
        rows = [{"owner": "q1", "owner_kind": "qubit", "channel": "xy"},
                {"owner": "q10", "owner_kind": "qubit", "channel": "xy"},
                {"owner": "q1-2", "owner_kind": "pair", "channel": "cz"},
                {"owner": "q11", "owner_kind": "qubit", "channel": "xy"}]
        assert [r["owner"] for r in _pulse_rows_filter(rows, "", "", "q1")] == ["q1"]
        assert [r["owner"] for r in _pulse_rows_filter(rows, "", "", "q1-2")] == ["q1-2"]
        # no pick = every row, byte-identical to the pre-166 call
        assert len(_pulse_rows_filter(rows, "", "", "")) == 4
        assert len(_pulse_rows_filter(rows, "", "")) == 4

    def test_the_pick_composes_with_the_search_box_rather_than_replacing_it(self):
        from quam_state_manager.web.routes import _pulse_rows_filter
        rows = [{"owner": "q1", "op_name": "x180", "owner_kind": "qubit", "channel": "xy"},
                {"owner": "q1", "op_name": "saturation", "owner_kind": "qubit", "channel": "xy"},
                {"owner": "q2", "op_name": "x180", "owner_kind": "qubit", "channel": "xy"}]
        got = _pulse_rows_filter(rows, "", "x180", "q1")
        assert [(r["owner"], r["op_name"]) for r in got] == [("q1", "x180")]


class TestTheRowPatchHonoursThePick:
    """One truth for the page and for /pulse/row (docs/141 4l-review): a
    repainted row that no longer belongs to the picked entity must leave."""

    def test_the_client_sends_the_pick_with_every_row_patch(self):
        app = (_STATIC / "app.js").read_text(encoding="utf-8")
        i = app.index('document.addEventListener("pulses-rows-changed"')
        block = app[i:i + 2400]
        assert '"&owner=" + encodeURIComponent(filt.owner)' in block, \
            "a patch that dropped the pick would restore a row the filter excluded"
        assert "owner: _pulsesOwnerPick()" in app, \
            "the filter object reads the ONE hidden input"

    def test_every_pulses_request_inherits_the_pick_from_one_place(self):
        app = (_STATIC / "app.js").read_text(encoding="utf-8")
        i = app.index('var isPulsesReq = el.id === "pulses-rows-wrap"')
        block = app[i:i + 1800]
        assert '_setQueryParam(path, "owner", _pulsesOwnerPick())' in block, \
            "the tabs, search, pagination and mutation refresh all pass here"
        assert 'delete evt.detail.parameters["owner"]' in block, \
            "htmx appends serialized parameters to the baked query string (docs/141)"


class TestTheMount:
    def test_the_page_mounts_the_shared_map_as_a_pickable_control(self):
        tpl = (_TPL / "_pulses.html").read_text(encoding="utf-8")
        assert "{% include '_component_map.html' %}" in tpl, \
            "the SAME drawing the component pages use, not a second layout"
        assert 'id="pulses-owner-pick"' in tpl

    def test_the_map_knobs_are_additive_so_every_other_page_is_unchanged(self):
        cm = (_TPL / "_component_map.html").read_text(encoding="utf-8")
        for knob, dflt in (("cmap_cell", "120"), ("cmap_pick", "''"),
                           ("cmap_open_key", "''")):
            pat = r"\{\{\s*" + knob + r"\s*\|\s*default\(" + re.escape(dflt) + r"\)\s*\}\}"
            assert re.search(pat, cm), f"{knob} must default to the pre-166 value"

    def test_the_pulses_map_is_compact_and_says_what_it_does(self):
        routes = _routes()
        m = re.search(r"cmap_cell=(\d+),", routes)
        assert m and int(m.group(1)) < 120, \
            "the customer asked for a reduced size — this map is a control, not a subject"
        assert 'cmap_label="click a qubit or a pair to show only its pulses"' in routes

    def test_this_map_remembers_its_own_collapse_choice(self):
        """One key for two roles would mean folding the filter away also folds
        the component pages' drawing away."""
        js = (_STATIC / "component-map.js").read_text(encoding="utf-8")
        assert 'root.getAttribute("data-open-key")' in js
        assert "localStorage.getItem(openKey(root))" in js
        assert "localStorage.setItem(openKey(root)" in js
        assert 'cmap_open_key="quam_pulses_map_open"' in _routes()

    def test_the_page_gets_the_code_that_draws_the_map(self):
        base = (_TPL / "base.html").read_text(encoding="utf-8")
        pb = base[base.index("set page_bundles"):]
        m = re.search(r"'pulses': \[([^\]]*)\],", pb)
        assert m and "components" in m.group(1), \
            "without the components bundle the mount is a silent no-op"


class TestTheDrawingIsAControl:
    def test_a_pair_edge_has_a_real_hit_target(self):
        """A 2px line is not something a mouse can reasonably hit."""
        tg = (_STATIC / "topo-graph.js").read_text(encoding="utf-8")
        assert '<line class="cm-edge-hit"' in tg
        css = _css()
        assert re.search(r"\.cm-edge-hit \{[^}]*pointer-events: none", css), \
            "inert by default, so no other map's behaviour changes"
        assert re.search(
            r'\.cmap\[data-cm-pick="1"\] \.cm-edge-hit \{[^}]*pointer-events: stroke', css)

    def test_the_decorations_are_hidden_only_on_the_pick_mount(self):
        css = _css()
        for cls in ("cm-res", "cm-flux", "cm-arrow", "cm-coupler", "cm-role",
                    "cm-freq", "cm-qdac", "cm-note"):
            assert re.search(r'\.cmap\[data-cm-pick="1"\] \.' + cls + r"[,\s]", css), \
                f"{cls} must be hidden on the control map"
            assert not re.search(r"^\." + cls + r" \{[^}]*display: none", css, re.M), \
                f"{cls} must still render on the component pages"

    def test_a_stone_is_opaque_so_an_edge_does_not_print_through_it(self):
        m = re.search(r'\.cmap\[data-cm-pick="1"\] \.cm-stone \{([^}]*)\}', _css())
        assert m, "pick-mode stone rule"
        assert "var(--pico-background-color)" in m.group(1), \
            "an edge runs centre to centre; a see-through stone shows it under the id"

    def test_the_edges_are_visible_because_they_are_the_pair_targets(self):
        """The shared style paints an edge in --pico-muted-border-color, which
        on the dark ground measured as the ground itself."""
        m = re.search(r'\.cmap\[data-cm-pick="1"\] \.cm-edge-line \{([^}]*)\}', _css())
        assert m and "var(--pico-primary)" in m.group(1)

    def test_a_pick_mount_owns_its_own_click(self):
        """The shared map opens the entity in the inspector on click. Here the
        click already means "show only its pulses", and doing both took the
        pane the table lives in — measured: the table dropped to ~220px with
        the inspector filling the rest, so the click a person made to narrow a
        list buried it instead."""
        js = (_STATIC / "component-map.js").read_text(encoding="utf-8")
        i = js.index('body.addEventListener("click"')
        assert "if (_isPick()) return;" in js[i:i + 400], \
            "a pick mount's click belongs to the page that mounted it"
        assert 'data-cm-pick") === "1"' in js, "_isPick reads the mount, not a page token"
        assert '_isPick() ? "click to show only its pulses"' in js, \
            "the hover card must not promise an inspector it will not open"

    def test_the_pick_is_visible_and_clearable_without_any_javascript(self):
        """A page opened at ?owner=q1 — a reload, a bookmark, a shared link —
        shows 16 rows out of 535. It must say why before the map's code
        arrives, and the way out must not depend on it either."""
        tpl = (_TPL / "_pulses.html").read_text(encoding="utf-8")
        assert "{% if not active_owner %} hidden{% endif %}" in tpl, \
            "the SERVER decides whether the pick is shown"
        assert '<strong id="pulses-owner-chip-id">{{ active_owner' in tpl, \
            "and names it, rather than waiting for a script to fill it in"
        assert 'href="/pulses' in tpl and "window.pulsePickOwner" in tpl, \
            "the x is a real link; the click handler is the fast path, not the only one"

    def test_the_picked_entity_is_visibly_picked(self):
        """The stone already wears a primary ring at rest, so a ring going
        2.2px -> 3px is not a state change anyone can see. It fills."""
        css = _css()
        m = re.search(r'\.cmap\[data-cm-pick="1"\] \.cm-picked \.cm-stone \{([^}]*)\}', css)
        assert m and "fill: var(--pico-primary)" in m.group(1)
        assert re.search(
            r'\.cmap\[data-cm-pick="1"\] \.cm-picked \.cm-id \{[^}]*var\(--pico-primary-inverse\)',
            css), "a filled stone needs its id in the inverse colour"
        assert re.search(
            r'\.cmap\[data-cm-pick="1"\] \.cm-picked \.cm-edge-line \{[^}]*stroke-width: 5px', css),             "a picked PAIR is an edge, not a stone"
