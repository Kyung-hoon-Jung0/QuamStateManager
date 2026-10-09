"""docs/252: an agent cannot loosen its own guardrails, and SM can say who pressed.

Findings (agent validation campaign, 2026-10-03): A-09, A-10 / D-07, D-08 /
C-15, B-01, B-04, B-10. The person-proof pins run with ``PROVE_CALLERS`` on
(the test client is otherwise exempt, like the CSRF guard); one pin builds a
production app to show the proof is on by default there.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from quam_state_manager import mcp
from quam_state_manager.core import agent_link, agent_session, entity_notes, journal, limits, story
from quam_state_manager.web import callers
from quam_state_manager.web.app import create_app

_H = {"Origin": "http://localhost"}
user_c = {**_H, "X-SM-Actor": "user-c"}
CLAUDE = {**_H, "X-SM-Agent": "claude"}


@pytest.fixture
def chip(tmp_path):
    from tests.test_web import _make_state, _make_wiring
    d = tmp_path / "chip"
    d.mkdir()
    (d / "state.json").write_text(json.dumps(_make_state(), indent=2), encoding="utf-8")
    (d / "wiring.json").write_text(json.dumps(_make_wiring(), indent=2), encoding="utf-8")
    return d


@pytest.fixture
def app(tmp_path):
    a = create_app(testing=True, instance_path=str(tmp_path / "inst"))
    a.config["PROVE_CALLERS"] = True
    return a


@pytest.fixture
def c(app, chip):
    cl = app.test_client()
    cl.post("/load", data={"folder": str(chip)})
    return cl


def _key(c):
    return c.get("/api/agent/chip").get_json()["chip_key"]


def _name(c):
    return c.get("/api/agent/chip").get_json()["name"]


def _window(c):
    """What a person's browser does first: load a page."""
    r = c.get("/journal")
    assert r.status_code == 200 and r.mimetype == "text/html"
    return r


# ------------------------------------------------------- the person's window

