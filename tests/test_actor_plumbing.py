"""docs/173 S1: who did it -- the actor travels from the caller to every record.

The bridge learns its CLI from MCP ``initialize.clientInfo`` and sends it as
``X-SM-Agent``; SM turns that (or a browser's ``X-SM-Actor``) into one actor
string that lands on the staged ChangeEntry, the undo-journal unit entries,
and the pre-apply SnapshotMeta. The hook carries ``--backend``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from quam_state_manager import hook, mcp
from quam_state_manager.web.app import create_app

ROOT = Path(__file__).resolve().parent.parent
_H = {"Origin": "http://localhost"}


@pytest.fixture
def client(tmp_path):
    return create_app(testing=True, instance_path=str(tmp_path / "_inst")).test_client()


@pytest.fixture
def synth_folder(tmp_path):
    from tests.test_web import _make_state, _make_wiring
    d = tmp_path / "chip"
    d.mkdir()
    (d / "state.json").write_text(json.dumps(_make_state(), indent=2), encoding="utf-8")
    (d / "wiring.json").write_text(json.dumps(_make_wiring(), indent=2), encoding="utf-8")
    return d


@pytest.fixture
def loaded(client, synth_folder):
    client.post("/load", data={"folder": str(synth_folder)})
    return client


def _numeric_leaf(c) -> str:
    cont = c.get("/api/agent/state?path=qubits.qA1").get_json()["value"]
    key = next(k for k, v in cont.items() if isinstance(v, (int, float)) and not isinstance(v, bool))
    return f"qubits.qA1.{key}"


class TestTheBridgeKnowsItsCli:
    def test_initialize_names_the_client(self, monkeypatch):
        monkeypatch.setattr(mcp, "_link", None)
        assert mcp._learn_client({"clientInfo": {"name": "claude-code", "version": "2.1"}}) == "claude"
        assert mcp._learn_client({"clientInfo": {"name": "Codex CLI"}}) == "codex"
        assert mcp._learn_client({"clientInfo": {"name": "My Agent!"}}) == "myagent"
        assert mcp._learn_client({}) == "myagent", "no name keeps the last answer"

    def test_the_link_carries_it_as_a_header(self, monkeypatch):
        from quam_state_manager.core import agent_link
        fl = agent_link.SMLink("http://127.0.0.1:1")
        monkeypatch.setattr(mcp, "_link", fl)
        mcp._learn_client({"clientInfo": {"name": "codex"}})
        assert fl._headers()["X-SM-Agent"] == "codex"


class TestTheActorLandsEverywhere:
    def test_staged_entry_unit_and_snapshot(self, loaded, synth_folder, tmp_path):
        from quam_state_manager.core import undo_journal
        from quam_state_manager.web import routes
        c = loaded
        tok = c.get("/api/agent/chip").get_json()["chip_token"]
        path = _numeric_leaf(c)
        r = c.post("/field/edit", data={"dot_path": path, "value": "12.5", "expect_chip": tok},
                   headers={**_H, "X-SM-Agent": "codex"})
        assert r.status_code == 200, r.get_data(as_text=True)
        tray = c.get("/api/agent/tray").get_json()
        assert tray["entries"][0]["actor"] == "by_codex"
        r = c.post("/state/apply-to-live", data={"seen_changes": "1"}, headers={**_H, "X-SM-Agent": "codex",
                                                                             "X-SM-Plan": "plan-7"})
        assert r.status_code == 200, r.get_data(as_text=True)[:200]
        units = undo_journal.load(undo_journal.sidecar_path(c.application.instance_path, synth_folder))
        assert units and units[-1]["entries"][0]["actor"] == "by_codex"
        assert units[-1]["meta"]["actor"] == "by_codex" and units[-1]["meta"]["plan_id"] == "plan-7"
        with c.application.app_context():
            snaps = routes._history().list_snapshots(str(synth_folder))
        assert any(getattr(s, "actor", None) == "by_codex" for s in snaps), \
            "the pre-apply snapshot names who applied"

    def test_a_browser_names_the_person_or_is_plain_human(self, loaded):
        c = loaded
        tok = c.get("/api/agent/chip").get_json()["chip_token"]
        path = _numeric_leaf(c)
        c.post("/field/edit", data={"dot_path": path, "value": "1", "expect_chip": tok}, headers=_H)
        c.post("/field/edit", data={"dot_path": path, "value": "2", "expect_chip": tok},
               headers={**_H, "X-SM-Actor": "박OO"})
        actors = [e["actor"] for e in c.get("/api/agent/tray").get_json()["entries"]]
        assert actors == ["human", "human:박OO"]

    def test_a_cookie_names_the_person_too(self, loaded):
        c = loaded
        tok = c.get("/api/agent/chip").get_json()["chip_token"]
        path = _numeric_leaf(c)
        c.set_cookie("sm_actor", "이OO")
        c.post("/field/edit", data={"dot_path": path, "value": "3", "expect_chip": tok}, headers=_H)
        assert c.get("/api/agent/tray").get_json()["entries"][-1]["actor"] == "human:이OO"


class TestTheHookNamesItsBackend:
    def test_backend_flag_lands_in_the_record(self, tmp_path):
        env = dict(os.environ, PYTHONUTF8="1", SM_INSTANCE=str(tmp_path), PYTHONPATH=str(ROOT))
        env.pop("SM_URL", None)
        subprocess.run([sys.executable, "-m", "quam_state_manager.hook", "--backend", "Codex"],
                       input=json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_use_id": "t",
                                         "tool_input": {"command": "python x.py"}}),
                       capture_output=True, text=True, encoding="utf-8", env=env, cwd=str(ROOT), timeout=60)
        f = next((tmp_path / "agent_events").glob("*.jsonl"))
        assert json.loads(f.read_text(encoding="utf-8").splitlines()[-1])["backend"] == "codex"
        assert hook.record({"hook_event_name": "Stop"}, None)["backend"] is None
