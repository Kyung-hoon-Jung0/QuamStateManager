"""docs/246: the MCP bridge's P0 safety defects (agent validation campaign,
2026-10-03, findings A-03..A-07).

* A-03 -- ``state_edit(fsp_ack="comp")`` must stage what the FSP popup's comp
  button stages: the FSP and every compensated amplitude, ONE group.
* A-04/A-05 -- an agent's ``/undo`` touches only its own staged group: never a
  person's row, never the cross-save journal (which writes the LIVE chip).
* A-06 -- the ``SM_CHIP`` pin is matched against the chip's identity (chip_key /
  extras.chip_name), not its folder name.
* A-07 -- an apply declares the chip its ``seen_changes`` were read on; another
  chip open at press time is refused, by the bridge AND by the server.

Server rules run against a real Flask app on synthetic chips; bridge rules run
in-process against the FakeLink of tests/test_mcp_bridge.py.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import pytest

from quam_state_manager import mcp
from quam_state_manager.web.app import create_app
from tests.test_fsp_compensation import _WIRING, _state
from tests.test_mcp_bridge import CHIP, FakeLink

_FSP = "ports.mw_outputs.con1.1.1.full_scale_power_dbm"
_AGENT = {"X-SM-Agent": "claude"}
_T1 = "qubits.qA1.resonator.operations.const.length"


def _chip_dir(root: Path, chip_name: str | None = None, host: str | None = None) -> Path:
    """A chip in a folder called ``chip`` under *root* -- every fridge here has
    the same folder name. *host* gives it another network identity (so
    another fingerprint token)."""
    d = root / "chip"
    d.mkdir(parents=True)
    st = _state()
    if chip_name:
        st["extras"] = {"chip_name": chip_name}
    wiring = json.loads(json.dumps(_WIRING))
    if host:
        wiring["network"]["host"] = host
    (d / "state.json").write_text(json.dumps(st), encoding="utf-8")
    (d / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")
    return d


def _app(tmp_path, tag="a"):
    app = create_app(testing=True, instance_path=str(tmp_path / f"_inst_{tag}"))
    return app, app.test_client()


def _ctx(app):
    name = app.config["active_context"]
    return app.config["contexts"][name]


def _h(p: Path) -> str:
    return hashlib.sha256((p / "state.json").read_bytes()).hexdigest()


def _log(ctx):
    return [(e.dot_path, e.new_value, e.group_id, getattr(e, "actor", "human"))
            for e in ctx["store"].change_log]


# ------------------------------------------------------------------ A-03

class TestFspCompOnTheEditDoor:
    def test_comp_on_field_edit_stages_exactly_what_the_popup_stages(self, tmp_path):
        # the browser: 409 offer -> the popup's comp batch
        app_b, cb = _app(tmp_path, "browser")
        cb.post("/load", data={"folder": str(_chip_dir(tmp_path / "b"))})
        plan = cb.post("/field/edit", data={"dot_path": _FSP, "value": "-6"}).get_json()["fsp_compensation"]
        updates = ([{"dot_path": _FSP, "value": "-6"}]
                   + [{"dot_path": a["path"], "value": str(a["new"])} for a in plan["amps"]])
        assert cb.post("/field/edit-batch", json={"updates": updates, "fsp_ack": "comp"}).get_json()["ok"]
        browser = _log(_ctx(app_b))

        # the agent: /field/edit with fsp_ack=comp, as the MCP bridge sends it
        app_a, ca = _app(tmp_path, "agent")
        ca.post("/load", data={"folder": str(_chip_dir(tmp_path / "a"))})
        r = ca.post("/field/edit", data={"dot_path": _FSP, "value": "-6", "fsp_ack": "comp"}, headers=_AGENT)
        assert r.status_code == 200 and r.get_json()["ok"], r.get_json()
        agent = _log(_ctx(app_a))

        assert len(agent) == len(browser) == 1 + len(plan["amps"]) == 4
        assert [p for p, *_ in agent] == [p for p, *_ in browser]
        for (p, va, _g, _a), (_p2, vb, _g2, _a2) in zip(agent, browser):
            # the SAME value and the same type -- not merely close: the server
            # twin takes the popup's door (text, parsed against the target)
            assert va == vb and type(va) is type(vb), (p, va, vb)
        # and the amps really moved by the FSP identity, 10**((0-(-6))/20)
        amp = dict((p, v) for p, v, *_ in agent)["qubits.qA1.resonator.operations.readout.amplitude"]
        assert math.isclose(amp, 0.2 * 10 ** (6 / 20), rel_tol=1e-12)
        assert len({g for _p, _v, g, _a in agent}) == 1 and agent[0][2] is not None, "ONE group"
        assert {a for *_x, a in agent} == {"by_claude"}, "every row of the bundle is the agent's"
        assert r.get_json()["group_id"] == agent[0][2]

        # and the agent's own undo takes the whole bundle back in one press
        u = ca.post("/undo", headers=_AGENT)
        assert u.status_code == 200 and _log(_ctx(app_a)) == []

    def test_solo_still_writes_the_fsp_alone(self, tmp_path):
        app, c = _app(tmp_path)
        c.post("/load", data={"folder": str(_chip_dir(tmp_path))})
        r = c.post("/field/edit", data={"dot_path": _FSP, "value": "-6", "fsp_ack": "solo"}, headers=_AGENT)
        assert r.status_code == 200 and [p for p, *_ in _log(_ctx(app))] == [_FSP]


# ------------------------------------------------------------------ A-04 / A-05

@pytest.fixture
def chip(tmp_path):
    app, c = _app(tmp_path)
    d = _chip_dir(tmp_path / "live")
    c.post("/load", data={"folder": str(d)})
    return app, c, d


class TestAgentUndoIsItsOwnOnly:
    def test_a_persons_staged_row_is_refused_and_kept(self, chip):
        app, c, _d = chip
        assert c.post("/field/edit", data={"dot_path": _T1, "value": "123"}, headers=_AGENT).status_code == 200
        assert c.post("/field/edit", data={"dot_path": "qubits.qA1.xy.operations.x180.length",
                                           "value": "44"}).status_code == 200      # a person
        before = _log(_ctx(app))
        r = c.post("/undo", headers=_AGENT)
        assert r.status_code == 409 and r.get_json()["refused"] == "human_group"
        assert r.get_json()["paths"] == ["qubits.qA1.xy.operations.x180.length"]
        assert _log(_ctx(app)) == before, "nothing undone"

    def test_its_own_group_on_top_is_undone(self, chip):
        app, c, _d = chip
        c.post("/field/edit", data={"dot_path": "qubits.qA1.xy.operations.x180.length", "value": "44"})
        c.post("/field/edit", data={"dot_path": _T1, "value": "123"}, headers=_AGENT)
        r = c.post("/undo", headers=_AGENT)
        assert r.status_code == 200
        assert [p for p, *_ in _log(_ctx(app))] == ["qubits.qA1.xy.operations.x180.length"]

    def test_an_empty_tray_never_walks_the_journal_onto_the_live_chip(self, chip):
        app, c, d = chip
        c.post("/field/edit", data={"dot_path": _T1, "value": "321"})        # a person stages ...
        assert c.post("/state/apply-to-live").status_code == 200           # ... and applies
        h_applied = _h(d)
        assert _log(_ctx(app)) == []
        r = c.post("/undo", headers=_AGENT)
        assert r.status_code == 409 and r.get_json()["refused"] == "journal"
        assert _h(d) == h_applied, "the person's applied value is still on the live chip"
        assert _log(_ctx(app)) == []
        # the same press by a PERSON still walks it (the gate is the actor, not the route)
        c.post("/undo")
        assert _h(d) != h_applied

    def test_a_burst_is_one_press_for_an_agent(self, chip):
        app, c, _d = chip
        c.post("/field/edit", data={"dot_path": "qubits.qA1.xy.operations.x180.length", "value": "44"})
        c.post("/field/edit", data={"dot_path": _T1, "value": "123"}, headers=_AGENT)
        assert c.post("/undo?n=5", headers=_AGENT).status_code == 200
        assert [p for p, *_ in _log(_ctx(app))] == ["qubits.qA1.xy.operations.x180.length"]

    def test_redo_is_a_persons_key(self, chip):
        _app_, c, _d = chip
        r = c.post("/redo", headers=_AGENT)
        assert r.status_code == 409 and r.get_json()["refused"] == "redo"

    def test_a_staged_journal_step_on_top_is_refused(self, chip):
        """A ``jrn:`` row is applied history a person's Ctrl+Z staged -- refused
        even when its actor field reads as an agent's (the journal check comes
        first)."""
        from quam_state_manager.core import undo_journal
        app, c, _d = chip
        c.post("/field/edit", data={"dot_path": _T1, "value": "123"}, headers=_AGENT)
        top = _ctx(app)["store"].change_log[-1]
        top.group_id = undo_journal.GID_PREFIX + "u1"
        before = _log(_ctx(app))
        r = c.post("/undo", headers=_AGENT)
        assert r.status_code == 409 and r.get_json()["refused"] == "journal"
        assert _log(_ctx(app)) == before


# ------------------------------------------------------------------ A-06 / A-07 server

class TestApplyNamesItsChip:
    def test_chip_and_tray_say_who_the_chip_is(self, tmp_path):
        _a, c = _app(tmp_path)
        c.post("/load", data={"folder": str(_chip_dir(tmp_path / "x", chip_name="arbel"))})
        ch = c.get("/api/agent/chip").get_json()
        assert ch["name"] == "chip" and ch["declared_name"] == "arbel"
        assert ch["pin"] == ch["chip_key"] and ch["chip_key"]
        tr = c.get("/api/agent/tray").get_json()
        assert tr["chip_key"] == ch["chip_key"] and tr["chip_token"] == ch["chip_token"] and tr["seen_sig"]

    def test_an_apply_seen_on_another_chip_is_refused(self, tmp_path):
        """Two fridges, same folder name, same content (so the same fingerprint
        token), the SAME path staged on both: only the chip key tells them
        apart, and the server refuses."""
        app, c = _app(tmp_path)
        da = _chip_dir(tmp_path / "fridgeA")
        db = _chip_dir(tmp_path / "fridgeB")
        c.post("/load", data={"folder": str(da)})
        c.post("/field/edit", data={"dot_path": _T1, "value": "111"}, headers=_AGENT)
        seen = c.get("/api/agent/tray").get_json()
        c.post("/load", data={"folder": str(db)})                       # a person switches chips
        c.post("/field/edit", data={"dot_path": _T1, "value": "222"}, headers=_AGENT)
        hb = _h(db)
        r = c.post("/state/apply-to-live", headers={**_AGENT, "Accept": "application/json"},
                   data={"seen_changes": seen["seen_changes"], "seen_sig": seen["seen_sig"],
                         "expect_chip": seen["chip_token"], "expect_chip_key": seen["chip_key"]})
        assert r.status_code == 409 and r.get_json()["conflict"] == "chip_mismatch", r.get_data(as_text=True)
        assert _h(db) == hb and len(_ctx(app)["store"].change_log) == 1, "nothing written"
        # the tray read ON this chip applies
        now = c.get("/api/agent/tray").get_json()
        r = c.post("/state/apply-to-live", headers={**_AGENT, "Accept": "application/json"},
                   data={"seen_changes": now["seen_changes"], "seen_sig": now["seen_sig"],
                         "expect_chip": now["chip_token"], "expect_chip_key": now["chip_key"]})
        assert r.status_code == 200 and _h(db) != hb

    def test_a_token_from_another_chip_is_refused_without_a_key(self, tmp_path):
        """A caller that names the chip only by its fingerprint token (no key):
        the token gate every edit door has now guards the apply door too."""
        app, c = _app(tmp_path)
        da = _chip_dir(tmp_path / "fridgeA")
        db = _chip_dir(tmp_path / "fridgeB", host="4.4.4.4")      # another network identity
        c.post("/load", data={"folder": str(da)})
        c.post("/field/edit", data={"dot_path": _T1, "value": "111"}, headers=_AGENT)
        seen = c.get("/api/agent/tray").get_json()
        c.post("/load", data={"folder": str(db)})
        c.post("/field/edit", data={"dot_path": _T1, "value": "222"}, headers=_AGENT)
        assert c.get("/api/agent/tray").get_json()["chip_token"] != seen["chip_token"]
        hb = _h(db)
        r = c.post("/state/apply-to-live", headers={**_AGENT, "Accept": "application/json"},
                   data={"seen_changes": seen["seen_changes"], "seen_sig": seen["seen_sig"],
                         "expect_chip": seen["chip_token"]})
        assert r.status_code == 409 and r.get_json()["chip_mismatch"] is True, r.get_data(as_text=True)
        assert _h(db) == hb and len(_ctx(app)["store"].change_log) == 1

    def test_a_tray_read_across_a_chip_switch_is_refused(self, tmp_path, monkeypatch):
        """The rows and the key it hands back must describe ONE chip: a /load
        landing mid-read is a refusal, never chip A's rows under chip B's key."""
        from quam_state_manager.web import agent_api
        app, c = _app(tmp_path)
        c.post("/load", data={"folder": str(_chip_dir(tmp_path / "fridgeB"))})
        name_b = app.config["active_context"]
        c.post("/load", data={"folder": str(_chip_dir(tmp_path / "fridgeA"))})
        c.post("/field/edit", data={"dot_path": _T1, "value": "111"}, headers=_AGENT)
        real = agent_api._chip_key

        def _switch_then_key():
            app.config["active_context"] = name_b              # the person's /load lands here
            return real()
        monkeypatch.setattr(agent_api, "_chip_key", _switch_then_key)
        r = c.get("/api/agent/tray")
        assert r.status_code == 409 and r.get_json()["conflict"] == "chip_switched", r.get_json()

    def test_the_internal_door_names_the_chip_it_staged_on(self, tmp_path, monkeypatch):
        """SM's own apply of an agent run's writes (``_stage_writes``) stages on
        the run's chip, then presses the door -- which applies the ACTIVE chip.
        A /load between its check and the door must not push the other chip's
        tray."""
        from quam_state_manager.web import agent_api
        app, c = _app(tmp_path)
        db = _chip_dir(tmp_path / "fridgeB")
        c.post("/load", data={"folder": str(db)})
        name_b = app.config["active_context"]
        c.post("/field/edit", data={"dot_path": _T1, "value": "222"}, headers=_AGENT)   # B: one agent row
        c.post("/load", data={"folder": str(_chip_dir(tmp_path / "fridgeA"))})
        ctx_a = _ctx(app)
        ctx_b = app.config["contexts"][name_b]
        hb = _h(db)
        real = agent_api._pre_door_state

        def _switch_at_the_door(r, ctx):
            out = real(r, ctx)
            app.config["active_context"] = name_b
            return out
        monkeypatch.setattr(agent_api, "_pre_door_state", _switch_at_the_door)
        with app.app_context():
            res = agent_api._stage_writes(app, str(ctx_a["path"]),
                                          [{"path": _T1, "new": 111, "old": 100}],
                                          "g-door", "by_claude", None, True)
        assert res["applied"] is False, res
        assert _h(db) == hb, "chip B's live files untouched"
        assert len(ctx_b["store"].change_log) == 1, "chip B's tray untouched"
        assert ctx_a["store"].change_log == [], "the run's group came back out of chip A's tray"


# ------------------------------------------------------------------ bridge

@pytest.fixture
def link(monkeypatch):
    fl = FakeLink({("GET", "/api/agent/chip"): (200, {**CHIP[1], "chip_key": "chip-aaaa", "pin": "chip-aaaa"}),
                   ("GET", "/api/agent/tray"): (200, {"ok": True, "count": 1, "seen_changes": 1,
                                                     "entries": [{"path": "a"}], "seen_sig": "s1",
                                                     "chip_key": "chip-aaaa", "chip_token": "tokA"}),
                   ("POSTJ", "/api/agent/journal"): (200, {"ok": True})})
    for k, v in {"_link": fl, "_seen": None, "_chip": None, "_seen_key": None, "_seen_token": None,
                 "_seen_sig": None, "_seen_paths": [], "_pin_key": None, "_CHIP_PIN": None}.items():
        monkeypatch.setattr(mcp, k, v)
    return fl


def _chip_answer(link, **kw):
    link.answers[("GET", "/api/agent/chip")] = (200, {**CHIP[1], **kw})


class TestBridgeApplyDeclaresTheSeenChip:
    def test_a_chip_switch_after_looking_is_refused_before_the_door(self, link):
        mcp.t_tray({})
        _chip_answer(link, chip_key="chip-bbbb", chip_token="tokB", pending=1)
        r = mcp.t_apply_to_live({})
        assert r["applied"] is False and r["refused"]["conflict"] == "chip_mismatch"
        assert not any(c[1] == "/state/apply-to-live" for c in link.calls)
        assert mcp._seen is None, "must look again"

    def test_the_press_carries_the_seen_chip_not_a_fresh_read(self, link):
        mcp.t_tray({})
        # same key (a reload of the same folder), a moved token: the SEEN token is declared
        _chip_answer(link, chip_key="chip-aaaa", chip_token="tokMoved", pending=1)

        def _apply(d):
            _chip_answer(link, chip_key="chip-aaaa", pending=0)
            return 200, "<div/>"
        link.answers[("POST", "/state/apply-to-live")] = _apply
        r = mcp.t_apply_to_live({})
        sent = [c for c in link.calls if c[1] == "/state/apply-to-live"][-1][2]
        assert sent["expect_chip"] == "tokA" and sent["expect_chip_key"] == "chip-aaaa"
        assert sent["seen_sig"] == "s1" and sent["seen_changes"] == 1
        assert r["applied"] is True and r["wrote"] == ["a"]


    def test_a_server_chip_refusal_is_said_as_a_chip_switch(self, link):
        """A 409 chip_mismatch from the door is not 'a human edited the chip'."""
        mcp.t_tray({})
        _chip_answer(link, chip_key="chip-aaaa", chip_token="tokA", pending=1)
        link.answers[("POST", "/state/apply-to-live")] = (
            409, {"ok": False, "chip_mismatch": True, "loaded_chip": "other", "error": "x"})
        r = mcp.t_apply_to_live({})
        assert r["applied"] is False and r["refused"]["chip_mismatch"] is True
        assert "another chip" in r["how"] and "human edited" not in r["how"]
        assert mcp._seen is None


class TestBridgeUndo:
    def test_a_server_refusal_is_returned_as_data(self, link):
        link.answers[("POST", "/undo")] = (409, {"ok": False, "refused": "journal", "message": "m"})
        r = mcp.t_undo({})
        assert r["undone"] is False and r["refused"]["refused"] == "journal" and "undo_mine" in r["how"]

    def test_undo_mine_without_a_new_picture_must_look_again(self, link):
        mcp.t_tray({})
        assert mcp._seen == 1
        link.answers[("POSTJ", "/api/agent/undo-mine")] = (200, {"ok": True, "reverted": ["a"], "pending": 0})
        link.answers[("GET", "/api/agent/tray")] = (500, {"ok": False, "error": "boom"})
        mcp.t_undo_mine({})
        assert mcp._seen is None, "a count with no chip behind it is not a picture"


class TestBridgeStagesTheWholeBundle:
    def test_a_comp_answer_returns_and_journals_every_row_of_its_group(self, link):
        """A-03, bridge side: the FSP and its amps are ONE group; the agent is
        told about, and the journal records, every row -- not just the last."""
        link.answers[("POST", "/field/edit")] = (200, {"ok": True, "group_id": "grp7"})
        link.answers[("GET", "/api/agent/tray")] = (200, {
            "ok": True, "count": 4, "seen_changes": 4, "seen_sig": "s4", "chip_key": "chip-aaaa",
            "chip_token": "tokA", "entries": [
                {"path": "other", "old": 1, "new": 2, "group": None},
                {"path": "fsp", "old": 0, "new": -6, "group": "grp7"},
                {"path": "amp1", "old": 0.1, "new": 0.2, "group": "grp7"},
                {"path": "amp2", "old": 0.3, "new": 0.6, "group": "grp7"}]})
        r = mcp.t_state_edit({"path": "fsp", "value": "-6", "fsp_ack": "comp", "reason": "r"})
        assert r["staged"] is True and [e["path"] for e in r["entries"]] == ["fsp", "amp1", "amp2"]
        assert r["entry"]["path"] == "fsp"
        journaled = [c[2]["paths"] for c in link.calls if c[1] == "/api/agent/journal"]
        assert journaled == [["fsp"], ["amp1"], ["amp2"]]
        sent = [c for c in link.calls if c[1] == "/field/edit"][-1][2]
        assert sent["fsp_ack"] == "comp"


class TestServerTwinOfTheCompRows:
    def test_the_rows_go_as_text_like_the_popup(self):
        """app.js ``_fspCompUpdates`` sends ``String(v)``; the twin sends the
        same digits, as text, so the batch parses them on the popup's path."""
        from quam_state_manager.web.routes import fsp_comp_updates
        plan = {"amps": [{"path": "a.amplitude", "new": 0.1 * 10 ** 0.3},
                         {"path": "b.amplitude", "new": 1e-05}, {"new": 0.5}]}
        ups = fsp_comp_updates(plan)
        assert [u["dot_path"] for u in ups] == ["a.amplitude", "b.amplitude"], "a row with no path is never written"
        assert all(isinstance(u["value"], str) for u in ups)
        assert [float(u["value"]) for u in ups] == [0.1 * 10 ** 0.3, 1e-05], "round-trips exactly"


