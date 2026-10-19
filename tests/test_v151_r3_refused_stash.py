"""v1.5.1 R3 M3: an edit the user undid after a refused write stays undone.

Save -> Pull & apply refused (read-only folder) -> the tray shows the row.
The merge door had replayed the re-apply stash into that row, but the press
that then wrote found the pre-merge stash and, refused, put it back beside
the row. The user undid the row; the next Apply replayed the stash and wrote
the undone value to the live chip.
"""
from __future__ import annotations

import json

from tests.test_v151_refused_write_rows import _ctx, _edit, _rows, _set_readonly, env  # noqa: F401


def _live_off(env):
    return json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))[
        "qubits"]["qA1"]["z"]["joint_offset"]


def test_an_undone_edit_is_not_written_by_the_next_apply(env):
    c = env["c"]
    _edit(env, "0.09")
    assert c.post("/save").status_code in (200, 204)
    ctx = _ctx(env)
    assert ctx.get("pending_reapply"), "Save stashes the saved edits"

    _set_readonly(env["live"], True)
    r = c.post("/state/sync", data={"mode": "apply", "ack_unseen": "1", "ack_collision": "1"})
    d = r.get_json() or {}
    assert r.status_code == 409 and d.get("error_code") == "live_write_failed", r.data[:400]
    assert _rows(env) == ["qubits.qA1.z.joint_offset"], "the refused edit stays a row"
    assert not ctx.get("pending_reapply"), (
        "the merge consumed the stash into the row; a refused write must not put it back")

    r = c.post("/undo", data={"ack_unseen": "1"})
    assert r.status_code == 200
    assert _rows(env) == [], "the undo removed the row"

    _set_readonly(env["live"], False)
    r = c.post("/state/sync", data={"mode": "apply", "ack_unseen": "1", "ack_collision": "1"})
    assert r.status_code in (200, 409), r.data[:300]
    assert _live_off(env) == 0.08, "the edit the user undid was written to the live chip"


def test_a_refused_merge_keeps_the_edit_as_a_row_and_a_retry_writes_it(env):
    """The other half: clearing the stash loses nothing -- the edit is the row,
    and the retry after the folder is fixed writes it."""
    c = env["c"]
    _edit(env, "0.09")
    assert c.post("/save").status_code in (200, 204)
    _set_readonly(env["live"], True)
    r = c.post("/state/sync", data={"mode": "apply", "ack_unseen": "1", "ack_collision": "1"})
    assert r.status_code == 409
    _set_readonly(env["live"], False)
    r = c.post("/state/sync", data={"mode": "apply", "ack_unseen": "1", "ack_collision": "1"})
    assert r.status_code == 200, r.data[:300]
    assert _live_off(env) == 0.09