class TestTheWindowsProof:
    def test_a_page_hands_the_window_its_proof(self, app, c):
        r = _window(c)
        set_cookie = r.headers.get("Set-Cookie") or ""
        assert set_cookie.startswith("sm_person=" + app.config["SM_PERSON_SECRET"]), set_cookie
        assert "HttpOnly" in set_cookie and "SameSite=Strict" in set_cookie and "Path=/" in set_cookie
        fresh = app.test_client()                    # holds no cookie yet, so an answer WOULD carry one
        assert "Set-Cookie" not in fresh.get("/api/agent/chip").headers, "a JSON answer never carries the proof"
        assert "Set-Cookie" not in fresh.get("/journal", headers={"X-SM-Agent": "claude"}).headers, \
            "the bridge fetching a page is still the bridge"
        assert "Set-Cookie" in fresh.get("/journal").headers, "and the same page without the header does"

    def test_the_cookie_is_named_per_port(self, app, c):
        r = c.get("/journal", base_url="http://127.0.0.1:5111")
        assert (r.headers.get("Set-Cookie") or "").startswith("sm_person_5111="), \
            "two SM windows share one cookie jar; each needs its own name"

    def test_curl_with_a_persons_name_but_no_window_cannot_press(self, app, c):
        """A-09: X-SM-Actor alone armed, approved and started."""
        key = _key(c)
        before_root = journal.root(app.instance_path)
        for method_path, body in (("/api/agent/session/arm", {}),
                                  ("/api/agent/approvals/nope/approve", {}),
                                  ("/api/agent/plans/nope/start", {}),
                                  ("/api/agent/plans/nope/cancel", {}),
                                  ("/api/agent/plans/nope/mode", {"mode": "auto"}),
                                  ("/api/agent/limits", {"mode": "auto"}),
                                  ("/api/agent/journal/root", {"root": str(Path(app.instance_path) / "elsewhere")}),
                                  ("/journal/claim", {"run_id": 1, "who": "user-c"}),
                                  ("/journal/adopt", {}),
                                  ("/api/agent/setup/journal", {"root": str(Path(app.instance_path) / "x")})):
            r = c.post(method_path, json=body, headers=user_c)
            assert r.status_code == 403 and r.get_json()["refused"] == "no_window_proof", method_path
        assert not (agent_session.load(app.instance_path, key) or {}).get("start_token")
        assert limits.load(app.instance_path, key)["mode"] == "ask-writes"
        assert journal.root(app.instance_path) == before_root
        assert not story.load_claims(app.instance_path, _name(c))

    def test_the_window_still_presses(self, app, c):
        _window(c)
        # docs/253: the session-wide Arm is gone -- the gate lets the person through and the route
        # itself answers that arming is per plan (a refused window would be 403 no_window_proof)
        r = c.post("/api/agent/session/arm", json={}, headers=user_c)
        assert r.status_code == 409 and r.get_json()["refused"] == "arm_is_per_plan", r.get_json()
        assert c.post("/api/agent/limits", json={"max_writes_per_plan": 20}, headers=user_c).status_code == 200
        # the gate let them through; the route answers for itself
        assert c.post("/api/agent/approvals/nope/approve", json={}, headers=user_c).status_code == 404
        assert c.post("/api/agent/plans/nope/start", json={}, headers=user_c).status_code == 404

    def test_a_proof_from_another_process_is_not_this_ones(self, app, c):
        c.set_cookie("sm_person", "a-secret-from-yesterdays-sm", domain="localhost")
        r = c.post("/api/agent/session/arm", json={}, headers=user_c)
        assert r.status_code == 403 and "reload the page" in r.get_json()["error"]
        c.set_cookie("sm_person_5112", app.config["SM_PERSON_SECRET"], domain="localhost")
        c.delete_cookie("sm_person", domain="localhost")
        assert c.post("/api/agent/session/arm", json={}, headers=user_c).status_code == 403, \
            "the other port's cookie is the other window's"

    @pytest.mark.parametrize("path,body", [
        ("/api/agent/session/arm", {}), ("/api/agent/plans/nope/cancel", {}),
        ("/api/agent/plans/nope/start", {}), ("/api/agent/approvals/nope/reject", {}),
        ("/api/agent/limits", {"mode": "auto"}), ("/api/agent/journal/root", {"root": ""}),
        ("/journal/claim", {"run_id": 1, "who": "user-c"}), ("/api/agent/setup/connect", {"backend": "claude"}),
        ("/api/agent/setup/context", {"answers": {}}),
    ])
    def test_an_agent_with_the_cookie_is_still_an_agent(self, c, path, body):
        _window(c)
        r = c.post(path, json=body, headers={**CLAUDE, "X-SM-Actor": "user-c"})
        assert r.status_code == 403 and r.get_json()["refused"] == "person_only", path

    def test_every_gated_endpoint_exists_and_takes_a_post(self, app):
        """A renamed view would silently fall out of the gate."""
        rules = {r.endpoint: r.methods for r in app.url_map.iter_rules()}
        for ep in list(callers.PERSON_ONLY) + list(callers.HOOK_ONLY):
            assert ep in rules and "POST" in rules[ep], ep

    def test_a_production_app_proves_by_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SM_DISABLE_ENV_WARMUP", "1")
        a = create_app(testing=False, instance_path=str(tmp_path / "prod"))
        cl = a.test_client()
        h = {"Origin": "http://localhost", "X-SM-Actor": "user-c"}
        assert cl.post("/api/agent/limits", json={"mode": "auto"}, headers=h).status_code == 403
        cl.get("/journal", headers={"Origin": "http://localhost"})
        r = cl.post("/api/agent/limits", json={"mode": "ask-all"}, headers=h)
        assert r.status_code == 200 and r.get_json()["limits"]["mode"] == "ask-all"


