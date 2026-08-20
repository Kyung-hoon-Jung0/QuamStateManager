"""The live chip's recorded version, from the top bar (docs/120 item 10).

Customer: *"Move the bookmark button below Calculator, and in its place show
the current state working version id. Since we're adding Auto-Sync, revert back
and forth has to be really free. Clicking it lists the version history with
**when each was updated**, checkboxes to pick several -> show just the combined
diff -> and let a chosen state be applied to the live chip."*

Deliberately a thin surface over machinery that already exists and is already
gated -- State History's snapshots, the docs/84 diff workbench, the Compare hub
basket, and ``/state-history/<ts>/restore-live`` with BOTH its independent force
tokens. What it adds is one click from every page instead of a navigation.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app


def _chip(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(
        {"qubits": {"q1": {"id": "q1", "f_01": 6.1e9}},
         "qubit_pairs": {}, "active_qubit_names": ["q1"]}), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(
        {"network": {"host": "1.2.3.4"}, "wiring": {"qubits": {}}}), encoding="utf-8")
    return folder


@pytest.fixture
def client(tmp_path):
    _chip(tmp_path / "quam_state")
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    c = app.test_client()
    c.post("/load", data={"folder": str(tmp_path / "quam_state")})
    return c


class TestBookmarkMoved:
    def test_bookmark_is_a_sidebar_tool_now(self, client):
        page = client.get("/").get_data(as_text=True)
        assert "sidebar-tool-details" in page
        # it sits with Settings + Calculator, below them
        i = page.index('class="sidebar-tools"')
        assert page.index('id="archive-details"') > i
        assert page.index("Calculator") < page.index('id="archive-details"')

    def test_it_kept_everything_that_made_it_work(self, client):
        """A move, not a rewrite: same route, same target, and the
        reset-and-flash hook that made a second save visible (C1).

        docs/120 item 27 moved the hook itself. It used to live in an
        ``hx-on::after-request`` attribute — which htmx compiles with
        ``new Function``, blocked by this app's own CSP, so the flash had in
        fact never run. The behaviour is now a named action in app.js, and this
        asserts BOTH halves: the element declares it, and something implements
        it. The old check ("archive-flash" appears in the page) passed happily
        while the code containing it could not execute.
        """
        from pathlib import Path
        import quam_state_manager
        page = client.get("/").get_data(as_text=True)
        assert 'hx-post="/state/archive"' in page
        assert 'hx-target="#archive-status"' in page
        assert 'data-after-request="archiveDone"' in page
        assert 'name="tag"' in page and 'name="note"' in page
        app_js = (Path(quam_state_manager.__file__).parent / "web" / "static"
                  / "app.js").read_text(encoding="utf-8")
        assert "archiveDone:" in app_js
        assert "archive-flash" in app_js

    def test_the_topbar_slot_is_the_version_now(self, client):
        page = client.get("/").get_data(as_text=True)
        assert 'class="archive-wrap"' not in page
        assert 'id="state-version-slot"' in page

    def test_the_version_is_lazy_not_render_path(self, client):
        """Resolving it hashes the live state+wiring pair; docs/28 forbids a
        live read on a surface that renders on every page. So the slot fetches
        after paint, like the diagnostics and instances slots."""
        page = client.get("/").get_data(as_text=True)
        m = re.search(r'id="state-version-slot"[^>]*', page)
        assert m and 'hx-get="/state/version"' in m.group(0)
        assert "load" in m.group(0)

    def test_the_panel_is_not_inside_the_swap_target(self, client):
        """An innerHTML swap on the slot would delete the panel every time the
        chip refreshed."""
        page = client.get("/").get_data(as_text=True)
        slot = re.search(r'<span id="state-version-slot".*?</span>', page, re.S)
        assert slot and 'id="state-version-panel"' not in slot.group(0)


class TestVersionChip:
    def test_renders_for_an_open_chip_even_with_no_history(self, client):
        """The affordance must exist before the first snapshot -- the panel's
        empty state is what explains where versions come from."""
        body = client.get("/state/version").get_data(as_text=True)
        assert "state-version-chip" in body
        # docs/126: the label is the WORD "Versions" — the raw snapshot token
        # carried nothing a user can read (the id lives in the tooltip and the
        # panel). With zero versions there is nothing to call "unrecorded";
        # the badge appears only when history exists and live matches none of
        # it. "unsaved" stays banned (review finding 4: never name a culprit).
        assert ">Versions<" in body
        assert "unrecorded" not in body
        assert "unsaved" not in body

    def test_nothing_at_all_without_a_chip(self, tmp_path):
        app = create_app(testing=True, instance_path=str(tmp_path / "_i2"))
        body = app.test_client().get("/state/version").get_data(as_text=True)
        assert "state-version-chip" not in body

    def test_after_a_snapshot_it_names_that_version(self, client):
        client.post("/state/archive", data={"tag": "t", "note": "n"})
        body = client.get("/state/version").get_data(as_text=True)
        # docs/126: the version id moved to the TOOLTIP — the button label is
        # the word, the id is detail on demand.
        m = re.search(r"The live chip is on recorded version (\d{8}_\d{6}\S*)", body)
        assert m, body
        assert ">Versions<" in body

    def test_the_id_is_content_matched_not_newest(self, client):
        """After A -> B -> A the NEWEST snapshot holds B, so "newest" would name
        the wrong one; snapshot_ts_for_current_content is the witness (the
        audit-r10 finding that helper exists for). And never mutation_seq, which
        resets on reload/eviction/restart."""
        from quam_state_manager.web import routes as R
        src = Path(R.__file__).read_text(encoding="utf-8")
        i = src.index("def _state_version_now")
        # Sliced to the next def: the function grew a stat-gated memo around
        # the hash (it read 526 KB of live files on every page load), and a
        # fixed-length slice reads that growth as "the helper is gone".
        body = src[i:src.index("@bp.route", i)]
        assert "snapshot_ts_for_current_content" in body
        assert "mutation_seq" not in body


class TestVersionsPanel:
    def test_empty_state_is_honest(self, client):
        body = client.get("/state/versions").get_data(as_text=True)
        assert "state-versions-empty" in body
        assert "No recorded versions" in body

    def test_rows_say_when_and_what_produced_them(self, client):
        client.post("/state/archive", data={"tag": "before-cz", "note": "hi"})
        body = client.get("/state/versions").get_data(as_text=True)
        assert 'class="sv-check"' in body        # the checkbox the user asked for
        assert "sv-time" in body                 # WHEN it was updated
        assert "sv-trigger" in body              # what produced it
        assert "before-cz" in body               # its label

    def test_the_current_version_is_marked(self, client):
        client.post("/state/archive", data={"tag": "t"})
        body = client.get("/state/versions").get_data(as_text=True)
        assert "sv-current" in body and "on this now" in body

    def test_go_back_uses_the_gated_restore_route(self, client, tmp_path):
        """Not a new write path: the same restore-live State History uses, whose
        unsaved-edits and wiring-topology gates are independent and still ask.

        Two versions are needed for the button to appear at all, and they have
        to differ in LIVE content -- /save writes the working copy, so editing
        and saving leaves the live pair (and therefore the current version)
        exactly where it was.
        """
        client.post("/state/archive", data={"tag": "first"})
        live = tmp_path / "quam_state" / "state.json"
        doc = json.loads(live.read_text(encoding="utf-8"))
        doc["qubits"]["q1"]["f_01"] = 6.3e9
        live.write_text(json.dumps(doc), encoding="utf-8")
        client.post("/state/archive", data={"tag": "second"})

        body = client.get("/state/versions").get_data(as_text=True)
        assert body.count('class="sv-check"') == 2, body
        # the older one is no longer current, so going back to it is offered
        assert "/restore-live" in body
        # ...and exactly once: the current version has nowhere to go back to
        assert body.count("/restore-live") == 1

    def test_an_archive_offers_no_go_back(self, client):
        """An archive is read-only; restore-live 409s there anyway, so the
        button is not offered rather than offered-and-refused."""
        from quam_state_manager.web import routes as R
        client.post("/state/archive", data={"tag": "t"})
        with client.application.test_request_context():
            pass
        ctx = (client.application.config.get("contexts") or {}).get(
            client.application.config.get("active_context"))
        ctx["origin"] = "dataset_archive"
        body = client.get("/state/versions").get_data(as_text=True)
        assert "/restore-live" not in body

    def test_it_never_500s_without_a_chip(self, tmp_path):
        app = create_app(testing=True, instance_path=str(tmp_path / "_i3"))
        r = app.test_client().get("/state/versions")
        assert r.status_code == 200


class TestReviewFindings:
    """The heavy review's findings on this surface, each pinned.

    Both are honesty defects rather than crashes, which is exactly why they
    needed pins: nothing failed, the surface simply said something that was not
    so.
    """

    def test_the_list_is_paged_and_says_so(self, client):
        """A real chip has 433 versions; the first page shows 40 and used to
        end there with no footer, so the list silently claimed to BE the
        history."""
        for i in range(45):
            client.post("/state/archive", data={"tag": f"v{i}"})
        body = client.get("/state/versions").get_data(as_text=True)
        n = body.count('class="sv-check"')
        assert n == 40, n
        assert "state-versions-more" in body
        assert "Show 40 more" in body
        assert "StateVersions.more(" in body

    def test_show_more_reaches_the_rest(self, client):
        for i in range(45):
            client.post("/state/archive", data={"tag": f"v{i}"})
        body = client.get("/state/versions?limit=200").get_data(as_text=True)
        assert body.count('class="sv-check"') > 40
        # nothing left to page to -> no footer claiming there is
        assert "state-versions-more" not in body

    def test_the_page_size_is_bounded_however_it_is_asked(self, client):
        """`limit` is user input on a route that renders every row it is
        given."""
        from quam_state_manager.web.routes import _STATE_VERSIONS_CAP
        client.post("/state/archive", data={"tag": "t"})
        r = client.get(f"/state/versions?limit={_STATE_VERSIONS_CAP * 10}")
        assert r.status_code == 200
        assert r.get_data(as_text=True).count('class="sv-check"') <= _STATE_VERSIONS_CAP

    def test_the_chip_marks_edits_that_are_not_in_the_named_version(self, client):
        """The id names the LIVE chip. Unapplied edits mean SM is holding
        something else, and a bare id would read as "your work is recorded as
        this"."""
        client.post("/state/archive", data={"tag": "t"})
        clean = client.get("/state/version").get_data(as_text=True)
        assert "state-version-dirty" not in clean
        assert "state-version-edits" not in clean

        client.post("/field/edit", data={"dot_path": "qubits.q1.f_01", "value": "6.2e9"})
        dirty = client.get("/state/version").get_data(as_text=True)
        assert "state-version-dirty" in dirty
        assert "state-version-edits" in dirty
        # the version id itself is still shown (in the tooltip since docs/126)
        # -- it is still true, just no longer the whole truth
        assert re.search(r"recorded version \d{8}_", dirty)
        assert "not in it yet" in dirty

    def test_the_tooltip_never_calls_the_live_id_the_working_state(self, client):
        client.post("/state/archive", data={"tag": "t"})
        body = client.get("/state/version").get_data(as_text=True)
        assert "The live chip is on recorded version" in body


