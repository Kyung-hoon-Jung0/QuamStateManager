"""docs/172: the MCP server is a thin client of the running SM, and it obeys
the unseen-edit rule (docs/120) on the agent's behalf.

Driven two ways: the protocol over real stdio (a subprocess speaking
newline-delimited JSON-RPC to ``python -m quam_state_manager.mcp``), and the
tools in-process against a FAKE link that records the HTTP calls -- the
real HTTP path was proved by hand against a copy of the 20Q chip and is
recorded in docs/172; what is pinned here is the contract.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from quam_state_manager import mcp
from quam_state_manager.core import agent_link

ROOT = Path(__file__).resolve().parent.parent


class FakeLink:
    """Answers by (method, path); records everything it was asked."""

    def __init__(self, answers: dict):
        self.answers = answers
        self.calls: list[tuple] = []

    def alive(self):
        return True

    def get(self, path, params=None):
        self.calls.append(("GET", path, params))
        return self.answers.get(("GET", path), (404, {"ok": False, "error": "no"}))

    def post_form(self, path, data):
        self.calls.append(("POST", path, data))
        a = self.answers.get(("POST", path), (404, {"ok": False}))
        return a(data) if callable(a) else a

    def post_json(self, path, data):
        self.calls.append(("POSTJ", path, data))
        a = self.answers.get(("POSTJ", path), (404, {"ok": False}))
        return a(data) if callable(a) else a


CHIP = (200, {"ok": True, "loaded": True, "name": "c", "chip_token": "tok", "pending": 0, "live_diverged": False})


@pytest.fixture
def link(monkeypatch):
    fl = FakeLink({("GET", "/api/agent/chip"): CHIP,
                   ("GET", "/api/agent/tray"): (200, {"ok": True, "count": 0, "seen_changes": 0, "entries": []}),
                   ("POSTJ", "/api/agent/journal"): (200, {"ok": True})})
    monkeypatch.setattr(mcp, "_link", fl)
    monkeypatch.setattr(mcp, "_seen", None)
    monkeypatch.setattr(mcp, "_chip", None)
    return fl


class TestSeenGate:
    def test_apply_before_ever_looking_does_nothing(self, link):
        r = mcp.t_apply_to_live({})
        assert r["applied"] is False and "tray first" in r["note"]
        assert not any(c[1] == "/state/apply-to-live" for c in link.calls)

    def test_apply_declares_the_count_the_agent_saw_not_a_fresh_read(self, link):
        link.answers[("GET", "/api/agent/tray")] = (200, {"ok": True, "count": 1, "seen_changes": 1,
                                                         "entries": [{"path": "a"}]})
        mcp.t_tray({})
        # a human stages one more in the window; the agent never looked again
        link.answers[("GET", "/api/agent/chip")] = (200, {**CHIP[1], "pending": 2})
        link.answers[("GET", "/api/agent/tray")] = (200, {"ok": True, "count": 2, "seen_changes": 2,
                                                         "entries": [{"path": "a"}, {"path": "b"}]})
        applied = {"done": False}

        def _apply(d):
            if int(d["seen_changes"]) < 2:
                return 409, {"unseen_changes": True, "paths": ["b"], "seen": d["seen_changes"], "have": 2}
            applied["done"] = True
            link.answers[("GET", "/api/agent/chip")] = (200, {**CHIP[1], "pending": 0})
            return 200, "<div>fragment</div>"
        link.answers[("POST", "/state/apply-to-live")] = _apply
        r = mcp.t_apply_to_live({})
        assert r["applied"] is False and r["refused"]["paths"] == ["b"]
        sent = [c for c in link.calls if c[1] == "/state/apply-to-live"][-1][2]
        assert sent["seen_changes"] == 1, "declares what the agent saw, not the fresh count"
        assert sent["expect_chip"] == "tok"
        # after the refusal the agent must look again before pressing
        r = mcp.t_apply_to_live({})
        assert r["applied"] is False and "tray first" in r["note"]
        mcp.t_tray({})
        r = mcp.t_apply_to_live({})
        assert r["applied"] is True and r["declared_seen"] == 2 and r["wrote"] == ["a", "b"]
        assert any(c[1] == "/api/agent/journal" and "applied 2 edit" in c[2]["text"] for c in link.calls), \
            "the bridge journals what it wrote"

    def test_state_edit_counts_as_looking_and_journals_with_its_reason(self, link):
        link.answers[("POST", "/field/edit")] = (200, {"ok": True})
        link.answers[("GET", "/api/agent/tray")] = (200, {"ok": True, "count": 1, "seen_changes": 1,
                                                         "entries": [{"path": "a", "old": 0, "new": 1}]})
        r = mcp.t_state_edit({"path": "a", "value": 1, "reason": "rabi says so"})
        assert r["staged"] is True and mcp._seen == 1
        form = [c for c in link.calls if c[1] == "/field/edit"][0][2]
        assert form["dot_path"] == "a" and form["value"] == "1" and form["expect_chip"] == "tok"
        j = [c for c in link.calls if c[1] == "/api/agent/journal"][-1][2]
        assert "staged `a` 0 -> 1" in j["text"] and j["reason"] == "rabi says so" and j["paths"] == ["a"]

    def test_an_apply_that_did_not_clear_the_tray_is_not_reported_as_applied(self, link):
        """The window answers a conflict FRAGMENT with 200; the bridge believes
        only the chip's own pending count."""
        link.answers[("GET", "/api/agent/tray")] = (200, {"ok": True, "count": 1, "seen_changes": 1, "entries": [{"path": "a"}]})
        mcp.t_tray({})
        link.answers[("GET", "/api/agent/chip")] = (200, {**CHIP[1], "pending": 1})
        link.answers[("POST", "/state/apply-to-live")] = (200, '<div id="pending-tray" class="pending-tray pending-tray-conflict">')
        r = mcp.t_apply_to_live({})
        assert r["applied"] is False and mcp._seen is None

    def test_a_stale_live_conflict_is_named_and_apply_refuses_a_known_divergence(self, link):
        link.answers[("GET", "/api/agent/tray")] = (200, {"ok": True, "count": 1, "seen_changes": 1, "entries": [{"path": "a"}]})
        mcp.t_tray({})
        link.answers[("GET", "/api/agent/chip")] = (200, {**CHIP[1], "pending": 1})
        link.answers[("POST", "/state/apply-to-live")] = (409, {"ok": False, "conflict": "stale_live"})
        r = mcp.t_apply_to_live({})
        assert r["applied"] is False and r["refused"]["conflict"] == "stale_live" and "take_live" in r["how"]
        mcp.t_tray({})
        link.answers[("GET", "/api/agent/chip")] = (200, {**CHIP[1], "pending": 1, "live_diverged": True})
        r = mcp.t_apply_to_live({})
        assert r["applied"] is False and r["refused"]["conflict"] == "stale_live"
        assert not any(c[1] == "/state/apply-to-live" and c is link.calls[-1] for c in link.calls[-1:]), \
            "a known divergence is refused before the door is even tried"