class TestPinIsAnIdentity:
    def _pin(self, monkeypatch, pin):
        monkeypatch.setattr(mcp, "_CHIP_PIN", pin)

    def test_a_folder_name_pin_latches_the_first_chip(self, link, monkeypatch):
        self._pin(monkeypatch, "chip")
        _chip_answer(link, name="chip", chip_key="chip-aaaa", declared_name=None)
        assert mcp._chip_facts()["chip_key"] == "chip-aaaa"
        # a second fridge whose folder is ALSO called chip
        _chip_answer(link, name="chip", chip_key="chip-bbbb", declared_name=None, pin="chip-bbbb")
        with pytest.raises(mcp.ToolError) as ei:
            mcp._chip_facts()
        body = json.loads(str(ei.value))
        assert body["refused"] == "chip_mismatch" and body["pinned_chip_key"] == "chip-aaaa"
        assert body["pin_for_open_chip"] == "chip-bbbb"

    def test_the_display_name_pin_sm_writes_itself_still_works_and_latches(self, link, monkeypatch):
        """SM's in-app session pins the DISPLAY name; a chip that also declares
        extras.chip_name must still answer it -- and stay the only chip that does."""
        self._pin(monkeypatch, "chip")
        _chip_answer(link, name="chip", chip_key="rA-1", declared_name="arbel")
        assert mcp._chip_facts()["chip_key"] == "rA-1"
        _chip_answer(link, name="chip", chip_key="alt-2", declared_name="otherfridge")
        with pytest.raises(mcp.ToolError):
            mcp._chip_facts()
        _chip_answer(link, name="chip", chip_key="rA-1", declared_name="arbel")
        assert mcp._chip_facts(), "back on its own chip, the bridge works again"

    def test_the_declared_name_and_the_chip_key_both_match(self, link, monkeypatch):
        self._pin(monkeypatch, "arbel")
        _chip_answer(link, name="chip", chip_key="chip-aaaa", declared_name="arbel")
        assert mcp._chip_facts()
        monkeypatch.setattr(mcp, "_pin_key", None)
        self._pin(monkeypatch, "chip-aaaa")
        assert mcp._chip_facts()
        _chip_answer(link, name="chip", chip_key="chip-bbbb", declared_name="arbel")
        with pytest.raises(mcp.ToolError):
            mcp._chip_facts()