class TestTheAuditsHonestyFindings:
    """Four surfaces stated something that was not so. Each is pinned here
    because nothing failed when they were wrong — the code worked, the words
    were false, and only a person reading them found out."""

    def test_the_empty_state_does_not_promise_a_save_records_a_version(self, client):
        """It said "captured on every save and apply". Measured by the audit:
        46 save cycles produced 0 versions — /save's own comment says history
        is snapshotted on apply, not there."""
        body = client.get("/state/versions").get_data(as_text=True)
        assert "applied to live" in body
        assert "on every save" not in body

    def test_going_back_wears_the_overwrite_live_language(self, client):
        """It writes the LIVE chip in one click from any page, and shipped as a
        neutral `outline` button — the most consequential affordance in the
        panel rendered as the least distinguishable. docs/86/97 established one
        visual language for writing over live; this reuses it rather than
        inventing a second."""
        client.post("/state/archive", data={"tag": "a"})
        client.post("/field/edit", data={"dot_path": "qubits.q1.f_01", "value": "6.2e9"})
        client.post("/state/archive", data={"tag": "b"})
        body = client.get("/state/versions").get_data(as_text=True)
        assert "restore-live" in body
        assert "live-diverged-overwrite" in body, "not the shared overwrite style"
        # Label concise per the user (2026-08-20, "Pull to Live"); the ACT —
        # overwriting the live chip — stays named in the button's title.
        assert "Pull to Live" in body
        assert "Overwrite the LIVE chip" in body, "the title must name the act"

    def test_the_gates_are_untouched_by_the_restyle(self, client):
        """Style and wording only — the two independent force tokens still
        gate the route this button posts to."""
        from quam_state_manager.web import routes as R
        src = Path(R.__file__).read_text(encoding="utf-8")
        i = src.index("def state_history_restore_live")
        body = src[i:i + 6000]
        assert "force_pending" in body and "force_align" in body


