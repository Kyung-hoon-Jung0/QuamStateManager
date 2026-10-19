"""v1.5.1 R4 a3: a note saved while the page leaves is not lost behind a save in flight.

R3 MINOR 7's leave flush sent the newest note text with ``expected`` = the
text of the save still on its way. A threaded server that took the flush
first found the OLD text stored, refused it as a conflict, and with the page
gone nobody saw it: the newest text was lost.

The flush now expects the text stored before that save and names that save's
text as ``supersedes``; the route accepts either. Whichever request lands
second finds the other's text: the in-flight (older) one is then refused,
so the newest stays. Another window's text is still a conflict. Checked here
on the real route in both orders; the page's flush body is pinned in
tests/v151_r3_note_self_conflict_selfcheck.cjs (the flush-first order too).
"""
from __future__ import annotations

import pytest

from quam_state_manager.web import routes
from tests.test_multifolder_datasets import _app_with_folders, _seed_run


@pytest.fixture
def note(tmp_path):
    f = tmp_path / "data"
    _seed_run(f, 1)
    app, c = _app_with_folders(tmp_path, [f])
    with app.app_context():
        uid = f"{routes._folder_key(f)}:1"

    def post(**body):
        return c.post(f"/dataset/{uid}/note", json=body)

    def stored():
        with app.test_request_context():
            ds, rid, _ = routes._resolve_run(uid)
            return ds.runs[rid].note
    assert post(note="v1", expected="").status_code == 200
    return post, stored


IN_FLIGHT = {"note": "v2", "expected": "v1"}
FLUSH = {"note": "v3", "expected": "v1", "supersedes": "v2"}


def test_the_save_in_flight_lands_first(note):
    post, stored = note
    assert post(**IN_FLIGHT).status_code == 200
    r = post(**FLUSH)
    assert r.status_code == 200, r.get_json()
    assert stored() == "v3"


def test_the_leave_flush_lands_first(note):
    post, stored = note
    r = post(**FLUSH)
    assert r.status_code == 200, r.get_json()
    late = post(**IN_FLIGHT)                      # the older text arrives second
    assert late.status_code == 409 and late.get_json().get("note_conflict")
    assert stored() == "v3", "the newest text was replaced by the save that was on its way"


def test_another_windows_text_is_still_a_conflict(note):
    post, stored = note
    assert post(note="other", expected="v1").status_code == 200   # another window
    r = post(**FLUSH)
    assert r.status_code == 409 and r.get_json().get("note_conflict")
    assert stored() == "other"
