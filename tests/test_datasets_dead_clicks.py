"""Guard the Datasets "먹통 / clicking a run does nothing" root-cause fix.

Root cause (verified against the bundled htmx 2.0.4 source): every #inspector-pane
load called htmx.ajax(...) with NO `source`, so htmx used document.body as the request
element. Consequences: (1) #inspector-pane's hx-sync="this:replace" was never applied
(it's a descendant of body, not an ancestor), and (2) ALL inspector loads shared
document.body's single request queue (config.timeout 0) — a slow/stalled prior load
made the next click QUEUE behind it (a thrown onload wedged that queue for the whole
session = the dead table), while a concurrent global-search GET clobbered the detail
(last-response-wins).

Fix: every #inspector-pane htmx.ajax call passes source:'#inspector-pane' (keys the
queue on the pane + reads its hx-sync="this:replace" → new load ABORTS the prior →
true last-click-wins, no shared body queue, no wedge), and the global search shares the
same sync owner via hx-sync="#inspector-pane:replace".

JS behaviour pytest can't execute → source-contract tripwires."""

import re
from pathlib import Path

_STATIC = Path(__file__).resolve().parent.parent / "quam_state_manager" / "web" / "static"
_TPL = Path(__file__).resolve().parent.parent / "quam_state_manager" / "web" / "templates"

# Every JS file that loads into #inspector-pane via htmx.ajax.
_JS_FILES = ["dataset-virtual.js", "app.js", "chip-status.js", "all-values.js", "pair-edit.js"]


class TestInspectorPaneLoadsAreSourced:
    def test_every_inspector_pane_ajax_has_a_source(self):
        # Every htmx.ajax targeting #inspector-pane must carry source:'#inspector-pane'
        # so it keys the request queue on the pane (not document.body). Assert the count
        # of bare targets equals the count of sourced targets in each file.
        for name in _JS_FILES:
            txt = (_STATIC / name).read_text(encoding="utf-8")
            targets = txt.count("target: '#inspector-pane'") + txt.count("target:'#inspector-pane'")
            sourced = txt.count("source: '#inspector-pane', target: '#inspector-pane'") \
                + txt.count("source:'#inspector-pane',target:'#inspector-pane'")
            assert targets > 0, f"{name}: expected #inspector-pane loads"
            assert targets == sourced, (
                f"{name}: {targets - sourced} #inspector-pane htmx.ajax call(s) MISSING "
                f"source:'#inspector-pane' — they'd fall back to the document.body queue")

    def test_global_search_shares_the_inspector_sync_owner(self):
        base = (_TPL / "base.html").read_text(encoding="utf-8")
        # The global search must sync on #inspector-pane so it serializes with the
        # dataset detail loads (last-action-wins) instead of clobbering them.
        m = re.search(r'id="global-search".*?>', base, re.S)
        assert m, "global-search input not found"
        assert 'hx-sync="#inspector-pane:replace"' in m.group(0)

    def test_openDatasetDetail_reissue_recovers_clobber_not_only_empty(self):
        # The 300ms backstop must re-fetch when the pane was clobbered by search
        # results, not only when it's empty — and it must use selectors the app
        # ACTUALLY emits: the global search renders .search-panel/.search-table and a
        # dataset detail renders #ds-detail-root (the prior #ds-search-results/.search-results
        # selectors matched nothing, so the clobber recovery was dead — audit 2026-06-26).
        dv = (_STATIC / "dataset-virtual.js").read_text(encoding="utf-8")
        assert "clobberedBySearch" in dv
        assert ".search-panel, .search-table" in dv
        assert "#ds-detail-root" in dv
        # The dead selectors must not be the ACTIVE clobber check any more (they may still
        # be named in the explanatory comment): no live querySelector uses them.
        assert "querySelector('#ds-search-results" not in dv
        assert "querySelector('#ds-detail-root')" in dv

    def test_search_help_panel_dismisses_on_table_click(self):
        # The floating help panel (z-index:30 over the rows) must close when the user
        # engages the run table, so it can't sit over the list eating clicks.
        app = (_STATIC / "app.js").read_text(encoding="utf-8")
        assert "t.closest('#datasets-scroll')) closeHelp()" in app