class TestTheManualStatesTheCurrentCovenant:
    """The covenant was amended twice (docs/117 push, docs/120 pull) and the
    in-app manual still stated the original, unconditionally. README was
    updated; the two screens a newcomer actually reads were not."""

    def test_help_names_auto_sync(self, client):
        body = client.get("/help").get_data(as_text=True)
        assert "Auto-Sync" in body
        assert "is only ever written by an explicit" not in body

    def test_the_landing_glossary_names_it_too(self, client):
        body = client.get("/").get_data(as_text=True)
        assert "Auto-Sync" in body
        assert "edits here never touch the instrument" not in body


class TestPanelLayout:
    """docs/126 r3 bug 2: the version rows rendered HORIZONTALLY with a
    scrollbar, and a chip far enough right pushed the panel past the viewport
    edge. Cause #1: the panel lives inside the topbar <nav>, and Pico's
    `nav ul { display:flex }` reaches nested lists — the list must declare
    display:block itself. Cause #2: CSS-anchored left:0 with a 46rem width —
    StateVersions clamps the open panel back into the viewport."""

    def _static(self, name):
        from pathlib import Path
        import quam_state_manager
        return (Path(quam_state_manager.__file__).parent / "web" / "static"
                / name).read_text(encoding="utf-8")

    def test_the_list_defeats_picos_nav_flex(self):
        css = self._static("style.css")
        i = css.index(".state-versions-list")
        block = css[i:css.index("}", i)]
        assert "display: block" in block

    def test_the_open_panel_is_clamped_to_the_viewport(self):
        js = self._static("app.js")
        i = js.index("window.StateVersions")
        block = js[i:i + 5000]
        assert "_clampToViewport" in block
        assert "innerWidth" in block