class TestTheAgentHeaderIsRefusedEvenWithoutProofChecks:
    """Without PROVE_CALLERS (every older test's client), the agent header is
    still refused on the person's routes that had no check of their own."""

    @pytest.mark.parametrize("path,body", [
        ("/api/agent/plans/nope/cancel", {}), ("/journal/claim", {"run_id": 1, "who": "user-c"}),
        ("/api/agent/journal/root", {"root": ""}), ("/api/agent/limits", {"mode": "auto"}),
        ("/api/agent/setup/journal", {"root": "x"}), ("/journal/adopt", {}),
    ])
    def test_refused(self, tmp_path, chip, path, body):
        cl = create_app(testing=True, instance_path=str(tmp_path / "inst")).test_client()
        cl.post("/load", data={"folder": str(chip)})
        assert cl.post(path, json=body, headers=CLAUDE).status_code == 403, path


# ------------------------------------------------------------ the guardrails

class TestLimitsAreThePersons:
    SECRET_HOOK = "https://hooks.slack.com/services/T000/B000/s3cr3tT0ken"

    def test_an_agent_trying_to_loosen_its_limits_is_refused_and_journaled(self, app, c):
        """A-10 / D-07: mode auto, human_recent_min 0 and a webhook, from the agent."""
        key, name = _key(c), _name(c)
        r = c.post("/api/agent/limits", json={"mode": "auto", "human_recent_min": 0, "max_writes_per_plan": 0,
                                              "webhook_url": self.SECRET_HOOK}, headers=CLAUDE)
        assert r.status_code == 403
        lim = limits.load(app.instance_path, key)
        assert lim["mode"] == "ask-writes" and lim["human_recent_min"] == 30 and lim["webhook_url"] == ""
        text = journal.read(app.instance_path, name)
        assert "by_claude tried changing the limits" in text and "mode -> auto" in text \
            and "human_recent_min -> 0" in text and "refused" in text
        assert "s3cr3tT0ken" not in text and "https://hooks.slack.com/..." in text

    def test_every_change_is_journaled_with_who_old_and_new(self, app, c):
        _window(c)
        name = _name(c)
        r = c.post("/api/agent/limits", json={"human_recent_min": 5, "max_writes_per_plan": 50,
                                              "webhook_url": self.SECRET_HOOK, "mode": "auto"}, headers=user_c)
        assert r.status_code == 200
        text = journal.read(app.instance_path, name)
        assert "mode ask-writes -> auto (set by human:user-c)" in text
        assert ("limits max_writes_per_plan 200 -> 50; human_recent_min 30 -> 5; "
                "webhook_url (none) -> https://hooks.slack.com/... (set by human:user-c)") in text, text
        assert "s3cr3tT0ken" not in text, "a webhook URL carries its own secret; the journal is a person's vault"
        c.post("/api/agent/limits", json={"human_recent_min": 5}, headers=user_c)
        assert journal.read(app.instance_path, name).count("human_recent_min") == 1, "no change, no line"
        c.post("/api/agent/limits", json={"webhook_url": "https://bot:t0kenInUserinfo@hooks.example:8443/x"}, headers=user_c)
        text = journal.read(app.instance_path, name)
        assert "t0kenInUserinfo" not in text and "-> https://hooks.example:8443/..." in text


# ------------------------------------------------------------- the journal

def _line(inst, chip, text, day):
    journal.append(inst, chip, text, kind="human", when=datetime.strptime(day + " 09:00:00", "%Y-%m-%d %H:%M:%S"))