class TestTakeLive:
    def test_refuses_with_a_non_empty_tray_and_when_nothing_moved(self, link):
        link.answers[("GET", "/api/agent/chip")] = (200, {**CHIP[1], "pending": 2, "live_diverged": True})
        r = mcp.t_take_live({})
        assert r["taken"] is False and "tray is not empty" in r["note"]
        link.answers[("GET", "/api/agent/chip")] = (200, {**CHIP[1], "pending": 0, "live_diverged": False})
        assert mcp.t_take_live({})["taken"] is False
        assert not any(c[1] == "/state/sync" for c in link.calls)

    def test_pulls_with_mode_discard_and_journals(self, link):
        link.answers[("GET", "/api/agent/chip")] = (200, {**CHIP[1], "pending": 0, "live_diverged": True})
        link.answers[("POST", "/state/sync")] = (200, {"status": "ok"})
        r = mcp.t_take_live({})
        assert r["taken"] is True
        sync = [c for c in link.calls if c[1] == "/state/sync"][0][2]
        assert sync["mode"] == "discard"
        assert any(c[1] == "/api/agent/journal" and "took the live files" in c[2]["text"] for c in link.calls)


class TestAnswersCarryTheChip:
    def test_every_dict_answer_names_the_chip(self, link, capsys):
        mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "sm_status", "arguments": {}}})
        mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "tray", "arguments": {}}})
        out = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.strip()]
        body = json.loads(out[1]["result"]["content"][0]["text"])
        assert body["chip"] == "c"

    def test_the_bridge_header_names_the_actor(self):
        assert agent_link.SMLink("http://127.0.0.1:1")._headers()["X-SM-Agent"] == "mcp"
        assert agent_link.SMLink("http://127.0.0.1:1", agent_id="hook")._headers()["X-SM-Agent"] == "hook"

    def test_a_409_offer_is_handed_back_not_answered(self, link):
        link.answers[("POST", "/field/edit")] = (409, {"error": "numeric text", "type_fix_offer": True})
        r = mcp.t_state_edit({"path": "a", "value": "007"})
        assert r["staged"] is False and r["needs_answer"] is True and r["offer"]["type_fix_offer"]
        assert not any(c[1] == "/api/agent/tray" for c in link.calls)

    def test_never_force(self, link):
        import inspect
        src = inspect.getsource(mcp)
        assert '"force"' not in src and "ack_unseen" not in src

    def test_journal_append_requires_a_reason_by_schema(self):
        spec = mcp.TOOLS["journal_append"][0]["inputSchema"]
        assert set(spec["required"]) == {"text", "reason"}