class TestQuickDiff:
    """docs/126 round 3: the panel leads with the before -> after table against
    the PREVIOUS version, immediately -- the tick-two-then-Compare dance stays
    for arbitrary pairs, but the question users actually bring ("what just
    changed?") is answered without any picking. Rendered only when it fits
    (<= 50 rows); a bigger drift states its count and defers to Compare."""

    def test_two_versions_show_the_diff_without_any_picking(self, client):
        client.post("/state/archive", data={"tag": "a"})
        client.post("/field/edit", data={"dot_path": "qubits.q1.f_01", "value": "6.2e9"})
        client.post("/state/apply-to-live")
        body = client.get("/state/versions").get_data(as_text=True)
        assert "sv-quick" in body
        assert "qubits.q1.f_01" in body
        # old AND new, through the one docs/76 delta implementation
        assert "sv-q-old" in body and "val-delta" in body

    def test_a_single_version_renders_no_quick_block(self, client):
        """One row has no "previous" -- inventing a comparison against nothing
        would be a lie, so the block simply is not there."""
        client.post("/state/archive", data={"tag": "only"})
        body = client.get("/state/versions").get_data(as_text=True)
        assert "sv-quick" not in body

    def test_the_realtime_refresh_is_wired_and_load_order_safe(self):
        """While an Auto-Sync session is armed, flushes keep landing and the
        open panel follows them (the user's ask). The listener must sit on
        `document`, never `document.body`: app.js executes from <head> where
        body is still null, and the throw took the WHOLE IIFE down --
        StateVersions.toggle() itself stopped existing. Found in a real
        browser; jsdom evals app.js with a body present and cannot see it."""
        from pathlib import Path
        import quam_state_manager
        src = (Path(quam_state_manager.__file__).parent / "web" / "static"
               / "app.js").read_text(encoding="utf-8")
        i = src.index("window.StateVersions")
        block = src[i:i + 4000]
        assert "document.addEventListener('htmx:afterSwap'" in block
        assert "document.body.addEventListener" not in block
        assert "auto-apply-on" in block        # armed sessions only
        assert "'/state/versions'" in block    # what it refetches


def _app_js_stateversions_block() -> str:
    import quam_state_manager
    src = (Path(quam_state_manager.__file__).parent / "web" / "static"
           / "app.js").read_text(encoding="utf-8")
    i = src.index("window.StateVersions")
    return src[i:i + 14000]


