"""F29 -- an agent's Apply outside auto mode becomes an approval card.

In ``ask-writes`` (the new-chip default) an agent's press on
``/state/apply-to-live`` never writes the chip: its staged rows leave the tray
and become ONE approval card that a person decides. A person's own press still
writes directly; ``auto`` mode lets the agent's press through; a tray that adds
or removes keys is refused (an approval carries value changes only).

Fixtures are test_agent_runs's: the real routes, the real working copy.
"""

from __future__ import annotations

import json

import pytest
from flask import request

from quam_state_manager import mcp
from quam_state_manager.core import approvals, limits
from tests.test_agent_runs import (  # noqa: F401 -- fixtures by import
    AGENT, HUMAN, _chip, app, c, cal, fake_run, inst, synth_folder,
)
from tests.test_mcp_bridge import CHIP, FakeLink

J = {"Accept": "application/json"}


def _live(folder):
    return json.loads((folder / "state.json").read_text(encoding="utf-8"))["qubits"]["qA1"]


def _agent_edit_and_apply(c, value="4242"):
    assert c.post("/field/edit", data={"dot_path": "qubits.qA1.T1", "value": value},
                  headers=AGENT).status_code == 200
    return c.post("/state/apply-to-live", data={"seen_changes": "1"}, headers={**AGENT, **J})


class TestAskWritesHolds:
    def test_the_agents_apply_is_held_as_one_card(self, c, inst, synth_folder):
        before = _live(synth_folder)["T1"]
        r = _agent_edit_and_apply(c)
        assert r.status_code == 202, r.get_data(as_text=True)[:300]
        d = r.get_json()
        assert d["held"] is True and d["applied"] is False and d["approval"]["id"]
        assert d["approval"]["n_writes"] == 1 and "held for approval" in d["message"]
        assert _live(synth_folder)["T1"] == before, "the live chip moved"
        assert c.get("/api/agent/chip").get_json()["pending"] == 0, "the tray still holds the agent's value"
        pend = approvals.pending(str(inst), _chip(c))
        assert len(pend) == 1
        ap = approvals.get(str(inst), _chip(c), d["approval"]["id"])
        assert ap["status"] == "pending"
        assert [(w["path"], w["new"]) for w in ap["writes"]] == [("qubits.qA1.T1", 4242)]
        assert ap["writes"][0]["old"] == before

    def test_a_person_approves_and_one_undo_takes_it_back(self, c, inst, synth_folder):
        before = _live(synth_folder)["T1"]
        aid = _agent_edit_and_apply(c).get_json()["approval"]["id"]
        a = c.post(f"/api/agent/approvals/{aid}/approve", json={}, headers=HUMAN)
        assert a.status_code == 200 and a.get_json()["stage"]["applied"], a.get_json()
        assert _live(synth_folder)["T1"] == 4242
        u = c.post("/undo", headers={**HUMAN, **J})
        assert u.status_code == 200
        assert _live(synth_folder)["T1"] == before, "one Ctrl+Z did not take the whole approval back"

    def test_the_card_never_prints_none(self, c, inst):
        aid = _agent_edit_and_apply(c).get_json()["approval"]["id"]
        r = c.get("/api/agent/chat/cards")
        assert r.status_code == 200
        cards = [x for x in (r.get_json().get("live") or {}).get("approvals") or [] if x.get("id") == aid]
        assert len(cards) == 1, "the held card is not in the feed"

        def strings(v):
            if isinstance(v, str):
                yield v
            elif isinstance(v, dict):
                for x in v.values():
                    yield from strings(x)
            elif isinstance(v, list):
                for x in v:
                    yield from strings(x)
        bad = [t for t in strings(cards[0]) if "None" in t]
        assert not bad, bad

    def test_a_structural_row_is_refused_and_the_tray_kept(self, c, inst, synth_folder):
        r = c.post("/field/edit-batch", json={"updates": [{"dot_path": "qubits.qA1.brand_new_key", "value": "1",
                                                           "create": True}], "expect_chip": ""}, headers=AGENT)
        assert r.status_code == 200, r.get_data(as_text=True)[:300]
        n = c.get("/api/agent/chip").get_json()["pending"]
        assert n >= 1
        r = c.post("/state/apply-to-live", data={"seen_changes": str(n)}, headers={**AGENT, **J})
        assert r.status_code == 409 and r.get_json()["conflict"] == "agent_structural_rows", r.get_json()
        assert c.get("/api/agent/chip").get_json()["pending"] == n
        assert not approvals.pending(str(inst), _chip(c))
        assert "brand_new_key" not in _live(synth_folder)


