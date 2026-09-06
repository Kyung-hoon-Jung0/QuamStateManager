"""Agent setup (docs/173 S7): previews before writes, a backup beside every
file, idempotent marked blocks, honest 'registered' reads, the lab-context
questions from what the state shows. Every write goes to a TEMP home."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from quam_state_manager.core import agent_setup as st


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    (h / ".claude").mkdir(parents=True)
    (h / ".codex").mkdir(parents=True)
    return h


class TestClaude:
    def test_mcp_entry_is_previewed_written_backed_up_and_idempotent(self, home):
        p = st.claude_json_path(home)
        p.write_text(json.dumps({"numStartups": 3, "mcpServers": {"other": {"command": "x"}}}), encoding="utf-8")
        spec = st.mcp_server_spec("py.exe", "D:/repo", instance="D:/inst", chip=None)
        pv = st.preview_claude_mcp(spec, home)
        assert pv["changed"] and pv["before"] is None and pv["after"]["args"] == ["-m", "quam_state_manager.mcp"]
        assert pv["after"]["env"]["SM_INSTANCE"] == "D:/inst" and "SM_URL" not in pv["after"]["env"]
        r = st.write_claude_mcp(spec, home)
        assert Path(r["backup"]).exists() and Path(r["backup"]).name.startswith(".claude.json.sm-backup-")
        d = json.loads(p.read_text(encoding="utf-8"))
        assert d["numStartups"] == 3 and d["mcpServers"]["other"] == {"command": "x"}, "the rest of the file is untouched"
        assert d["mcpServers"][st.SERVER_NAME] == spec
        assert st.claude_mcp_registered(home) == spec
        assert st.preview_claude_mcp(spec, home)["changed"] is False
        r2 = st.remove_claude_mcp(home)
        assert r2["removed"] and st.claude_mcp_registered(home) is None
        assert st.remove_claude_mcp(home)["removed"] is False
        assert json.loads(p.read_text(encoding="utf-8"))["mcpServers"] == {"other": {"command": "x"}}

    def test_hooks_merge_replaces_ours_and_keeps_theirs(self, home):
        p = st.claude_settings_path(home)
        p.write_text(json.dumps({"permissions": {"allow": ["Read"]},
                                 "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "their-hook"}]},
                                                          {"matcher": "Bash", "hooks": [{"type": "command", "command": "python -m quam_state_manager.hook --backend claude"}]}]}}),
                     encoding="utf-8")
        assert st.claude_hooks_registered(home) is True
        cmd = st.hook_command("py.exe", "D:/inst")
        assert cmd == '"py.exe" -m quam_state_manager.hook --backend claude --instance "D:/inst"'
        pv = st.preview_claude_hooks(cmd, home)
        pre = pv["after"]["PreToolUse"]
        assert [g["hooks"][0]["command"] for g in pre] == ["their-hook", cmd], "ours replaced, theirs kept, once"
        assert set(pv["after"]) == {"PreToolUse", "PostToolUse", "PostToolUseFailure", "Stop"}
        assert pv["after"]["Stop"][0]["hooks"][0]["async"] is True and "matcher" not in pv["after"]["Stop"][0]
        st.write_claude_hooks(cmd, home)
        st.write_claude_hooks(cmd, home)
        d = json.loads(p.read_text(encoding="utf-8"))
        assert d["permissions"] == {"allow": ["Read"]}
        assert len(d["hooks"]["PreToolUse"]) == 2 and len(d["hooks"]["Stop"]) == 1, "idempotent"
        assert len(list(home.glob(".claude/settings.json.sm-backup-*"))) >= 1
        r = st.remove_claude_hooks(home)
        assert r["removed"] and json.loads(p.read_text(encoding="utf-8"))["hooks"] == {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "their-hook"}]}]}
        assert st.claude_hooks_registered(home) is False

    def test_allow_rules_in_the_calibrations_folder(self, tmp_path):
        cal = tmp_path / "cal"
        cal.mkdir()
        assert st.allow_registered(cal) is False and st.allow_registered(None) is False
        pv = st.preview_allow(cal)
        assert pv["exists"] is False and pv["after"][:2] == list(st.ALLOW_RULES[:2])
        st.write_allow(cal)
        st.write_allow(cal)
        d = json.loads(st.allow_path(cal).read_text(encoding="utf-8"))
        assert d["permissions"]["allow"].count("mcp__quam-state-manager__*") == 1
        assert st.allow_registered(cal) is True

    def test_frozen_exe_commands(self, monkeypatch):
        monkeypatch.setattr(st.sys, "frozen", True, raising=False)
        monkeypatch.setattr(st.sys, "executable", r"C:\sm\quam-manager.exe")
        assert st.hook_command() == '"C:\\sm\\quam-manager.exe" --hook --backend claude'
        assert st.mcp_server_spec()["args"] == ["--mcp"]


class TestCodex:
    def test_toml_block_between_markers_replaces_and_keeps_the_rest(self, home):
        p = st.codex_config_path(home)
        p.write_text("[projects.'d:\\work\\x']\ntrust_level = \"trusted\"\n", encoding="utf-8")
        spec = st.mcp_server_spec("D:/envs/py.exe", "D:/work/sm agent", instance="D:/inst")
        pv = st.preview_codex(spec, home)
        assert pv["changed"] and pv["after"].startswith("[projects.'d:\\work\\x']") and st.TOML_START in pv["after"]
        st.write_codex(spec, home)
        import tomllib
        d = tomllib.loads(p.read_text(encoding="utf-8"))
        assert d["projects"]["d:\\work\\x"]["trust_level"] == "trusted"
        srv = d["mcp_servers"][st.SERVER_NAME]
        assert srv["command"] == "D:/envs/py.exe" and srv["args"] == ["-m", "quam_state_manager.mcp"]
        assert srv["env"]["PYTHONPATH"] == "D:/work/sm agent" and srv["env"]["SM_INSTANCE"] == "D:/inst" and srv["tool_timeout_sec"] == 1800
        assert st.codex_registered(home)
        spec2 = st.mcp_server_spec("D:/envs/py2.exe", "D:/repo")
        st.write_codex(spec2, home)
        d = tomllib.loads(p.read_text(encoding="utf-8"))
        assert d["mcp_servers"][st.SERVER_NAME]["command"] == "D:/envs/py2.exe" and p.read_text(encoding="utf-8").count(st.TOML_START) == 1
        assert st.remove_codex(home)["removed"] and st.codex_registered(home) is False
        assert tomllib.loads(p.read_text(encoding="utf-8"))["projects"]["d:\\work\\x"]["trust_level"] == "trusted"
        assert st.remove_codex(home)["removed"] is False

    def test_toml_str_survives_quotes(self):
        import tomllib
        for s in ("D:\\a b\\c", "it's", 'say "hi"', "한글 경로"):
            assert tomllib.loads("v = " + st._toml_str(s))["v"] == s


class TestContext:
    def test_facts_and_questions_from_a_state(self):
        state = {"extras": {"chip_name": "PJ"}, "qubits": {
            "q1": {"z": {"opx_output": "#/wiring/...", "joint_offset": 0.1, "min_offset": 0.0}},
            "q2": {"qdac_bias": {"channel": 3, "dc_offset": 0.0, "trigger_port": "ext1", "dwell": 1e-6}},
            "q3": {}},
            "qubit_pairs": {"q1-2": {"coupler": {"z": {}}}}}
        f = st.detect_facts(state, node_names=["05_power_rabi", "02_res"])
        assert f["n_qubits"] == 3 and f["n_pairs"] == 1 and f["chip_name"] == "PJ" and f["nodes"] == ["02_res", "05_power_rabi"]
        assert f["couplers_seen"] is True and f["purcell_mentioned"] is False and f["twpa_seen"] is False
        assert f["bias_modes"] == {"opx": 1, "qdac": 1, "none": 1} and f["flux_tunable_seen"] and f["qdac_seen"]
        qs = st.questions(f)
        ids = [q["id"] for q in qs]
        assert ids == ["tunable", "coupler", "purcell", "squid", "data_read", "notes"]
        by = {q["id"]: q for q in qs}
        assert by["coupler"]["detected"] == "tunable coupler" and by["purcell"]["detected"] == "unknown"
        assert by["data_read"]["detected"] == "no", "direct data reads are opt-in"

    def test_block_written_between_markers_idempotent_with_backup(self, tmp_path):
        cal = tmp_path / "cal"
        cal.mkdir()
        (cal / "CLAUDE.local.md").write_text("# mine\n\nkeep this\n", encoding="utf-8")
        facts = st.detect_facts({"qubits": {"q1": {}}, "qubit_pairs": {}})
        block = st.context_block(facts, {"tunable": "fixed-frequency", "coupler": "none", "purcell": "no", "squid": "no SQUID",
                                         "data_read": "no", "notes": "Cooldown 2026-09; readout LO shared"},
                                 chip="PJ", data_folder="D:/data")
        assert block.startswith(st.CTX_START) and block.rstrip().endswith(st.CTX_END)
        assert "Purcell filter: no" in block and "NOT allowed" in block and "Cooldown 2026-09" in block
        assert "run nodes only with run_node" in block
        pv = st.preview_context(cal, block)
        assert pv["changed"] and pv["after"].startswith("# mine\n\nkeep this\n\n" + st.CTX_START)
        r = st.write_context(cal, block)
        assert Path(r["backup"]).exists()
        block2 = block.replace("Purcell filter: no", "Purcell filter: yes")
        st.write_context(cal, block2)
        t = (cal / "CLAUDE.local.md").read_text(encoding="utf-8")
        assert t.count(st.CTX_START) == 1 and "Purcell filter: yes" in t and "keep this" in t
        assert st.context_written(cal) == {"claude:local": str(cal / "CLAUDE.local.md")}
        st.write_context(cal, block, target="codex", local=False)
        assert (cal / "AGENTS.md").exists() and "codex:shared" in st.context_written(cal)


class TestRoutes:
    """The setup routes over a TEMP home (app.config['agent_setup_home'])."""

    @pytest.fixture
    def c(self, tmp_path, home, monkeypatch):
        import json as _json
        import sys as _sys
        from quam_state_manager.web.app import create_app
        from quam_state_manager.web import chat_api
        from quam_state_manager.core import scheduler
        from tests.test_web import _make_state, _make_wiring
        from tests.test_agent_runs import NODE_SRC
        monkeypatch.setattr(chat_api.ab, "detect", lambda exe: {"found": exe == "claude", "version": "fake"})
        inst = tmp_path / "_app_instance"
        app = create_app(testing=True, instance_path=str(inst))
        app.config["agent_setup_home"] = str(home)
        chip = tmp_path / "chip"
        chip.mkdir()
        (chip / "state.json").write_text(_json.dumps(_make_state()), encoding="utf-8")
        (chip / "wiring.json").write_text(_json.dumps(_make_wiring()), encoding="utf-8")
        cal = tmp_path / "cal"
        cal.mkdir()
        (cal / "05_power_rabi.py").write_text(NODE_SRC, encoding="utf-8")
        client = app.test_client()
        client.post("/load", data={"folder": str(chip)})
        with app.app_context():
            from quam_state_manager.web import routes as r
            scheduler.save_settings(r._sched_inst(), {"env_python": _sys.executable, "calibrations_folder": str(cal)})
        client._cal = cal
        client._inst = inst
        return client

    def test_status_lists_only_what_is_not_done(self, c, home):
        d = c.get("/api/agent/setup").get_json()
        assert d["ok"] and d["clis"]["claude"]["found"] and not d["clis"]["codex"]["found"]
        assert "connect_claude" in d["todo"] and "connect_codex" not in d["todo"], "no codex here -> not asked"
        assert "allow" in d["todo"] and "journal" in d["todo"] and "context" in d["todo"] and "calibrations_folder" not in d["todo"]
        assert d["hook_command"].endswith('--instance "' + str(c._inst) + '"'), "a custom instance dir rides the hook command"
        assert d["journal"]["configured"] is False

    def test_connect_previews_then_writes_with_backups_and_journal(self, c, home):
        p = c.post("/api/agent/setup/connect", json={"backend": "claude"}).get_json()
        assert p["ok"] and p["applied"] is False and p["writes"] == {}
        assert p["previews"]["mcp"]["changed"] and p["previews"]["hooks"]["changed"] and p["previews"]["allow"]["changed"]
        assert not (home / ".claude.json").exists(), "a preview writes nothing"
        d = c.post("/api/agent/setup/connect", json={"backend": "claude", "apply": True}, headers={"X-SM-Actor": "kyunghoon"}).get_json()
        assert d["applied"] and set(d["writes"]) == {"mcp", "hooks", "allow"}
        assert (home / ".claude.json").exists() and (home / ".claude" / "settings.json").exists() and (c._cal / ".claude" / "settings.local.json").exists()
        s = c.get("/api/agent/setup").get_json()
        assert s["claude"] == dict(s["claude"], mcp=True, hooks=True, allow=True) and "connect_claude" not in s["todo"] and "allow" not in s["todo"]
        assert s["record"]["connected"]["claude"]["by"] == "human:kyunghoon"
        from quam_state_manager.core import journal as jm
        from datetime import datetime
        assert "claude connected to SM by human:kyunghoon (mcp, hooks, allow)" in (jm.read(str(c._inst), "chip", datetime.now().strftime("%Y-%m-%d")) or "")
        d = c.post("/api/agent/setup/disconnect", json={"backend": "claude"}).get_json()
        assert d["removed"]["mcp"]["removed"] and d["removed"]["hooks"]["removed"]
        assert c.get("/api/agent/setup").get_json()["claude"]["mcp"] is False
        d = c.post("/api/agent/setup/connect", json={"backend": "codex", "apply": True}).get_json()
        assert d["writes"]["mcp"]["file"].endswith("config.toml") and (home / ".codex" / "config.toml").exists()
        assert c.post("/api/agent/setup/connect", json={"backend": "nope"}).status_code == 400

    def test_journal_root_is_mandatory_and_set(self, c, tmp_path):
        assert c.post("/api/agent/setup/journal", json={}).status_code == 400
        root = tmp_path / "data" / "journal"
        d = c.post("/api/agent/setup/journal", json={"root": str(root), "claude_says": True}).get_json()
        assert d["ok"] and Path(d["root"]) == root and root.is_dir() and d["claude_says"] is True
        s = c.get("/api/agent/setup").get_json()
        assert s["journal"]["configured"] is True and "journal" not in s["todo"]

    def test_context_questions_preview_and_write(self, c):
        g = c.get("/api/agent/setup/context").get_json()
        assert g["ok"] and g["facts"]["n_qubits"] == 1 and [q["id"] for q in g["questions"]][0] == "tunable"
        assert g["facts"]["nodes"] == ["05_power_rabi"] and g["written"] == {}
        answers = {"tunable": "fixed-frequency", "coupler": "none", "purcell": "no", "squid": "no SQUID", "data_read": "no", "notes": "test lab"}
        p = c.post("/api/agent/setup/context", json={"answers": answers}).get_json()
        assert p["ok"] and p["applied"] is False and set(p["previews"]) == {"claude", "codex"} and "test lab" in p["block"]
        assert not (c._cal / "CLAUDE.local.md").exists()
        d = c.post("/api/agent/setup/context", json={"answers": answers, "apply": True, "targets": ["claude"]}).get_json()
        assert d["applied"] and list(d["writes"]) == ["claude"]
        t = (c._cal / "CLAUDE.local.md").read_text(encoding="utf-8")
        assert st.CTX_START in t and "Purcell filter: no" in t and "test lab" in t and not (c._cal / "AGENTS.local.md").exists()
        s = c.get("/api/agent/setup").get_json()
        assert s["context"] == {"claude:local": str(c._cal / "CLAUDE.local.md")} and "context" not in s["todo"]
        assert c.post("/api/agent/setup/context", json={"answers": answers, "apply": True, "folder": str(c._cal / "nope")}).status_code == 409

    def test_test_call_is_a_real_readonly_ask(self, c, monkeypatch):
        from quam_state_manager.web import chat_api
        from tests.test_chat_api import _FakeClaude
        monkeypatch.setitem(chat_api.BACKEND_CLASSES, "claude", _FakeClaude)
        d = c.post("/api/agent/setup/test", json={"backend": "claude", "timeout_s": 20}).get_json()
        assert d["ok"] and d["done"] and d["failed"] is False and d["answer"].startswith("answer 1: ")
        assert isinstance(d["elapsed_s"], float) and d["tools"] == ["mcp__sm__sm_status"]
        assert c.get("/api/agent/setup").get_json()["record"]["tested"]["claude"]["ok"] is True


class TestStatusRecord:
    def test_status_reads_the_files_and_the_record(self, home, tmp_path):
        inst = tmp_path / "inst"
        cal = tmp_path / "cal"
        cal.mkdir()
        s = st.status(inst, home=home, cal_folder=str(cal), python="py", repo="R", detect={"claude": {"found": True}})
        assert s["claude"] == {"mcp": False, "hooks": False, "allow": False, "json": str(home / ".claude.json"),
                               "settings": str(home / ".claude" / "settings.json")}
        assert s["codex"]["mcp"] is False and s["context"] == {} and s["record"] == {}
        st.write_claude_mcp(st.mcp_server_spec("py"), home)
        st.write_allow(cal)
        st.save_record(inst, {"connected": {"claude": 1}})
        s = st.status(inst, home=home, cal_folder=str(cal), python="py", repo="R")
        assert s["claude"]["mcp"] and s["claude"]["allow"] and s["record"]["connected"] == {"claude": 1}