class TestVersionDiff:
    """Customer (2026-08-21): the list showed WHEN each version landed, but
    WHAT it holds against now cost tick-two + Compare — a navigation. Each
    row now carries a read-only Diff button opening this-version-vs-NOW in
    the same overlay shell and Δ language the sync review modal uses
    (docs/76/86), fed by the diff_current pipeline that already existed —
    nothing on this path writes; Pull to Live stays the one gated write."""

    def test_every_row_offers_diff_left_of_pull_to_live(self, client, tmp_path):
        client.post("/state/archive", data={"tag": "first"})
        live = tmp_path / "quam_state" / "state.json"
        doc = json.loads(live.read_text(encoding="utf-8"))
        doc["qubits"]["q1"]["f_01"] = 6.3e9
        live.write_text(json.dumps(doc), encoding="utf-8")
        client.post("/state/archive", data={"tag": "second"})
        body = client.get("/state/versions").get_data(as_text=True)
        rows = body.split('<li class="state-version-row')[1:]
        assert len(rows) == 2
        # every row, current included — comparing is always safe
        assert all("sv-diff" in r for r in rows)
        # and on the row that also offers the write, the read comes first
        restore_rows = [r for r in rows if "sv-restore" in r]
        assert restore_rows and all(
            r.index("sv-diff") < r.index("sv-restore") for r in restore_rows)

    def test_an_archive_still_offers_diff(self, client):
        """Read-only compare is offered exactly where restore-live is not."""
        client.post("/state/archive", data={"tag": "t"})
        ctx = (client.application.config.get("contexts") or {}).get(
            client.application.config.get("active_context"))
        ctx["origin"] = "dataset_archive"
        body = client.get("/state/versions").get_data(as_text=True)
        assert "sv-diff" in body
        assert "/restore-live" not in body

    def test_the_endpoint_shows_version_then_now_in_the_review_language(
            self, client):
        client.post("/state/archive", data={"tag": "a"})
        body = client.get("/state/versions").get_data(as_text=True)
        ts = re.search(r'sv-check" value="(\d{8}_\d{6}\S*?)"', body).group(1)
        client.post("/field/edit",
                    data={"dot_path": "qubits.q1.f_01", "value": "6.2e9"})
        diff = client.get(f"/state/versions/{ts}/diff").get_data(as_text=True)
        assert "review-row" in diff            # the review modal's row language
        assert "qubits.q1.f_01" in diff
        # forward in time: the version's value, THEN now's (docs/76)
        assert diff.index("6,100,000,000.0") < diff.index("6,200,000,000.0")
        assert "val-delta" in diff             # the one Δ implementation
        # HONESTY: "now" is the working state, which holds an unapplied edit
        assert "not yet applied" in diff
        # read-only: none of the sync modal's write actions ride along
        assert "doStateSync" not in diff
        assert "/state/apply-to-live" not in diff

    def test_no_differences_is_stated_plainly(self, client):
        client.post("/state/archive", data={"tag": "t"})
        body = client.get("/state/versions").get_data(as_text=True)
        ts = re.search(r'sv-check" value="(\d{8}_\d{6}\S*?)"', body).group(1)
        diff = client.get(f"/state/versions/{ts}/diff").get_data(as_text=True)
        assert "No differences" in diff
        assert "review-row" not in diff

    def test_a_malformed_ts_is_refused_before_the_path_join(self, client):
        """The ts lands in a path join; a ``..\\..``-shaped segment escapes
        the history root on Windows. Same shape gate as load_snapshot."""
        r = client.get("/state/versions/hello/diff")
        assert r.status_code == 404
        assert "Not a snapshot id" in r.get_data(as_text=True)
        r = client.get("/state/versions/..\\..\\x/diff")
        assert r.status_code in (404, 308)     # rejected or not even routed
        if r.status_code == 404:
            assert "Not a snapshot id" in r.get_data(as_text=True)

    def test_it_never_500s_without_a_chip(self, tmp_path):
        app = create_app(testing=True, instance_path=str(tmp_path / "_i4"))
        r = app.test_client().get("/state/versions/20250101_000000/diff")
        assert r.status_code == 200
        assert "No state loaded" in r.get_data(as_text=True)

    def test_the_full_view_link_uses_the_workbench_grammar(self, client):
        """The escape hatch is the docs/84 workbench with the SAME
        hist:/working: ref tokens the tick-two flow uses — no third grammar."""
        client.post("/state/archive", data={"tag": "t"})
        body = client.get("/state/versions").get_data(as_text=True)
        ts = re.search(r'sv-check" value="(\d{8}_\d{6}\S*?)"', body).group(1)
        diff = client.get(f"/state/versions/{ts}/diff").get_data(as_text=True)
        assert "/diff?a=hist%3A" in diff
        assert "working%3A" in diff
        assert 'hx-target="#table-pane"' in diff

    def test_the_cap_and_the_gate_are_in_the_source(self):
        """Honesty pins: entries are capped with a visible count, the ts is
        shape-gated pre-join, and the diff runs against the in-memory store
        (never the live files, docs/28)."""
        from quam_state_manager.web import routes as R
        src = Path(R.__file__).read_text(encoding="utf-8")
        i = src.index("def state_version_diff")
        block = src[i:src.index("@bp.route", i)]
        assert "[:300]" in block
        assert "_HIST_TS_RE" in block
        assert "diff_current" in block and "current_store" in block
        import quam_state_manager
        tpl = (Path(quam_state_manager.__file__).parent / "web" / "templates"
               / "_version_diff.html").read_text(encoding="utf-8")
        assert "showing" in tpl

    def test_the_overlay_shell_and_js_are_wired(self, client):
        """The overlay reuses the review shell classes (docs/86 language);
        the JS opens it with a focus trap and the panel's click-away guard
        ignores clicks inside it — closing the diff lands back on the row."""
        page = client.get("/").get_data(as_text=True)
        m = re.search(r'<div id="version-diff-overlay"[^>]*>', page)
        assert m and "state-review-overlay" in m.group(0)
        assert 'id="version-diff-host"' in page
        block = _app_js_stateversions_block()
        assert "closeDiff" in block
        assert "encodeURIComponent(ts) + '/diff'" in block
        assert "trapFocus" in block
        # the away guard names OUR overlay only — the sync-review/live-drift
        # overlays keep their pre-docs/128 dismiss behavior
        assert "closest('#version-diff-overlay')" in block
        assert "closest('.state-review-overlay')" not in block
        # stale-response token (the docs/122 class): a slow cold-snapshot
        # diff must never repaint over a newer row's or a closed overlay's
        # content
        assert "_diffGen" in block
        assert "diff: diff" in block and "closeDiff: closeDiff" in block

    def test_global_shortcuts_are_gated_while_the_diff_is_open(self):
        """window.smModalOpen is the app-wide 'is a modal up' oracle
        (visibility-tested by id); without the new overlay in its selector,
        j/k/Enter run-navigation and the '?' cheat sheet keep firing BEHIND
        the modal — Enter would swap #table-pane underneath it."""
        import quam_state_manager
        src = (Path(quam_state_manager.__file__).parent / "web" / "static"
               / "app.js").read_text(encoding="utf-8")
        i = src.index("window.smModalOpen = function")
        block = src[i:i + 1200]
        assert "#version-diff-overlay" in block

    def test_a_press_racing_a_chip_switch_is_refused_honestly(self, client):
        """Two SM windows share one server context (docs/120): the button
        ships its chip_key and a mismatch is refused — never answered from
        the wrong chip's history, never an internals-leaking 'Diff failed'."""
        client.post("/state/archive", data={"tag": "t"})
        body = client.get("/state/versions").get_data(as_text=True)
        ts = re.search(r'sv-check" value="(\d{8}_\d{6}\S*?)"', body).group(1)
        # the panel's button forwards the key
        assert f"StateVersions.diff('{ts}', '" in body
        b = client.get(
            f"/state/versions/{ts}/diff?chip_key=__someone_elses_chip__"
        ).get_data(as_text=True)
        assert "open that chip first" in b
        assert "review-row" not in b

    def test_the_pull_note_appears_only_where_the_button_does(
            self, client, tmp_path):
        """The overlay must never instruct a press the panel deliberately
        withholds: the ↑ Pull to Live sentence is gated off on the current
        version's row and on archives (where the button does not render)."""
        live = tmp_path / "quam_state" / "state.json"
        client.post("/state/archive", data={"tag": "v0"})
        for f in (6.3e9, 6.5e9):
            doc = json.loads(live.read_text(encoding="utf-8"))
            doc["qubits"]["q1"]["f_01"] = f
            live.write_text(json.dumps(doc), encoding="utf-8")
            client.post("/state/archive", data={"tag": f"v{f}"})
        body = client.get("/state/versions").get_data(as_text=True)
        ts = sorted(re.findall(r'sv-check" value="(\d{8}_\d{6}\S*?)"', body))
        mid, cur = ts[1], ts[2]     # mid: differs from working, not current
        d_mid = client.get(f"/state/versions/{mid}/diff").get_data(as_text=True)
        assert "review-row" in d_mid           # a real, non-empty diff
        assert "Pull to Live" in d_mid         # the row DOES offer the button
        d_cur = client.get(f"/state/versions/{cur}/diff").get_data(as_text=True)
        assert "review-row" in d_cur           # non-empty too (stale working)
        assert "Pull to Live" not in d_cur     # but this row has no button
        ctx = (client.application.config.get("contexts") or {}).get(
            client.application.config.get("active_context"))
        ctx["origin"] = "dataset_archive"
        d_arch = client.get(f"/state/versions/{mid}/diff").get_data(as_text=True)
        assert "Pull to Live" not in d_arch    # archives offer it nowhere


