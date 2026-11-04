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