class TestTheJournalMovesWithItsHistory:
    def test_a_move_carries_the_day_files_and_says_so_in_both_folders(self, app, c, tmp_path):
        """D-08 / C-15: the day files stayed behind and the log looked empty."""
        _window(c)
        inst, name = app.instance_path, _name(c)
        old = journal.root(inst)
        _line(inst, name, "yesterday's T1 looked low", "2026-10-01")
        _line(inst, name, "re-ran ramsey", "2026-10-02")
        _line(inst, "OtherChip", "other fridge", "2026-10-02")
        vault = tmp_path / "vault"
        r = c.post("/api/agent/journal/root", json={"root": str(vault)}, headers=user_c)
        assert r.status_code == 200 and Path(r.get_json()["root"]) == vault
        assert sorted(r.get_json()["carried"]) == sorted([journal._safe_key(name), "OtherChip"])
        assert "yesterday's T1 looked low" in journal.read(inst, name, "2026-10-01")
        assert "re-ran ramsey" in journal.read(inst, name, "2026-10-02")
        assert "other fridge" in journal.read(inst, "OtherChip", "2026-10-02")
        today = journal.read(inst, name)
        assert f"journal folder moved here from {old} by human:user-c" in today
        left = (old / journal._safe_key(name) / (datetime.now().strftime("%Y-%m-%d") + ".md")).read_text(encoding="utf-8")
        assert f"journal folder moved to {vault} by human:user-c" in left, "the old folder says where the log went"
        assert (old / journal._safe_key(name) / "2026-10-01.md").exists(), "copied, never moved away"
        page = c.get("/journal?day=2026-10-01").get_data(as_text=True)
        assert "looked low" in page, "the Calibration log shows the history after the move"

    def test_moving_back_duplicates_nothing(self, app, c, tmp_path):
        _window(c)
        inst, name = app.instance_path, _name(c)
        a = journal.root(inst)
        _line(inst, name, "line one", "2026-10-01")
        c.post("/api/agent/journal/root", json={"root": str(tmp_path / "b")}, headers=user_c)
        _line(inst, name, "line two, written in b", "2026-10-01")
        c.post("/api/agent/journal/root", json={"root": str(a)}, headers=user_c)
        text = journal.read(inst, name, "2026-10-01")
        assert text.count("line one") == 1 and text.count("line two, written in b") == 1, text

    def test_setup_moves_it_the_same_way(self, app, c, tmp_path):
        _window(c)
        inst, name = app.instance_path, _name(c)
        _line(inst, name, "from before setup", "2026-10-01")
        r = c.post("/api/agent/setup/journal", json={"root": str(tmp_path / "vault2")}, headers=user_c)
        assert r.status_code == 200 and r.get_json()["carried"]
        assert "from before setup" in journal.read(inst, name, "2026-10-01")

    def test_the_same_folder_again_is_not_a_move(self, app, c):
        _window(c)
        inst, name = app.instance_path, _name(c)
        c.post("/api/agent/journal/root", json={"root": ""}, headers=user_c)
        assert "moved" not in journal.read(inst, name)

    def test_an_agent_cannot_move_it(self, app, c, tmp_path):
        inst, name = app.instance_path, _name(c)
        before = journal.root(inst)
        r = c.post("/api/agent/journal/root", json={"root": str(tmp_path / "hidden")}, headers=CLAUDE)
        assert r.status_code == 403 and journal.root(inst) == before
        assert "by_claude tried moving the journal folder" in journal.read(inst, name)


class TestTheJournalDoorSignsLines:
    def test_an_agent_signs_with_its_own_name(self, app, c):
        for asked in ("sm", "human", "by_claude", "unknown"):
            r = c.post("/api/agent/journal", json={"text": f"line {asked}", "kind": asked, "reason": "r"},
                       headers={**_H, "X-SM-Agent": "codex"})
            assert r.status_code == 200 and r.get_json()["entry"]["kind"] == "by_codex", asked
        r = c.post("/api/agent/journal", json={"text": "applied 2 edit(s)", "kind": "sm"},
                   headers={**_H, "X-SM-Agent": "claude"})
        assert r.status_code == 200 and r.get_json()["entry"]["kind"] == "by_claude", \
            "the bridge's bookkeeping of its own acts needs no reason, and is the agent's"

    def test_no_header_and_no_window_is_unverified(self, app, c):
        r = c.post("/api/agent/journal", json={"text": "forged", "kind": "by_claude"}, headers=_H)
        assert r.status_code == 200 and r.get_json()["entry"]["kind"] == "unverified"
        assert "`unverified` forged" in journal.read(app.instance_path, _name(c))

    def test_an_agent_cannot_claim_a_run_for_a_person(self, app, c):
        """A-09: with the agent header, /journal/claim said "run by human:user-c"."""
        r = c.post("/journal/claim", json={"run_id": 7, "who": "user-c"}, headers=CLAUDE)
        assert r.status_code == 403 and not story.load_claims(app.instance_path, _name(c))