class TestVersionsCompareNway:
    """Customer (2026-08-21): pressing Compare on N picks must IMMEDIATELY
    list only the keys whose values differ — one column per version — not
    land on the Compare hub's configuration surface, and never spend rows on
    values that agree. Comparison is meaningful in the differences."""

    def _three_versions(self, client, tmp_path):
        live = tmp_path / "quam_state" / "state.json"
        client.post("/state/archive", data={"tag": "v0"})
        for f in (6.3e9, 6.5e9):
            doc = json.loads(live.read_text(encoding="utf-8"))
            doc["qubits"]["q1"]["f_01"] = f
            live.write_text(json.dumps(doc), encoding="utf-8")
            client.post("/state/archive", data={"tag": f"v{f}"})
        body = client.get("/state/versions").get_data(as_text=True)
        return re.findall(r'sv-check" value="(\d{8}_\d{6}\S*?)"', body)

    def test_three_picks_list_only_what_differs_as_columns(
            self, client, tmp_path):
        ts = self._three_versions(client, tmp_path)
        assert len(ts) == 3
        q = "&".join(f"ts={t}" for t in ts)
        body = client.get(f"/diff/versions?{q}").get_data(as_text=True)
        assert "vc-table" in body
        assert "qubits.q1.f_01" in body
        # columns oldest → newest, forward in time (docs/76)
        i0 = body.index("6,100,000,000.0")
        i1 = body.index("6,300,000,000.0")
        i2 = body.index("6,500,000,000.0")
        assert i0 < i1 < i2
        # DIFFERENCES ONLY: leaves that agree across all three never render
        assert "qubits.q1.id" not in body
        assert "network.host" not in body
        # the moved cells are marked by diff_n's own verdict (docs/118: one
        # rule for the row and the cell), with the docs/76 Δ chip
        assert "vc-changed" in body
        assert "val-delta" in body

    def test_the_hub_stays_reachable_but_out_of_the_way(
            self, client, tmp_path):
        ts = self._three_versions(client, tmp_path)
        q = "&".join(f"ts={t}" for t in ts)
        body = client.get(f"/diff/versions?{q}").get_data(as_text=True)
        assert "/compare-hub?" in body         # the advanced link
        assert body.count("src=hist") >= 3 or "src=hist%3A" in body

    def test_fewer_than_two_valid_picks_redirect_to_the_workbench(
            self, client, tmp_path):
        ts = self._three_versions(client, tmp_path)
        assert client.get(f"/diff/versions?ts={ts[0]}").status_code == 302
        # malformed ts values are shape-gated out BEFORE any path join
        assert client.get("/diff/versions?ts=hello&ts=..\\x").status_code == 302

    def test_a_missing_snapshot_explains_instead_of_500ing(
            self, client, tmp_path):
        ts = self._three_versions(client, tmp_path)
        r = client.get(f"/diff/versions?ts={ts[0]}&ts=20990101_000000")
        assert r.status_code == 200
        assert "Compare failed" in r.get_data(as_text=True)

    def test_a_link_naming_another_chip_is_refused_honestly(
            self, client, tmp_path):
        """A shared URL names its chip; answering from a DIFFERENT open
        chip's history would be silently wrong."""
        ts = self._three_versions(client, tmp_path)
        q = "&".join(f"ts={t}" for t in ts)
        body = client.get(
            f"/diff/versions?{q}&chip_key=__someone_elses_chip__"
        ).get_data(as_text=True)
        assert "open that chip first" in body
        assert "vc-table" not in body

    def test_errors_arrive_with_page_chrome_on_a_full_page_get(
            self, client, tmp_path):
        """Compare pushes this URL into browser history, so F5/bookmark/
        shared-link reloads are full-page GETs — an error must arrive WITH
        the chrome, never as a bare toast fragment that strands the user
        with only browser Back."""
        ts = self._three_versions(client, tmp_path)
        full = client.get(
            f"/diff/versions?ts={ts[0]}&ts=20990101_000000"
        ).get_data(as_text=True)
        assert "Compare failed" in full
        assert "<html" in full                 # the page shell came along
        part = client.get(
            f"/diff/versions?ts={ts[0]}&ts=20990101_000000",
            headers={"HX-Request": "true"}).get_data(as_text=True)
        assert "Compare failed" in part
        assert "<html" not in part             # the swap gets the partial

    def test_a_stored_null_is_named_not_blank(self, client, tmp_path):
        """groupdigits(None) is '' — a stored null used to render as a fully
        blank cell, hiding the very difference the row exists to show and
        indistinguishable from an empty string. It now says `null`,
        distinct from the en-dash of an ABSENT leaf."""
        live = tmp_path / "quam_state" / "state.json"
        client.post("/state/archive", data={"tag": "v0"})
        doc = json.loads(live.read_text(encoding="utf-8"))
        doc["qubits"]["q1"]["f_01"] = None
        live.write_text(json.dumps(doc), encoding="utf-8")
        client.post("/state/archive", data={"tag": "v1"})
        body = client.get("/state/versions").get_data(as_text=True)
        q = "&".join(
            f"ts={t}" for t in
            re.findall(r'sv-check" value="(\d{8}_\d{6}\S*?)"', body))
        page = client.get(f"/diff/versions?{q}").get_data(as_text=True)
        assert "qubits.q1.f_01" in page
        assert "vc-null" in page and ">null<" in page

    def test_the_compare_button_lands_here_not_on_the_hub(self):
        block = _app_js_stateversions_block()
        assert "'/diff/versions?'" in block
        assert "'/compare-hub?'" not in block
        # the hint no longer promises the hub
        assert "Lists what differs across all" in block

    def test_diff_n_is_every_leaf_and_one_equality_rule(self):
        """The engine walks EVERY leaf (state + wiring — multi_diff only
        walks curated qubit properties) and its per-cell `changed` verdict
        uses the same equality as the row verdict (docs/118)."""
        from quam_state_manager.core.differ import Differ
        a = ({"qubits": {"q1": {"f_01": 6.1e9, "id": "q1"}}},
             {"network": {"host": "h"}})
        b = ({"qubits": {"q1": {"f_01": 6.3e9, "id": "q1"}}},
             {"network": {"host": "h"}})
        c = ({"qubits": {"q1": {"f_01": 6.3e9, "id": "q1", "extra": 1}}},
             {"network": {"host": "h"}})
        rows = Differ().diff_n([a, b, c])
        by_path = {r["dot_path"]: r for r in rows}
        assert set(by_path) == {"qubits.q1.f_01", "qubits.q1.extra"}
        f = by_path["qubits.q1.f_01"]
        assert f["values"] == [6.1e9, 6.3e9, 6.3e9]
        assert f["changed"] == [False, True, False]
        e = by_path["qubits.q1.extra"]
        assert e["present"] == [False, False, True]
        assert e["changed"] == [False, False, True]
        # agreeing leaves never make a row — comparison lives in differences
        assert "qubits.q1.id" not in by_path
        assert "network.host" not in by_path


class TestCompactRows:
    """Customer (2026-08-21): the gap between the date column and the row
    actions was most of the panel — 46rem of width for ~26rem of content.
    The panel is narrower and the actions are a tight cluster, so a row
    reads as one line instead of two far-apart columns."""

    def _css(self):
        import quam_state_manager
        return (Path(quam_state_manager.__file__).parent / "web" / "static"
                / "style.css").read_text(encoding="utf-8")

    def test_the_panel_is_narrower(self):
        css = self._css()
        i = css.index(".state-version-panel {")
        block = css[i:css.index("}", i)]
        assert "36rem" in block
        assert "46rem" not in block

    def test_the_actions_are_a_tight_cluster(self):
        css = self._css()
        i = css.index(".sv-row-actions")
        block = css[i:css.index("}", i)]
        assert "display: flex" in block and "gap" in block