def test_a_persons_apply_in_ask_writes_writes_directly(c, inst, synth_folder):
    assert c.post("/field/edit", data={"dot_path": "qubits.qA1.T1", "value": "777"}).status_code == 200
    r = c.post("/state/apply-to-live", data={"seen_changes": "1"}, headers={**HUMAN, **J})
    assert r.status_code == 200
    assert _live(synth_folder)["T1"] == 777
    assert not approvals.pending(str(inst), _chip(c))


def test_auto_mode_lets_the_agents_apply_through(c, inst, synth_folder):
    limits.save(str(inst), _chip(c), {"mode": "auto"})
    r = _agent_edit_and_apply(c)
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    assert _live(synth_folder)["T1"] == 4242
    assert not approvals.pending(str(inst), _chip(c))


WIN_A = {"X-SM-Window-ID": "window-a"}
WIN_B = {"X-SM-Window-ID": "window-b"}


def _undo_trigger(c, window):
    r = c.post("/undo", headers={**HUMAN, **window})
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    raw = r.headers.get("HX-Trigger")
    return json.loads(raw) if raw else {}


def _approve_in(c, window):
    aid = _agent_edit_and_apply(c).get_json()["approval"]["id"]
    a = c.post(f"/api/agent/approvals/{aid}/approve", json={}, headers={**HUMAN, **window})
    assert a.status_code == 200 and a.get_json()["stage"]["applied"], a.get_json()
    return aid


class TestApproveIsThePressingWindowsChange:
    def test_undo_in_the_approving_window_does_not_ask(self, c, inst, synth_folder):
        before = _live(synth_folder)["T1"]
        _approve_in(c, WIN_A)
        assert _live(synth_folder)["T1"] == 4242
        t = _undo_trigger(c, WIN_A)
        assert "undoAskPrevSession" not in t
        assert _live(synth_folder)["T1"] == before

    def test_undo_in_another_window_asks_and_writes_nothing(self, c, inst, synth_folder):
        _approve_in(c, WIN_A)
        t = _undo_trigger(c, WIN_B)
        assert "another window" in t["undoAskPrevSession"]["message"]
        assert _live(synth_folder)["T1"] == 4242

    def test_an_agent_only_stage_still_asks(self, c, inst, synth_folder):
        limits.save(str(inst), _chip(c), {"mode": "auto"})
        own = c.environ_base.pop("HTTP_X_SM_WINDOW_ID")
        try:
            assert _agent_edit_and_apply(c).status_code == 200
        finally:
            c.environ_base["HTTP_X_SM_WINDOW_ID"] = own
        assert _live(synth_folder)["T1"] == 4242
        t = _undo_trigger(c, WIN_A)
        assert "undoAskPrevSession" in t
        assert _live(synth_folder)["T1"] == 4242


def _agent_stage_without_window(c, value="4242"):
    own = c.environ_base.pop("HTTP_X_SM_WINDOW_ID")
    try:
        r = c.post("/field/edit", data={"dot_path": "qubits.qA1.T1", "value": value}, headers=AGENT)
        assert r.status_code == 200, r.get_data(as_text=True)[:300]
    finally:
        c.environ_base["HTTP_X_SM_WINDOW_ID"] = own