class TestNoServer:
    def test_a_missing_sm_is_a_tool_error_naming_where_it_looked(self, monkeypatch, tmp_path):
        monkeypatch.setattr(mcp, "_link", None)
        monkeypatch.setattr(agent_link, "connect", lambda *a, **k: None)
        monkeypatch.setenv("SM_INSTANCE", str(tmp_path))
        with pytest.raises(mcp.ToolError, match="not running"):
            mcp._sm()


class TestReadOnlyMode:
    def test_a_readonly_bridge_lists_and_allows_only_the_read_tools(self, tmp_path):
        env = dict(os.environ, PYTHONUTF8="1", SM_INSTANCE=str(tmp_path), PYTHONPATH=str(ROOT), SM_MCP_MODE="readonly")
        env.pop("SM_URL", None)
        p = subprocess.Popen([sys.executable, "-m", "quam_state_manager.mcp"], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                             env=env, cwd=str(ROOT))
        msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "state_edit", "arguments": {"path": "a", "value": 1}}},
                {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "apply_to_live", "arguments": {}}}]
        out, err = p.communicate("\n".join(json.dumps(m) for m in msgs) + "\n", timeout=60)
        assert p.returncode == 0, err
        replies = {r["id"]: r for r in (json.loads(l) for l in out.splitlines() if l.strip())}
        names = {t["name"] for t in replies[2]["result"]["tools"]}
        assert "state_get" in names and "runs" in names and "check_fit" in names
        assert not ({"state_edit", "apply_to_live", "undo", "take_live", "journal_append", "note_set"} & names)
        for i in (3, 4):
            assert replies[i]["result"]["isError"] is True and "read-only" in replies[i]["result"]["content"][0]["text"]


class TestProtocolOverStdio:
    def test_initialize_list_call_and_ping(self, tmp_path):
        env = dict(os.environ, PYTHONUTF8="1", SM_INSTANCE=str(tmp_path), PYTHONPATH=str(ROOT))
        env.pop("SM_URL", None)
        p = subprocess.Popen([sys.executable, "-m", "quam_state_manager.mcp"], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                             env=env, cwd=str(ROOT))
        msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "sm_status", "arguments": {}}},
                {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "nope", "arguments": {}}},
                {"jsonrpc": "2.0", "id": 5, "method": "ping"}]
        out, err = p.communicate("\n".join(json.dumps(m) for m in msgs) + "\n", timeout=60)
        assert p.returncode == 0, err
        replies = {r["id"]: r for r in (json.loads(l) for l in out.splitlines() if l.strip())}
        assert replies[1]["result"]["serverInfo"]["name"] == "quam-state-manager"
        assert replies[1]["result"]["protocolVersion"] == "2025-06-18"
        names = [t["name"] for t in replies[2]["result"]["tools"]]
        assert {"sm_status", "state_get", "state_edit", "apply_to_live", "journal_append", "check_fit", "take_live"} <= set(names)
        # no SM is running in this tmp instance: a tool error, not a crash
        assert replies[3]["result"]["isError"] is True and "not running" in replies[3]["result"]["content"][0]["text"]
        assert replies[4]["error"]["code"] == -32601
        assert replies[5]["result"] == {}
        assert 2 not in [r for r in replies if "error" in replies[r]], "the notification got no reply"
