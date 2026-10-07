"""docs/297: the Agent panel's conversation can be cleared -- archived or deleted.

The routes are driven for real (the real ``ChatManager`` over the fake CLI, the
same fixtures as ``test_chat_api``). What a clear must do: end the in-app
session and forget its id (the next message is a fresh context), start the
panel's feed over in every window, keep a read-only copy only when asked, and
never touch the work or the audit record -- running plans and runs, approvals,
the Calibration log, the agent event log.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from quam_state_manager.core import agent_conversation as conv_mod
from quam_state_manager.core import agent_plans, agent_session, approvals
from quam_state_manager.web import chat_api
from tests.test_chat_api import (  # noqa: F401  (fixtures)
    _chip, _events, _journal, _name, _texts, _wait, app, c, fakes, inst, synth_folder,
)


_STEPS = [{"node": "n", "targets": ["q1"]}]


def _cards(c, after=0):
    return c.get(f"/api/agent/chat/cards?after={after}").get_json()


def _talk(c, text):
    before = len(_texts(c))
    r = c.post("/api/agent/chat/start", json={"prompt": text}).get_json()
    assert r["ok"], r
    assert _wait(lambda: len(_texts(c)) > before)
    assert _wait(lambda: not c.get("/api/agent/chat/status").get_json()["session"]["busy"])
    return r


class TestClear:
    def test_archive_and_clear_starts_over_and_keeps_a_copy(self, c, inst):
        _talk(c, "first question")
        before = _cards(c)
        assert [k["kind"] for k in before["cards"] if k["kind"] in ("user", "answer")] == ["user", "answer"]
        assert before["conversation"] == {"since_n": 0, "archives": 0, "resumable": True}

        r = c.post("/api/agent/chat/clear", json={"keep": 1})
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        assert body["kept"] is True and body["ended"] is True
        assert body["archive"]["title"] == "first question" and body["archive"]["messages"] == 2

        after = _cards(c)
        assert after["cards"] == [], "the feed starts over"
        assert after["conversation"]["since_n"] == body["since_n"] > 0
        assert after["conversation"]["archives"] == 1
        assert after["conversation"]["resumable"] is False
        assert after["last"] >= body["since_n"], "a window's cursor jumps past the cleared conversation"
        assert _events(c) == [], "/events follows the clear too"
        # the agent forgets it: nothing to resume
        assert agent_session.load(str(inst), _chip(c))["session_id"] is None
        assert c.get("/api/agent/chat/status").get_json()["resumable"] is False
        assert c.post("/api/agent/chat/start", json={"prompt": "x", "resume": "last"}).status_code == 409
        assert "agent conversation cleared by human (2 messages archived); claude session ended" in _journal(c, inst)

        lst = c.get("/api/agent/chat/archives").get_json()["archives"]
        assert [a["id"] for a in lst] == [body["archive"]["id"]]
        got = c.get(f"/api/agent/chat/archives/{lst[0]['id']}").get_json()
        assert got["omitted"] == 0
        texts = [(k["kind"], k.get("text")) for k in got["cards"] if k["kind"] in ("user", "answer")]
        assert texts == [("user", "first question"), ("answer", "answer 1: first question")]

        # a new conversation is a fresh context with its own cards
        r2 = _talk(c, "second question")
        assert r2["resumed"] is False, "started without --resume"
        now = _cards(c)
        users = [k["text"] for k in now["cards"] if k["kind"] == "user"]
        assert users == ["second question"]

    def test_a_second_clear_archives_only_the_second_conversation(self, c):
        _talk(c, "one")
        a1 = c.post("/api/agent/chat/clear", json={"keep": 1}).get_json()["archive"]
        _talk(c, "two")
        a2 = c.post("/api/agent/chat/clear", json={"keep": 1}).get_json()["archive"]
        assert a2["from_n"] == a1["to_n"] and a2["to_n"] > a1["to_n"]
        lst = c.get("/api/agent/chat/archives").get_json()["archives"]
        assert [a["title"] for a in lst] == ["two", "one"], "newest first"
        got = c.get(f"/api/agent/chat/archives/{a2['id']}").get_json()
        assert [k["text"] for k in got["cards"] if k["kind"] == "user"] == ["two"]

    def test_delete_and_clear_keeps_no_copy_and_never_edits_the_event_log(self, c, inst):
        _talk(c, "forget me")
        r = c.post("/api/agent/chat/clear", json={"keep": 0}).get_json()
        assert r["ok"] and r["kept"] is False and r["archive"] is None
        assert c.get("/api/agent/chat/archives").get_json()["archives"] == []
        assert _cards(c)["cards"] == []
        day = inst / "agent_events" / (datetime.now().strftime("%Y-%m-%d") + ".jsonl")
        assert "forget me" in day.read_text(encoding="utf-8"), "the audit record is not edited"
        assert "(not kept)" in _journal(c, inst)

    def test_clear_with_no_session_and_nothing_said_is_harmless(self, c):
        r = c.post("/api/agent/chat/clear", json={"keep": 1}).get_json()
        assert r["ok"] and r["kept"] is False and r["ended"] is False
        assert c.get("/api/agent/chat/archives").get_json()["archives"] == []

    def test_another_window_sees_the_new_epoch(self, c):
        _talk(c, "hello")
        seen = _cards(c)
        cursor = seen["last"]
        c.post("/api/agent/chat/clear", json={"keep": 1})
        again = _cards(c, after=cursor)
        assert again["conversation"]["since_n"] != seen["conversation"]["since_n"], \
            "the epoch a window compares to start its feed over"
        assert again["cards"] == []


class TestPill:
    """The strip said "thinking" for the event window's whole 15 minutes after a clear: the
    clear forgets the session id the record held, which was how SM knew the session was its own."""

    def test_after_a_clear_the_agent_is_not_thinking(self, c):
        _talk(c, "hi")
        assert _cards(c)["now"]["state"] == "between", "precondition: an open session between turns"
        c.post("/api/agent/chat/clear", json={"keep": 1})
        assert _wait(lambda: not c.get("/api/agent/chat/status").get_json()["session"]["alive"])
        assert _cards(c)["now"]["state"] == "idle"

    def test_after_end_session_the_agent_is_not_thinking(self, c):
        _talk(c, "hi")
        c.post("/api/agent/chat/end")
        assert _wait(lambda: not c.get("/api/agent/chat/status").get_json()["session"]["alive"])
        assert _cards(c)["now"]["state"] == "idle"

    def test_after_a_restart_the_closed_list_says_it(self, c, app):
        _talk(c, "hi")
        c.post("/api/agent/chat/clear", json={"keep": 1})
        assert _wait(lambda: not c.get("/api/agent/chat/status").get_json()["session"]["alive"])
        app.config["agent_chat"].sessions.clear()            # what a restart leaves: no manager session
        assert _cards(c)["now"]["state"] == "idle"


class TestRefusals:
    def test_refused_while_the_agent_is_answering(self, c, monkeypatch):
        monkeypatch.setenv("FAKE_SLOW", "4")
        c.post("/api/agent/chat/start", json={"prompt": "slow one"})
        assert _wait(lambda: c.get("/api/agent/chat/status").get_json()["session"]["busy"])
        r = c.post("/api/agent/chat/clear", json={"keep": 1})
        assert r.status_code == 409 and "still answering" in r.get_json()["error"]
        assert [k["text"] for k in _cards(c)["cards"] if k["kind"] == "user"] == ["slow one"], "nothing was cleared"

    def test_refused_while_a_plan_runs(self, c, inst):
        key = _chip(c)
        p = agent_plans.add(str(inst), key, title="night run", steps=_STEPS, mode=None, created_by="human")
        agent_plans.update(str(inst), key, p["id"], status="running")
        r = c.post("/api/agent/chat/clear", json={"keep": 1})
        assert r.status_code == 409 and "night run" in r.get_json()["error"]

    def test_refused_while_another_session_is_alive(self, c, inst):
        import os
        agent_session.save(str(inst), _chip(c), backend="claude", pid=os.getpid(), owner="someone")
        r = c.post("/api/agent/chat/clear", json={"keep": 1})
        assert r.status_code == 409 and "alive on this chip" in r.get_json()["error"]

    def test_refused_while_a_question_is_being_answered(self, c, monkeypatch):
        monkeypatch.setenv("FAKE_SLOW", "4")
        r = c.post("/api/agent/chat/ask", json={"text": "slow question", "feed": 1}).get_json()
        assert r["ok"]
        r = c.post("/api/agent/chat/clear", json={"keep": 1})
        assert r.status_code == 409 and "question is still being answered" in r.get_json()["error"]

    def test_refused_while_a_run_is_running(self, c, app):
        key = _chip(c)
        with app.app_context():
            from quam_state_manager.web import agent_api as aa
            aa._registry().runs["rk-1"] = {"key": "rk-1", "chip": key, "status": "running", "node": "05_power_rabi",
                                           "targets": ["q1"], "since": time.time()}
        r = c.post("/api/agent/chat/clear", json={"keep": 1})
        assert r.status_code == 409 and "05_power_rabi" in r.get_json()["error"]

    def test_an_unreadable_index_is_never_rewritten_and_nothing_is_ended(self, c, inst):
        _talk(c, "keep talking")
        p = conv_mod.index_path(str(inst), _name(c))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"{not json")
        r = c.post("/api/agent/chat/clear", json={"keep": 1})
        assert r.status_code == 500 and "cannot be read" in r.get_json()["error"]
        assert p.read_bytes() == b"{not json"
        st = c.get("/api/agent/chat/status").get_json()
        assert st["session"]["ended"] is None and st["resumable"] is True, "a refused clear ends nothing"


class TestWhatStays:
    def test_live_work_and_approvals_stay_finished_plans_go(self, c, inst):
        key = _chip(c)
        done = agent_plans.add(str(inst), key, title="old done", steps=_STEPS, mode=None, created_by="human")
        agent_plans.update(str(inst), key, done["id"], status="done", created=time.time() - 60)
        draft = agent_plans.add(str(inst), key, title="still a draft", steps=_STEPS, mode=None, created_by="human")
        ap = approvals.add(str(inst), key, kind="writes", node="n", targets=["q1"],
                           writes=[{"path": "qubits.q1.f_01", "old": 1.0, "new": 2.0}], reason="r",
                           why_held="ask-writes", actor="agent")
        live = _cards(c)["live"]
        assert {p["id"] for p in live["plans"]} == {done["id"], draft["id"]}
        assert c.post("/api/agent/chat/clear", json={"keep": 1}).status_code == 200
        live = _cards(c)["live"]
        assert [p["id"] for p in live["plans"]] == [draft["id"]], "a draft waiting for Start is live work"
        assert [a["id"] for a in live["approvals"]] == [ap["id"]], "an approval still waits for a decision"
        assert agent_plans.get(str(inst), key, done["id"]) is not None, "the plan record itself is kept"

    def test_a_finished_run_goes_with_the_conversation(self, c, app):
        key = _chip(c)
        with app.app_context():
            from quam_state_manager.web import agent_api as aa
            aa._registry().runs["rk-old"] = {"key": "rk-old", "chip": key, "status": "ended", "node": "n",
                                             "targets": ["q1"], "since": time.time() - 60, "result": {}}
        assert [r["key"] for r in _cards(c)["live"]["runs"]] == ["rk-old"]
        assert c.post("/api/agent/chat/clear", json={"keep": 1}).status_code == 200
        assert _cards(c)["live"]["runs"] == []

    def test_a_plan_made_after_the_clear_is_shown_once_finished(self, c, inst):
        key = _chip(c)
        c.post("/api/agent/chat/clear", json={"keep": 0})
        p = agent_plans.add(str(inst), key, title="after", steps=_STEPS, mode=None, created_by="human")
        agent_plans.update(str(inst), key, p["id"], status="done")
        assert [x["id"] for x in _cards(c)["live"]["plans"]] == [p["id"]]

    def test_the_closed_sessions_trailing_events_stay_out(self, c, app):
        _talk(c, "hi")
        sid = c.get("/api/agent/chat/status").get_json()["session"]["session_id"]
        c.post("/api/agent/chat/clear", json={"keep": 1})
        with app.app_context():
            chat_api._record({"ts": time.time(), "hook_event_name": "Text", "origin": "chat", "chip": _name(c),
                              "text": "a late line from the ended process", "session_id": sid})
            chat_api._record({"ts": time.time(), "hook_event_name": "Text", "origin": "chat", "chip": _name(c),
                              "text": "a line of the next conversation", "session_id": None})
        texts = [k.get("text") for k in _cards(c)["cards"]]
        assert texts == ["a line of the next conversation"]


class TestReviewRound:
    """docs/297 review: each finding reproduced first, then fixed."""

    def test_a_failed_write_changes_nothing(self, c, inst, monkeypatch):
        _talk(c, "keep me")
        before = _cards(c)

        def boom(*a, **k):
            raise OSError(28, "No space left on device")
        monkeypatch.setattr(conv_mod, "_write", boom)
        r = c.post("/api/agent/chat/clear", json={"keep": 1})
        assert r.status_code == 500 and r.is_json and "nothing was cleared" in r.get_json()["error"]
        st = c.get("/api/agent/chat/status").get_json()
        assert st["session"]["ended"] is None and st["resumable"] is True, "the session was not ended"
        assert _cards(c)["cards"] == before["cards"] and "conversation cleared" not in _journal(c, inst)
        d = conv_mod.index_path(str(inst), _name(c)).parent
        assert not list(d.glob("*.jsonl")), "no orphan archive file"

    def test_a_draft_kept_at_the_clear_stays_once_it_finishes(self, c, inst):
        key = _chip(c)
        draft = agent_plans.add(str(inst), key, title="kept draft", steps=_STEPS, mode=None, created_by="human")
        c.post("/api/agent/chat/clear", json={"keep": 0})
        agent_plans.update(str(inst), key, draft["id"], status="done", ended=time.time() + 1)
        assert [p["id"] for p in _cards(c)["live"]["plans"]] == [draft["id"]]

    def test_a_session_cleared_from_another_process_cannot_go_on(self, c, inst):
        _talk(c, "hello")
        # another SM process on this instance clears: it cannot end THIS process's session
        time.sleep(0.05)
        conv_mod.clear(str(inst), _name(c), since_n=10_000, events=[], who="other window", keep=False)
        r = c.post("/api/agent/chat/send", json={"text": "more"})
        assert r.status_code == 409 and r.get_json().get("cleared") is True
        st = c.get("/api/agent/chat/status").get_json()["session"]
        assert st["ended"] is not None, "the cleared conversation is ended here too"

    def test_a_cleared_session_id_is_never_resumed(self, c):
        _talk(c, "x")
        sid = c.get("/api/agent/chat/status").get_json()["session"]["session_id"]
        c.post("/api/agent/chat/clear", json={"keep": 0})
        r = c.post("/api/agent/chat/start", json={"prompt": "y", "resume": sid})
        assert r.status_code == 409 and "was cleared" in r.get_json()["error"]

    def test_the_counter_follows_another_process(self, c, inst, app):
        _talk(c, "first")
        (inst / "agent_events" / "chat_n.txt").write_text("5000", encoding="utf-8")
        aid = c.post("/api/agent/chat/ask", json={"text": "after another process numbered", "feed": 1}).get_json()["ask_id"]
        assert _wait(lambda: any(e["n"] > 5000 and e["hook_event_name"] == "Text" for e in _events(c)))
        assert _wait(lambda: app.config["agent_chat"].ask_alive(aid) is False)
        # a process whose counter lags rewrote the shared mark lower; the clear lifts it to since_n
        (inst / "agent_events" / "chat_n.txt").write_text("3", encoding="utf-8")
        r = c.post("/api/agent/chat/clear", json={"keep": 0})
        assert r.status_code == 200, r.get_json()
        since = r.get_json()["since_n"]
        assert since > 5000 and int((inst / "agent_events" / "chat_n.txt").read_text()) >= since

    def test_a_clear_from_a_stale_sheet_is_refused(self, c):
        _talk(c, "x")
        seen = _cards(c)["conversation"]["since_n"]
        assert c.post("/api/agent/chat/clear", json={"keep": 1, "since_n": seen}).status_code == 200
        r = c.post("/api/agent/chat/clear", json={"keep": 1, "since_n": seen})
        assert r.status_code == 409 and "already cleared" in r.get_json()["error"]
        assert len(c.get("/api/agent/chat/archives").get_json()["archives"]) == 1

    def test_two_clears_at_once_archive_once(self, tmp_path):
        ev = [{"n": 1, "hook_event_name": "User", "text": "a"}]
        conv_mod.clear(tmp_path, "k", since_n=1, events=ev, who="h", keep=True, expect_since=0)
        with pytest.raises(conv_mod.Moved):
            conv_mod.clear(tmp_path, "k", since_n=1, events=ev, who="h", keep=True, expect_since=0)
        assert len(conv_mod.archives(tmp_path, "k")) == 1

    def test_an_event_numbered_during_the_clear_is_archived(self, c, app, monkeypatch):
        import threading
        _talk(c, "before")
        orig = chat_api._next_n
        numbered = threading.Event()

        def slow(ev):
            n = orig(ev)
            numbered.set()
            time.sleep(0.6)                  # numbered, not yet written
            return n
        monkeypatch.setattr(chat_api, "_next_n", slow)

        def rec():
            with app.app_context():
                chat_api._record({"ts": time.time(), "hook_event_name": "Text", "origin": "chat", "chip": _name(c),
                                  "text": "landing during the clear", "session_id": None})
        t = threading.Thread(target=rec)
        t.start()
        assert numbered.wait(5)
        a = c.post("/api/agent/chat/clear", json={"keep": 1}).get_json()["archive"]
        t.join(5)
        got = c.get(f"/api/agent/chat/archives/{a['id']}").get_json()
        assert "landing during the clear" in [k.get("text") for k in got["cards"]]

    def test_a_closed_sessions_trailing_event_stays_hidden_after_the_next_start(self, c, app):
        _talk(c, "hi")
        sid = c.get("/api/agent/chat/status").get_json()["session"]["session_id"]
        c.post("/api/agent/chat/clear", json={"keep": 1})
        with app.app_context():
            chat_api._record({"ts": time.time(), "hook_event_name": "Error", "origin": "chat", "chip": _name(c),
                              "error": "trailing error of the ended process", "session_id": sid})
        _talk(c, "next")                     # the fake CLI hands out the same id again
        texts = [k.get("error") or k.get("text") for k in _cards(c)["cards"]]
        assert "trailing error of the ended process" not in texts
        assert "next" in texts and "answer 1: next" in texts

    def test_a_panel_question_answered_after_the_clear_is_not_lost(self, c, app):
        c.post("/api/agent/chat/clear", json={"keep": 0})
        with app.app_context():
            app.config.setdefault("agent_feed_asks", {})["ask-late"] = {"chip": _name(c), "backend": "claude", "who": "human"}
            chat_api._post_feed_answer("ask-late", [{"hook_event_name": "Text", "text": "the late answer"}])
        assert "the late answer" in [k.get("text") for k in _cards(c)["cards"]]

    def test_a_permission_error_on_the_index_is_unreadable_not_absent(self, tmp_path, monkeypatch):
        ev = [{"n": 1, "hook_event_name": "User", "text": "a"}]
        conv_mod.clear(tmp_path, "k", since_n=1, events=ev, who="h", keep=True)
        p = conv_mod.index_path(tmp_path, "k")
        before = p.read_bytes()
        orig = Path.stat

        def stat(self, *a, **k):
            if self == p:
                raise PermissionError(13, "Access is denied")
            return orig(self, *a, **k)
        monkeypatch.setattr(Path, "stat", stat)
        with pytest.raises(conv_mod.Unreadable):
            conv_mod.clear(tmp_path, "k", since_n=2, events=[{"n": 2}], who="h", keep=True)
        assert conv_mod.load(tmp_path, "k")["since_n"] == 1, "a transient error reads the last good index"
        monkeypatch.setattr(Path, "stat", orig)
        assert p.read_bytes() == before


class TestArchive:
    def test_the_archive_reads_past_the_ring(self, c, inst, app):
        """A conversation older than the replayed week (and longer than the
        800-line ring) is still archived whole: the day files are read."""
        name = _name(c)
        old = datetime.now() - timedelta(days=12)
        d = inst / "agent_events"
        d.mkdir(parents=True, exist_ok=True)
        with open(d / (old.strftime("%Y-%m-%d") + ".jsonl"), "w", encoding="utf-8") as f:
            for i in range(1, 4):
                f.write(json.dumps({"ts": old.timestamp() + i, "hook_event_name": "User", "origin": "chat",
                                    "chip": name, "text": f"old line {i}", "n": i}) + "\n")
            f.write(json.dumps({"ts": old.timestamp() + 9, "hook_event_name": "User", "origin": "chat",
                                "chip": "another chip", "text": "not ours", "n": 4}) + "\n")
        (d / "chat_n.txt").write_text("4", encoding="utf-8")
        _talk(c, "today")
        a = c.post("/api/agent/chat/clear", json={"keep": 1}).get_json()["archive"]
        got = c.get(f"/api/agent/chat/archives/{a['id']}").get_json()
        users = [k["text"] for k in got["cards"] if k["kind"] == "user"]
        assert users == ["old line 1", "old line 2", "old line 3", "today"]
        assert a["title"] == "old line 1"

    def test_delete_an_archive(self, c, inst):
        _talk(c, "keep then drop")
        a = c.post("/api/agent/chat/clear", json={"keep": 1}).get_json()["archive"]
        f = conv_mod.index_path(str(inst), _name(c)).parent / (a["id"] + ".jsonl")
        assert f.exists()
        assert c.post(f"/api/agent/chat/archives/{a['id']}/delete").get_json()["ok"]
        assert not f.exists()
        assert c.get(f"/api/agent/chat/archives/{a['id']}").status_code == 404
        assert c.post(f"/api/agent/chat/archives/{a['id']}/delete").status_code == 404
        assert _cards(c)["conversation"]["archives"] == 0

    @pytest.mark.parametrize("aid", ["..%2Findex", "index", "ZZZZZZZZZZZZ", "0123456789abc"])
    def test_a_bad_archive_id_is_a_404(self, c, aid):
        assert c.get(f"/api/agent/chat/archives/{aid}").status_code == 404
        assert c.post(f"/api/agent/chat/archives/{aid}/delete").status_code == 404

    def test_needs_a_chip(self, app):
        cl = app.test_client()
        assert cl.post("/api/agent/chat/clear", json={}).status_code == 409
        assert cl.get("/api/agent/chat/archives").status_code == 409


class TestStore:
    def test_visible(self):
        conv = {"since_n": 5, "closed_sessions": {"s1": None, "s3": 9}}
        assert not conv_mod.visible({"n": 5}, conv)
        assert conv_mod.visible({"n": 6}, conv)
        assert not conv_mod.visible({"n": 60, "session_id": "s1"}, conv), "closed, no start since: all hidden"
        assert conv_mod.visible({"n": 6, "session_id": "s2"}, conv)
        assert not conv_mod.visible({"n": 9, "session_id": "s3"}, conv), "reopened at 9: hidden up to it"
        assert conv_mod.visible({"n": 10, "session_id": "s3"}, conv), "and shown after it"
        assert not conv_mod.visible({"n": "x"}, conv)

    def test_since_n_never_moves_back_and_closed_sessions_are_capped(self, tmp_path):
        for i in range(30):
            conv_mod.clear(tmp_path, "k", since_n=100 - i, events=[], who="h", keep=True, closed_session=f"s{i}")
        st = conv_mod.load(tmp_path, "k")
        assert st["since_n"] == 100
        assert len(st["closed_sessions"]) == 20 and list(st["closed_sessions"])[-1] == "s29"

    def test_an_unreadable_index_raises_and_stays(self, tmp_path):
        p = conv_mod.index_path(tmp_path, "k")
        p.parent.mkdir(parents=True)
        p.write_bytes(b"[1, 2")
        with pytest.raises(conv_mod.Unreadable):
            conv_mod.clear(tmp_path, "k", since_n=3, events=[{"n": 1}], who="h", keep=True)
        with pytest.raises(conv_mod.Unreadable):
            conv_mod.delete_archive(tmp_path, "k", "aaaaaaaaaaaa")
        assert p.read_bytes() == b"[1, 2"
        assert conv_mod.load(tmp_path, "k")["since_n"] == 0, "the panel shows everything rather than guess"

    def test_title_is_the_first_message_clipped(self, tmp_path):
        ev = [{"n": 1, "hook_event_name": "Text", "text": "agent first"},
              {"n": 2, "hook_event_name": "User", "text": "  a  " + "x" * 300}]
        meta = conv_mod.clear(tmp_path, "k", since_n=2, events=ev, who="h", keep=True)
        assert meta["title"].startswith("a xxx") and len(meta["title"]) == conv_mod.TITLE_CHARS
        assert meta["messages"] == 2


_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_agent_clear_selfcheck():
    node = shutil.which("node")
    try:
        subprocess.run([node, "-e", "require('jsdom')"], check=True, capture_output=True, timeout=30, cwd=str(_ROOT))
    except Exception:
        pytest.skip("jsdom not installed")
    r = subprocess.run([node, str(_ROOT / "tests" / "agent_clear_selfcheck.cjs")], capture_output=True, text=True,
                       encoding="utf-8", timeout=120, cwd=str(_ROOT))
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.count("ok - ") >= 40, r.stdout
