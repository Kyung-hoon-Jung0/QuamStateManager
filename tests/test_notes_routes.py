"""The notes surfaces: the panel, the grid markers, the inspector strip.

The orphan pin here is the one a design review rebuilt. The first version
deleted a leaf from state.json on disk and re-POSTed /load, expecting the store
to notice — but the user-facing load path is ``sync_if_clean=False`` (docs/87,
"SM never swaps what you are looking at"), so a provably-clean working copy is
served UNCHANGED and the leaf is still there. The orphan is produced through
SM's own machinery instead.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.core import entity_notes
from quam_state_manager.web.app import create_app


def _state() -> dict:
    return {
        "qubits": {
            "qA1": {"id": "qA1", "f_01": 6.25e9, "T1": 1.0e-5,
                    "xy": {"operations": {"x180": "#./x180_DragCosine",
                                          "x180_DragCosine": {"amplitude": 0.11}}}},
            "qA2": {"id": "qA2", "f_01": 5.80e9, "T1": 1.2e-5,
                    "xy": {"operations": {"x180": "#./x180_DragCosine",
                                          "x180_DragCosine": {"amplitude": 0.12}}}},
        },
        "qubit_pairs": {"qA1-qA2": {"id": "qA1-qA2",
                                    "qubit_control": "#/qubits/qA1",
                                    "qubit_target": "#/qubits/qA2"}},
        "active_qubit_names": ["qA1", "qA2"],
    }


@pytest.fixture
def client(tmp_path: Path):
    (tmp_path / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(
        json.dumps({"wiring": {"qubits": {}}, "network": {"host": "10.1.1.1"}}),
        encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_app_instance"))
    c = app.test_client()
    c.post("/load", data={"folder": str(tmp_path)})
    c._live = str(tmp_path)
    c._inst = str(tmp_path / "_app_instance")
    return c


def _html(resp) -> str:
    return resp.get_data(as_text=True)


class TestTheRoundTrip:
    def test_a_note_can_be_written_read_and_deleted(self, client):
        r = client.post("/note", data={"subject": "qubits.qA1",
                                       "text": "flux line contact is suspect"})
        assert r.status_code == 200 and r.get_json()["ok"] is True

        got = client.get("/notes").get_json()
        assert got["count"] == 1
        assert got["present"][0]["subject"] == "qubits.qA1"
        assert got["present"][0]["orphan"] is False
        assert "suspect" in _html(client.get("/notes/panel"))

        r = client.post("/note/delete", data={"subject": "qubits.qA1"})
        assert r.get_json()["ok"] is True
        assert client.get("/notes").get_json()["count"] == 0

    def test_every_mutation_returns_the_rerendered_panel(self, client):
        """The client owns no model: it swaps the HTML the server sent back."""
        for url, data in (("/note", {"subject": "qubits.qA1", "text": "a"}),
                          ("/note/delete", {"subject": "qubits.qA1"})):
            body = client.post(url, data=data).get_json()
            assert "panel" in body and 'id="notes-panel"' in body["panel"]

    def test_a_note_on_a_leaf_is_a_first_class_subject(self, client):
        r = client.post("/note", data={"subject": "qubits.qA1.T1",
                                       "text": "refit this, the fit was bad"})
        assert r.get_json()["ok"] is True
        got = client.get("/notes").get_json()
        assert got["present"][0]["entity"] == "qubits.qA1"

    def test_a_stale_rev_is_a_409_carrying_the_other_text(self, client):
        client.post("/note", data={"subject": "qubits.qA1", "text": "mine"})
        client.post("/note", data={"subject": "qubits.qA1", "text": "theirs"})
        r = client.post("/note", data={"subject": "qubits.qA1",
                                       "text": "mine again", "expect_rev": "1"})
        assert r.status_code == 409
        body = r.get_json()
        assert body["note_conflict"] is True
        assert body["stored"]["text"] == "theirs"
        assert client.get("/notes").get_json()["present"][0]["text"] == "theirs"

    def test_a_note_cannot_land_on_a_chip_somebody_else_swapped_in(self, client):
        """The same gate every other edit door takes."""
        r = client.post("/note", data={"subject": "qubits.qA1", "text": "x",
                                       "expect_chip": "a-token-from-another-chip"})
        assert r.status_code == 409
        assert r.get_json()["chip_mismatch"] is True
        assert client.get("/notes").get_json()["count"] == 0

    def test_a_bad_note_is_refused_with_a_reason(self, client):
        for data in ({"subject": "", "text": "x"},
                     {"subject": "qubits.qA1", "text": ""},
                     {"subject": "qubits.qA1", "text": "x" * 3000}):
            r = client.post("/note", data=data)
            assert r.status_code == 400 and r.get_json()["error"]


class TestOrphans:
    def _orphan(self, client):
        """Produce a real orphan the way the app itself would.

        NOT by editing the live file behind the app: /load serves a
        provably-clean working copy unchanged (sync_if_clean=False, docs/87), so
        store.merged would still hold the leaf and there would be no orphan to
        find. The note is simply written about a path this chip never had —
        which is what a regenerated chip or a renamed pulse leaves behind.
        """
        client.post("/note", data={"subject": "qubits.qGONE.T1",
                                   "text": "measured before the rebuild"})

    def test_an_orphan_is_reported_and_kept(self, client):
        self._orphan(client)
        got = client.get("/notes").get_json()
        assert got["count"] == 1
        assert not got["present"]
        assert got["orphans"][0]["subject"] == "qubits.qGONE.T1"
        assert got["orphans"][0]["text"] == "measured before the rebuild"

    def test_the_panel_says_so_without_offering_to_tidy_it_away(self, client):
        self._orphan(client)
        html = _html(client.get("/notes/panel"))
        assert "not on this chip" in html
        assert "Nothing was deleted" in html
        assert "Re-address" in html

    def test_readdressing_is_checked_against_the_chip(self, client):
        self._orphan(client)
        r = client.post("/note/readdress", data={"subject": "qubits.qGONE.T1",
                                                 "new_subject": "qubits.qALSOGONE"})
        assert r.status_code == 400, "a typo must not create a second orphan"
        assert client.get("/notes").get_json()["orphans"], "and must change nothing"

        r = client.post("/note/readdress", data={"subject": "qubits.qGONE.T1",
                                                 "new_subject": "qubits.qA1.T1"})
        assert r.get_json()["ok"] is True
        got = client.get("/notes").get_json()
        assert not got["orphans"]
        assert got["present"][0]["text"] == "measured before the rebuild"


class TestTheSurfaces:
    def test_the_grid_marks_the_rows_that_have_something_to_read(self, client):
        client.post("/note", data={"subject": "qubits.qA1.T1", "text": "refit"})
        client.post("/note", data={"subject": "qubit_pairs.qA1-qA2",
                                   "text": "coupler drifts"})
        html = _html(client.get("/bulk", headers={"HX-Request": "true"}))
        assert 'bulk-rowhead-note' in html
        assert 'title="refit"' in html, "a LEAF note lights its entity's row"
        assert 'title="coupler drifts"' in html
        # and the row that has nothing to say is untouched
        assert html.count("bulk-rowhead-note") == 2

    def test_a_chip_with_no_notes_renders_no_markers(self, client):
        html = _html(client.get("/bulk", headers={"HX-Request": "true"}))
        assert "bulk-rowhead-note" not in html
        assert "bulk-note-mark" not in html

    def test_the_panel_rides_the_grid_render_with_no_extra_request(self, client):
        client.post("/note", data={"subject": "qubits.qA1", "text": "hello"})
        html = _html(client.get("/bulk", headers={"HX-Request": "true"}))
        assert 'id="notes-panel"' in html and "hello" in html

    def test_the_inspector_shows_the_entitys_own_note(self, client):
        client.post("/note", data={"subject": "qubits.qA1", "text": "do not trust T1"})
        html = _html(client.get("/qubit/qA1", headers={"HX-Request": "true"}))
        assert "inspector-note" in html and "do not trust T1" in html
        # a DIFFERENT qubit's header says nothing
        other = _html(client.get("/qubit/qA2", headers={"HX-Request": "true"}))
        assert "inspector-note" not in other

    def test_the_pair_inspector_gets_it_too(self, client):
        client.post("/note", data={"subject": "qubit_pairs.qA1-qA2",
                                   "text": "coupler drifts"})
        html = _html(client.get("/pair/qA1-qA2", headers={"HX-Request": "true"}))
        assert "inspector-note" in html and "coupler drifts" in html

    def test_a_leaf_note_does_not_fill_the_inspector_header(self, client):
        """The header is one line, so it carries the note written ABOUT the
        qubit — not a digest of everything under it. The grid marker is what
        rolls leaves up."""
        client.post("/note", data={"subject": "qubits.qA1.T1", "text": "refit"})
        html = _html(client.get("/qubit/qA1", headers={"HX-Request": "true"}))
        assert "inspector-note" not in html


class TestNoChipLoaded:
    def test_the_routes_answer_honestly_with_nothing_open(self, tmp_path):
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        c = app.test_client()
        got = c.get("/notes").get_json()
        assert got == {"notes": {}, "present": [], "orphans": [], "count": 0,
                       "chip": False}
        assert c.post("/note", data={"subject": "qubits.q1", "text": "x"}).status_code == 400
        assert "No chip is open" in _html(c.get("/notes/panel"))


class TestHandTunedReachesTheConfirmation:
    """The mark's ONLY job: adding a clause to a confirmation that already
    exists. The overwrite-live preflight (docs/86) already names what
    disappears, because a push discards live content — a value somebody tuned
    by hand is exactly the content worth naming before it goes."""

    def test_the_preflight_names_the_marked_values(self, client):
        client.post("/note", data={"subject": "qubits.qA1.T1",
                                   "text": "tuned by hand on 09-04",
                                   "hand_tuned": "1"})
        client.post("/note", data={"subject": "qubits.qA2", "text": "just a note"})
        got = client.get("/state/overwrite-live/preflight").get_json()
        assert got["ok"] is True
        assert got["hand_tuned"] == ["qubits.qA1.T1"], got
        # everything the confirm said before is still there
        for key in ("live_changes", "unsaved", "reversible", "run_active"):
            assert key in got

    def test_it_is_an_empty_list_when_nothing_is_marked(self, client):
        client.post("/note", data={"subject": "qubits.qA1", "text": "plain"})
        assert client.get("/state/overwrite-live/preflight").get_json()["hand_tuned"] == []

    def test_the_note_probe_can_never_break_the_gate(self, client, monkeypatch):
        """An advisory that can 500 the confirm is worse than no advisory: the
        user would be unable to overwrite at all."""
        from quam_state_manager.core import entity_notes
        monkeypatch.setattr(entity_notes, "load",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        got = client.get("/state/overwrite-live/preflight").get_json()
        assert got["ok"] is True and got["hand_tuned"] == []

    def test_the_mark_blocks_nothing(self, client):
        """Pinned as an absence, deliberately. A gate SM cannot enforce against
        the lab's own calibration nodes would teach people to stop reading the
        gates it CAN enforce."""
        client.post("/note", data={"subject": "qubits.qA1", "text": "x",
                                   "hand_tuned": "1"})
        r = client.post("/field/edit", data={"dot_path": "qubits.qA1.T1",
                                            "value": "2e-05"})
        assert r.status_code == 200, r.get_data(as_text=True)[:200]

    def test_the_panel_offers_and_shows_the_mark(self, client):
        client.post("/note", data={"subject": "qubits.qA1", "text": "x",
                                   "hand_tuned": "1"})
        html = _html(client.get("/notes/panel"))
        assert "notes-tuned" in html and "hand-tuned" in html
        # the ROW's own tooltip, not the add-form's: both say it, so a
        # whole-page search stayed green when the row's promise was rewritten
        row = html[html.index('class="notes-tuned"'):]
        row = row[:row.index("</span>")]
        assert "does not and cannot block" in row, (
            "the row's mark must say what it is NOT, where it is shown")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_notes_panel_selfcheck_passes():
    """The panel's client, driven under jsdom.

    A source pin would not have caught the one that matters: an EDIT that
    silently drops the hand-tuned mark. The harness clicks Edit on a marked row
    and reads what actually goes over the wire.
    """
    root = Path(__file__).resolve().parent.parent
    r = subprocess.run(["node", str(root / "tests" / "notes_panel_selfcheck.cjs")],
                       capture_output=True, text=True, encoding="utf-8",
                       cwd=str(root), timeout=180)
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)
