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
        reset-and-flash hook that made a second save visible (C1)."""
        page = client.get("/").get_data(as_text=True)
        assert 'hx-post="/state/archive"' in page
        assert 'hx-target="#archive-status"' in page
        assert "archive-flash" in page
        assert 'name="tag"' in page and 'name="note"' in page

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
        # "unrecorded", not "unsaved": the chip hashes the LIVE pair, so this
        # state is reached by an out-of-band write as readily as by an edit and
        # must not name a culprit it did not observe (review finding 4).
        assert "unrecorded" in body
        assert "unsaved" not in body

    def test_nothing_at_all_without_a_chip(self, tmp_path):
        app = create_app(testing=True, instance_path=str(tmp_path / "_i2"))
        body = app.test_client().get("/state/version").get_data(as_text=True)
        assert "state-version-chip" not in body

    def test_after_a_snapshot_it_names_that_version(self, client):
        client.post("/state/archive", data={"tag": "t", "note": "n"})
        body = client.get("/state/version").get_data(as_text=True)
        m = re.search(r'class="state-version-id"[^>]*>([^<]+)<', body)
        assert m, body
        ts = m.group(1).strip()
        assert re.match(r"^\d{8}_\d{6}", ts), ts

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
        # the version id itself is still shown -- it is still true, just no
        # longer the whole truth
        assert re.search(r'class="state-version-id"[^>]*>\d{8}_', dirty)
        assert "not in it yet" in dirty

    def test_the_tooltip_never_calls_the_live_id_the_working_state(self, client):
        client.post("/state/archive", data={"tag": "t"})
        body = client.get("/state/version").get_data(as_text=True)
        assert "The live chip is on this recorded version" in body


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
        assert "overwrite live" in body, "the button must name the act"

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