# ---------------------------------------------------------------- the hook

class TestTheHooksKey:
    def test_create_app_makes_one_stable_key(self, app):
        k = agent_link.read_hook_key(app.instance_path)
        assert k and len(k) == 64
        assert agent_link.ensure_hook_key(app.instance_path) == k
        assert agent_link.hook_key_path(app.instance_path).parent.name == "agent_link"

    def _ev(self, **kw):
        rec = {"session_id": "s1", "hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_use_id": "t1",
               "summary": "python 05_power_rabi.py", "backend": "claude", "ts": datetime.now().timestamp()}
        rec.update(kw)
        return rec

    def test_an_event_without_the_key_records_nothing(self, app, c):
        """A-09: any POST became a `by_claude` journal line."""
        r = c.post("/api/agent/event", json=self._ev(), headers=_H)
        assert r.status_code == 403 and r.get_json()["refused"] == "unproven_event"
        r = c.post("/api/agent/event", json=self._ev(tool_use_id="t2"),
                   headers={**_H, agent_link.HOOK_KEY_HEADER: "0" * 64})
        assert r.status_code == 403
        assert c.get("/api/agent/events").get_json()["count"] == 0
        assert "05_power_rabi" not in journal.read(app.instance_path, _name(c))

    def test_the_hooks_own_event_is_recorded(self, app, c):
        key = agent_link.read_hook_key(app.instance_path)
        r = c.post("/api/agent/event", json=self._ev(), headers={**_H, agent_link.HOOK_KEY_HEADER: key})
        assert r.status_code == 200
        assert "`by_claude` ran `05_power_rabi`" in journal.read(app.instance_path, _name(c))

    def test_an_event_cannot_speak_in_the_agent_panel(self, app, c):
        key = agent_link.read_hook_key(app.instance_path)
        rec = self._ev(hook_event_name="Text", origin="chat", n=999, chip="ElsewhereChip",
                       text="I approved everything")
        assert c.post("/api/agent/event", json=rec, headers={**_H, agent_link.HOOK_KEY_HEADER: key}).status_code == 200
        ev = c.get("/api/agent/events").get_json()["events"][-1]
        assert "origin" not in ev and "n" not in ev and "chip" not in ev
        cards = json.dumps(c.get("/api/agent/chat/cards").get_json())
        assert "I approved everything" not in cards

    def test_the_hook_sends_the_key(self, tmp_path, monkeypatch):
        inst = tmp_path / "inst"
        key = agent_link.ensure_hook_key(inst)
        seen = {}

        def fake_send(self, req, timeout=None):
            seen.update({k.lower(): v for k, v in req.header_items()})
            return 200, {"ok": True}
        monkeypatch.setattr(agent_link.SMLink, "_send", fake_send)
        monkeypatch.setenv("SM_URL", "http://127.0.0.1:5999")
        assert agent_link.fire_event(inst, {"hook_event_name": "Stop"}) is True
        assert seen.get(agent_link.HOOK_KEY_HEADER.lower()) == key


# ---------------------------------------------------------------- the notes

