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


CHIP = (200, {"ok": True, "loaded": True, "name": "c", "chip_token": "tok", "pending": 0})


@pytest.fixture
def link(monkeypatch):
    fl = FakeLink({("GET", "/api/agent/chip"): CHIP,
                   ("GET", "/api/agent/tray"): (200, {"ok": True, "count": 0, "seen_changes": 0, "entries": []})})
    monkeypatch.setattr(mcp, "_link", fl)
    monkeypatch.setattr(mcp, "_seen", None)
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
        link.answers[("POST", "/state/apply-to-live")] = lambda d: (
            (409, {"unseen_changes": True, "paths": ["b"], "seen": d["seen_changes"], "have": 2})
            if int(d["seen_changes"]) < 2 else (200, "ok"))
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
        assert r["applied"] is True and r["declared_seen"] == 2

    def test_state_edit_counts_as_looking(self, link):
        link.answers[("POST", "/field/edit")] = (200, {"ok": True})
        link.answers[("GET", "/api/agent/tray")] = (200, {"ok": True, "count": 1, "seen_changes": 1,
                                                         "entries": [{"path": "a", "new": 1}]})
        r = mcp.t_state_edit({"path": "a", "value": 1})
        assert r["staged"] is True and mcp._seen == 1
        form = [c for c in link.calls if c[1] == "/field/edit"][0][2]
        assert form["dot_path"] == "a" and form["value"] == "1" and form["expect_chip"] == "tok"

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
        assert {"sm_status", "state_get", "state_edit", "apply_to_live", "journal_append", "check_fit"} <= set(names)
        # no SM is running in this tmp instance: a tool error, not a crash
        assert replies[3]["result"]["isError"] is True and "not running" in replies[3]["result"]["content"][0]["text"]
        assert replies[4]["error"]["code"] == -32601
        assert replies[5]["result"] == {}
        assert 2 not in [r for r in replies if "error" in replies[r]], "the notification got no reply"