class TestAPersonsApplyOfAgentRowsIsThePressingWindowsChange:
    """Agent rows carry no window; the person who presses Apply takes them."""

    def _apply_in_b(self, c, synth_folder):
        _agent_stage_without_window(c)
        r = c.post("/state/apply-to-live", data={"seen_changes": "1"}, headers={**HUMAN, **WIN_B, **J})
        assert r.status_code == 200, r.get_data(as_text=True)[:300]
        assert _live(synth_folder)["T1"] == 4242

    def test_undo_in_the_applying_window_does_not_ask(self, c, inst, synth_folder):
        before = _live(synth_folder)["T1"]
        self._apply_in_b(c, synth_folder)
        t = _undo_trigger(c, WIN_B)
        assert "undoAskPrevSession" not in t
        assert _live(synth_folder)["T1"] == before

    def test_undo_in_another_window_asks_and_writes_nothing(self, c, inst, synth_folder):
        self._apply_in_b(c, synth_folder)
        t = _undo_trigger(c, WIN_A)
        assert "another window" in t["undoAskPrevSession"]["message"]
        assert _live(synth_folder)["T1"] == 4242


class TestTheWindowStampLivesOneRequest:
    def test_no_stamp_leaks_into_the_next_request(self, c, inst, synth_folder):
        from quam_state_manager.core.loader import edit_window_id
        _approve_in(c, WIN_B)
        assert edit_window_id.get() is None, "an approve left its window stamp on the thread"
        assert c.post("/field/edit", data={"dot_path": "qubits.qA1.T1", "value": "5151"},
                      headers={**HUMAN, **WIN_B}).status_code == 200
        assert edit_window_id.get() is None, "an edit left its window stamp on the thread"

    def test_a_nested_request_context_keeps_the_outer_stamp(self, app, c):
        from quam_state_manager.core.loader import edit_window_id
        with app.test_request_context("/", headers={**HUMAN, **WIN_B}):
            app.preprocess_request()
            assert edit_window_id.get() == "window-b"
            with app.test_request_context("/"):
                pass
            assert edit_window_id.get() == "window-b", "a nested context released the outer stamp"
        assert edit_window_id.get() is None

    def test_a_request_that_raises_leaves_no_stamp(self, app):
        from quam_state_manager.core.loader import edit_window_id
        from quam_state_manager.web import routes
        seen = {}

        def _raising_view():
            seen["stamp"] = edit_window_id.get()
            seen["environ"] = request.environ
            raise RuntimeError("view failed")

        # test-only route on this test's own app (no request served yet, so setup is still open)
        app.add_url_rule("/_test/raising-view", "_test_raising_view", _raising_view, methods=["POST"])
        client = app.test_client()
        with pytest.raises(RuntimeError, match="view failed"):
            client.post("/_test/raising-view", headers={**HUMAN, **WIN_B})
        assert seen["stamp"] == "window-b", "the stamp was not bound while the view ran"
        assert edit_window_id.get() is None, "a request that raised left its window stamp on the thread"
        assert routes._EDIT_WINDOW_TOKEN not in seen["environ"], "the request context still holds the stamp token"


def test_the_mcp_bridge_relays_a_held_apply(monkeypatch):
    held = {"ok": False, "held": True, "applied": False, "approval": {"id": "ap1", "n_writes": 1},
            "message": "held for approval: card ap1 (1 value(s)); nothing was written to the chip"}
    chip = (200, dict(CHIP[1], pending=1))
    fl = FakeLink({("GET", "/api/agent/chip"): chip,
                   ("GET", "/api/agent/tray"): (200, {"ok": True, "count": 1, "seen_changes": 1,
                                                      "entries": [{"path": "qubits.qA1.T1"}]}),
                   ("POSTJ", "/api/agent/journal"): (200, {"ok": True}),
                   ("POST", "/state/apply-to-live"): (202, held)})
    monkeypatch.setattr(mcp, "_link", fl)
    monkeypatch.setattr(mcp, "_chip", None)
    for k, v in {"_seen": 1, "_seen_key": None, "_seen_token": "tok", "_seen_sig": None,
                 "_seen_paths": ["qubits.qA1.T1"], "_pin_key": None}.items():
        monkeypatch.setattr(mcp, k, v)
    r = mcp.t_apply_to_live({})
    assert r["applied"] is False and r["held_for_approval"] == {"id": "ap1", "n_writes": 1}, r
    assert "do not re-stage" in r["how"]
    assert any(cl[1] == "/state/apply-to-live" for cl in fl.calls)
    assert mcp._seen == 0 and mcp._seen_paths == []