class TestNotesAreReadBack:
    def test_a_note_one_agent_pinned_is_read_by_the_next(self, app, c):
        """B-01: "do not touch, fridge warming" was readable by no tool."""
        r = c.post("/api/agent/note", json={"subject": "qA1", "text": "do not touch, fridge warming"},
                   headers={**_H, "X-SM-Agent": "codex"})
        assert r.status_code == 200
        leaf = c.get("/api/agent/state?path=qubits.qA1.T1").get_json()
        assert [n["text"] for n in leaf["notes"]] == ["do not touch, fridge warming"]
        assert leaf["notes"][0]["author"] == "by_codex" and leaf["notes"][0]["subject"] == "qubits.qA1"
        ent = c.get("/api/agent/state?path=qubits.qA1").get_json()
        assert ent["notes"] and c.get("/api/agent/state").get_json()["notes"]
        other = c.get("/api/agent/state?path=qubits").get_json()
        assert other["notes"], "a note under the subtree asked for is shown"
        assert c.get("/api/agent/state?path=qubit_pairs.qA1-A2").get_json()["notes"] == [], \
            "a note on qA1 is not a note on another entity"
        allnotes = c.get("/api/agent/notes").get_json()
        assert allnotes["count"] == 1 and allnotes["notes"][0]["text"] == "do not touch, fridge warming"
        p = c.post("/api/agent/note", json={"subject": "qA1-A2", "text": "coupler drifts"},
                   headers={**_H, "X-SM-Agent": "claude"}).get_json()["note"]
        assert p["subject"] == "qubit_pairs.qA1-A2" and p["author"] == "by_claude"

    def test_a_bare_name_note_written_before_the_fix_is_still_found(self, app, c):
        entity_notes.save(app.instance_path, c.get("/api/agent/chip").get_json()["path"], "qA1",
                          "old bare-name note", author="claude-code")
        notes = c.get("/api/agent/state?path=qubits.qA1.T1").get_json()["notes"]
        assert [n["text"] for n in notes] == ["old bare-name note"] and "orphan" not in notes[0]


@pytest.fixture
def link(monkeypatch):
    from tests.test_mcp_bridge import FakeLink
    fl = FakeLink({("GET", "/api/agent/chip"): (200, {"ok": True, "loaded": True, "name": "c", "pending": 0}),
                   ("GET", "/api/agent/notes"): (200, {"ok": True, "notes": [{"subject": "qubits.q1",
                                                                              "text": "fridge warming"}]}),
                   ("POSTJ", "/api/agent/note"): (200, {"ok": True})})
    monkeypatch.setattr(mcp, "_link", fl)
    monkeypatch.setattr(mcp, "_chip", None)
    monkeypatch.setattr(mcp, "_pin_key", None)
    return fl


class TestTheBridge:
    def test_sm_status_carries_the_notes(self, link):
        assert mcp.t_sm_status({})["notes"] == [{"subject": "qubits.q1", "text": "fridge warming"}]

    def test_sm_status_survives_an_sm_without_notes(self, link):
        del link.answers[("GET", "/api/agent/notes")]
        out = mcp.t_sm_status({})
        assert out["loaded"] and "notes" not in out

    def test_note_set_claims_no_author(self, link):
        mcp.t_note_set({"subject": "q1", "text": "t"})
        body = [x for x in link.calls if x[1] == "/api/agent/note"][-1][2]
        assert "author" not in body, "SM signs the note from the bridge's header"

    def test_the_server_instructions_carry_the_lab_rules(self, capsys):
        mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"clientInfo": {"name": "codex"}}})
        ins = json.loads(capsys.readouterr().out)["result"]["instructions"]
        assert "Never edit state.json or wiring.json directly" in ins
        assert "never run `python <node>.py` yourself" in ins and "notes" in ins

    def test_the_manual_setup_names_the_tool_timeout(self):
        """B-10: Codex gives up on a tool after 300 s; run_node can wait ~28 min."""
        doc = mcp.__doc__
        assert "tool_timeout_sec = 1800" in doc and "MCP_TOOL_TIMEOUT=1800000" in doc
        assert mcp._WAIT_CAP_S < 1800, "the cap stays under the timeout the doc tells people to set"
