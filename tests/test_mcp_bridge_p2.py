"""docs/260: malformed lines survive, window choice is explicit, small manual reads.

Only fake HTTP links and Flask's in-process test client; no live SM or CLI.
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from quam_state_manager import mcp
from quam_state_manager.core import agent_link, agent_setup
from quam_state_manager.web import agent_api
from tests.test_agent_setup import c as setup_client, home
from tests.test_mcp_bridge import FakeLink

ROOT = Path(__file__).resolve().parents[1]


def request(method="ping", **extra):
    return {"jsonrpc": "2.0", "id": 7, "method": method, **extra}


def wire(value):
    return json.dumps(value).encode() + b"\n"


# Every item runs through main, followed by a valid ping on the SAME stream.
BAD_LINES = [
    ("not-json", b"not json\n", -32700),
    ("truncated-json", b'{"id":\n', -32700),
    ("invalid-utf8", b'\xff\xfe\n', -32700),
    ("utf8-inside-json", b'{"method":"\xff"}\n', -32700),
    ("nan", b'NaN\n', -32700),
    ("infinity", b'Infinity\n', -32700),
    ("deep-json", b'[' * 50000 + b']' * 50000 + b'\n', -32700),
    ("array", wire([request()]), -32600),
    ("empty-array", wire([]), -32600),
    ("mixed-batch", wire([request(), 2]), -32600),
    ("number", wire(2), -32600),
    ("string", wire("ping"), -32600),
    ("boolean", wire(True), -32600),
    ("null", wire(None), -32600),
    ("empty-object", wire({}), -32600),
    ("missing-method", wire({"jsonrpc": "2.0", "id": 7}), -32600),
    ("missing-version", wire({"method": "ping", "id": 7}), -32600),
    ("wrong-version", wire(request(jsonrpc="1.0")), -32600),
    ("object-method", wire(request(method={})), -32600),
    ("array-method", wire(request(method=[])), -32600),
    ("null-method", wire(request(method=None)), -32600),
    ("numeric-method", wire(request(method=123)), -32600),
    *[(f"params-{label}", wire(request(params=value)), -32602)
      for label, value in [("list", []), ("string", "x"), ("number", 1), ("null", None), ("bool", False)]],
    *[(f"id-{label}", wire(request(id=value)), -32600)
      for label, value in [("list", []), ("object", {}), ("bool", True)]],
    ("overflow-id", b'{"jsonrpc":"2.0","method":"ping","id":1e999}\n', -32600),
    ("notification-with-id", wire(request("notifications/initialized")), -32600),
    ("notification-with-null-id", wire(request("notifications/progress", id=None)), -32600),
    ("unknown-method", wire(request("unknown")), -32601),
    ("unknown-method-null-id", wire(request("unknown", id=None)), -32601),
    ("missing-tool-name", wire(request("tools/call", params={})), -32602),
    *[(f"tool-name-{label}", wire(request("tools/call", params={"name": value})), -32602)
      for label, value in [("list", []), ("object", {}), ("number", 1), ("empty", "")]],
    *[(f"arguments-{label}", wire(request("tools/call", params={"name": "sm_status", "arguments": value})), -32602)
      for label, value in [("list", []), ("string", "x"), ("number", 1), ("null", None), ("bool", False)]],
    ("unknown-tool", wire(request("tools/call", params={"name": "missing"})), -32601),
    ("initialize-client-list", wire(request("initialize", params={"clientInfo": []})), -32602),
    ("initialize-client-null", wire(request("initialize", params={"clientInfo": None})), -32602),
    ("initialize-version-object", wire(request("initialize", params={"protocolVersion": {}})), -32602),
    ("huge-line", b'"' + b'x' * (mcp._MAX_LINE_BYTES * 2) + b'"\n', -32600),
]


def run_main(monkeypatch, raw):
    output = io.StringIO()
    monkeypatch.setattr(mcp.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(raw)))
    monkeypatch.setattr(mcp.sys, "stdout", output)
    mcp.main()
    return [json.loads(line) for line in output.getvalue().splitlines()]


@pytest.mark.parametrize("label,raw,code", BAD_LINES, ids=[r[0] for r in BAD_LINES])
def test_malformed_line_then_ping(monkeypatch, label, raw, code):
    replies = run_main(monkeypatch, raw + wire(request(id=900)))
    assert len(replies) == 2, label
    assert replies[0]["jsonrpc"] == "2.0" and replies[0]["error"]["code"] == code
    assert "result" not in replies[0]
    assert replies[0]["id"] is None or type(replies[0]["id"]) in (str, int, float)
    assert replies[1] == {"jsonrpc": "2.0", "id": 900, "result": {}}


@pytest.mark.parametrize("method,params", [
    ("notifications/initialized", {}), ("notifications/progress", []),
    ("unknown", {}), ("ping", {}), ("tools/call", {"name": "state_edit"}),
])
def test_notifications_are_silent_and_do_not_execute(monkeypatch, method, params):
    def forbidden(*a, **kw):
        pytest.fail("notification attempted HTTP")
    monkeypatch.setattr(mcp, "_sm", forbidden)
    msg = request(method, params=params)
    del msg["id"]
    assert run_main(monkeypatch, wire(msg) + wire(request(id=900))) == [
        {"jsonrpc": "2.0", "id": 900, "result": {}}]


@pytest.mark.parametrize("mid", [None, "a", 0, 2.5])
def test_valid_id_is_echoed(monkeypatch, mid):
    assert run_main(monkeypatch, wire(request(id=mid)))[0] == {"jsonrpc": "2.0", "id": mid, "result": {}}


@pytest.fixture
def windows(monkeypatch):
    facts = {
        "http://127.0.0.1:5011": {"loaded": True, "name": "chip-a", "declared_name": "declared-a", "chip_key": "key-a", "pin": "key-a"},
        "http://127.0.0.1:5012": {"loaded": True, "name": "chip-b", "declared_name": "declared-b", "chip_key": "key-b", "pin": "key-b"},
    }
    posts = []
    monkeypatch.delenv("SM_URL", raising=False)
    monkeypatch.setattr(mcp, "_link", None)
    monkeypatch.setattr(mcp, "_CHIP_PIN", None)
    monkeypatch.setattr(mcp, "_pin_key", None)
    monkeypatch.setattr(mcp, "_chip", None)
    monkeypatch.setattr(agent_link, "candidate_urls", lambda: [os.environ["SM_URL"]]
                        if os.environ.get("SM_URL") else list(facts))

    def get(link, path, params=None):
        value = facts.get(link.base)
        if value == "offline":
            raise OSError("offline")
        if path == "/api/agent/chip":
            return (200, value) if isinstance(value, dict) else (503, {})
        return 200, {"notes": [], "value": "read"}

    def post(link, path, data):
        posts.append((link.base, path, data))
        return 200, {"ok": True}

    monkeypatch.setattr(agent_link.SMLink, "get", get)
    monkeypatch.setattr(agent_link.SMLink, "post_json", post)
    return facts, posts


def call(monkeypatch, name, args=None):
    output = io.StringIO()
    monkeypatch.setattr(mcp.sys, "stdout", output)
    mcp.handle(request("tools/call", params={"name": name, "arguments": args or {}}))
    return json.loads(output.getvalue())["result"]


def content(reply):
    return json.loads(reply["content"][0]["text"])


@pytest.mark.parametrize("tool,args", [("state_get", {}), ("journal_append", {"text": "x", "reason": "r"})])
def test_ambiguous_windows_refuse_all_nonstatus_tools(windows, monkeypatch, tool, args):
    facts, posts = windows
    reply = call(monkeypatch, tool, args)
    assert reply["isError"] is True
    body = content(reply)
    assert body["refused"] == "ambiguous_window"
    assert [(w["url"], w["chip"]["pin"]) for w in body["windows"]] == [(url, f["pin"]) for url, f in facts.items()]
    assert "SM_URL" in body["how"] and "SM_CHIP" in body["how"]
    assert posts == [] and mcp._link is None


def test_status_lists_ambiguous_windows(windows, monkeypatch):
    reply = call(monkeypatch, "sm_status")
    assert reply["isError"] is False and len(content(reply)["windows"]) == 2
    assert mcp._link is None


@pytest.mark.parametrize("inactive", ["absent", "offline", "bad-response"])
def test_single_live_window_reports_url_and_chip(windows, monkeypatch, inactive):
    facts, _ = windows
    if inactive == "absent":
        del facts["http://127.0.0.1:5012"]
    else:
        facts["http://127.0.0.1:5012"] = "offline" if inactive == "offline" else None
    reply = call(monkeypatch, "sm_status")
    body = content(reply)
    assert reply["isError"] is False and body["pin"] == "key-a" and body["name"] == "chip-a"
    assert body["url"] == "http://127.0.0.1:5011"


@pytest.mark.parametrize("pin", ["key-b", "declared-b", "chip-b"])
def test_chip_pin_selects_matching_window_not_newest(windows, monkeypatch, pin):
    monkeypatch.setattr(mcp, "_CHIP_PIN", pin)
    reply = call(monkeypatch, "sm_status")
    assert reply["isError"] is False and content(reply)["url"].endswith(":5012")


def test_url_pin_selects_window(windows, monkeypatch):
    monkeypatch.setenv("SM_URL", "http://127.0.0.1:5012")
    reply = call(monkeypatch, "sm_status")
    assert reply["isError"] is False and content(reply)["pin"] == "key-b"


def test_url_and_chip_mismatch_refuses(windows, monkeypatch):
    monkeypatch.setenv("SM_URL", "http://127.0.0.1:5011")
    monkeypatch.setattr(mcp, "_CHIP_PIN", "key-b")
    reply = call(monkeypatch, "state_get")
    assert reply["isError"] is True and content(reply)["refused"] == "chip_mismatch"


def test_duplicate_declared_pin_is_ambiguous(windows, monkeypatch):
    facts, _ = windows
    for f in facts.values():
        f["declared_name"] = "shared-name"
    monkeypatch.setattr(mcp, "_CHIP_PIN", "shared-name")
    reply = call(monkeypatch, "state_get")
    assert reply["isError"] is True and content(reply)["refused"] == "ambiguous_window"
    assert mcp._pin_key is None


def test_second_window_after_attach_requires_choice(windows, monkeypatch):
    facts, _ = windows
    second = facts.pop("http://127.0.0.1:5012")
    assert call(monkeypatch, "sm_status")["isError"] is False
    facts["http://127.0.0.1:5012"] = second
    reply = call(monkeypatch, "state_get")
    assert reply["isError"] is True and content(reply)["refused"] == "ambiguous_window"
    status = content(call(monkeypatch, "sm_status"))
    assert len(status["windows"]) == 2 and "chip" not in status


@pytest.mark.parametrize("backend", ["claude", "codex"])
@pytest.mark.parametrize("pinned", [False, True])
def test_setup_preview_and_write_pin_opt_in(setup_client, home, backend, pinned):
    c = setup_client
    chip = c.get("/api/agent/chip").get_json()
    data = {"backend": backend, "pinned": pinned}
    preview = c.post("/api/agent/setup/connect", json=data).get_json()
    assert preview["ok"] and not preview["applied"]
    assert not (home / ".claude.json").exists() and not (home / ".codex/config.toml").exists()
    data.update(apply=True, expected_pin=preview["pin"])
    result = c.post("/api/agent/setup/connect", json=data).get_json()
    assert result["ok"] and result["applied"]
    if backend == "claude":
        env = json.loads((home / ".claude.json").read_text())["mcpServers"][agent_setup.SERVER_NAME]["env"]
        preview_env = preview["previews"]["mcp"]["after"]["env"]
    else:
        import tomllib
        env = tomllib.loads((home / ".codex/config.toml").read_text())["mcp_servers"][agent_setup.SERVER_NAME]["env"]
        preview_env = tomllib.loads(preview["previews"]["mcp"]["after"])["mcp_servers"][agent_setup.SERVER_NAME]["env"]
    assert env == preview_env
    if pinned:
        assert env["SM_CHIP"] == chip["pin"] and env["SM_URL"] == "http://localhost"
    else:
        assert "SM_CHIP" not in env and "SM_URL" not in env


@pytest.mark.parametrize("loaded,expected_pin", [(False, None), (True, "old-key")])
def test_setup_pin_refuses_unloaded_or_changed_chip(setup_client, home, monkeypatch, loaded, expected_pin):
    from flask import jsonify
    monkeypatch.setattr(agent_api, "chip", lambda: jsonify(loaded=loaded, pin="new-key"))
    r = setup_client.post("/api/agent/setup/connect", json={"backend": "claude", "apply": True,
                         "pinned": True, "expected_pin": expected_pin})
    assert r.status_code == (409 if loaded else 400)
    assert not (home / ".claude.json").exists()


@pytest.fixture
def manual(monkeypatch):
    body = {"ok": True, "family": "sample", "physics": "overview", "rules": {"r": "rule"},
            "closure_rules": [], "signal_map": None,
            "cases": [{"id": "C1", "name": "first", "geometry": "shape", "prescription": "act"},
                      {"id": "C2", "name": "second", "geometry": "other"}]}
    link = FakeLink({("GET", "/api/agent/manual/sample"): (200, body)})
    monkeypatch.setattr(mcp, "_link", link)
    return body


def test_manual_default_is_unchanged(manual):
    assert mcp.t_family_manual({"family": "sample"}) == manual


def test_manual_outline_is_ids_titles_and_sections(manual):
    assert mcp.t_family_manual({"family": "sample", "outline": True}) == {
        "family": "sample", "cases": [{"id": "C1", "title": "first"}, {"id": "C2", "title": "second"}],
        "sections": ["physics", "rules", "closure_rules"]}


def test_manual_one_case(manual):
    assert mcp.t_family_manual({"family": "sample", "case_id": "C2"}) == {"family": "sample", "case": manual["cases"][1]}


@pytest.mark.parametrize("section", ["physics", "rules", "closure_rules", "signal_map"])
def test_manual_one_section(manual, section):
    assert mcp.t_family_manual({"family": "sample", "section": section}) == {"family": "sample", section: manual[section]}


@pytest.mark.parametrize("extra", [
    {"outline": "true"}, {"case_id": 5}, {"case_id": "missing"}, {"section": "missing"},
    {"outline": True, "case_id": "C1"}, {"case_id": "C1", "section": "physics"},
])
def test_manual_invalid_selector_is_error(manual, extra):
    with pytest.raises(mcp.ToolError):
        mcp.t_family_manual({"family": "sample", **extra})


def test_manual_schema_and_size_hint():
    spec = mcp.TOOLS["family_manual"][0]
    assert "55 KB" in spec["description"]
    assert {"outline", "case_id", "section"} <= spec["inputSchema"]["properties"].keys()
    assert spec["inputSchema"]["required"] == ["family"]


@pytest.mark.parametrize("name,expected", [
    ("mcp__sm__state_get", True), ("mcp__quam-state-manager__state_get", True),
    ("mcp__smuggled__state_get", False), ("mcp__quam-state-manager-other__state_get", False),
    ("mcp__other__state_get", False), ("Read", False),
])
def test_relevance_uses_both_exact_server_names(name, expected):
    assert agent_link.is_sm_mcp_tool(name) is expected
    assert agent_api._relevant([{"tool_name": name}]) is expected


def test_hooks_exclude_sm_tools_already_reported_by_http():
    hooks = agent_setup.hooks_block("fake-hook")
    for event in ("PreToolUse", "PostToolUse", "PostToolUseFailure"):
        matcher = hooks[event][0]["matcher"]
        for server in agent_link.SM_MCP_SERVERS:
            assert re.search(matcher, f"mcp__{server}__state_get") is None
        for tool in ("Bash", "Edit", "Write", "MultiEdit"):
            assert re.fullmatch(matcher, tool)
    assert agent_link.SMLink("http://localhost", agent_id="codex")._headers()["X-SM-Agent"] == "codex"


def test_module_docstring_tool_count():
    count = re.search(r"(\d+) tools\.", mcp.__doc__)
    assert count and int(count[1]) == len(mcp.TOOLS)


def test_setup_checkbox_preview_and_apply_share_pin(tmp_path):
    node = shutil.which("node")
    assert node, "node is required for the Setup UI pin"
    script = r'''
const fs = require('fs'), vm = require('vm'), {JSDOM} = require('jsdom');
const dom = new JSDOM('<div id="agent-setup"><div id="as-body"></div></div>', {url:'http://localhost/'});
global.window = dom.window; global.document = window.document; global.localStorage = window.localStorage;
const calls = [];
const status = {ok:true, clis:{claude:{found:true},codex:{}}, claude:{},codex:{},record:{},context:{},journal:{},todo:[]};
global.fetch = window.fetch = async (url, opts) => {
  const body = opts && opts.body ? JSON.parse(opts.body) : null;
  calls.push({url,body});
  let response = /\/setup$/.test(url) ? status : {ok:true};
  if (/\/connect$/.test(url)) response = {ok:true, pin:'key-a', previews:{}, writes:{}};
  return {status:200, json:async()=>response};
};
vm.runInThisContext(fs.readFileSync('quam_state_manager/web/static/agent-setup.js','utf8'));
const tick = () => new Promise(resolve=>setTimeout(resolve,20));
const assert = require('assert');
(async()=>{
  window.AgentSetup.init(); await tick();
  const box = document.getElementById('as-pin-claude');
  assert(box && !box.checked, 'pin checkbox defaults off');
  box.checked = true;
  window.AgentSetup.preview('claude'); await tick();
  assert.equal(calls.filter(c=>/\/connect$/.test(c.url)).at(-1).body.pinned,true);
  box.checked = false; // changing the box cannot change the preview being applied
  window.AgentSetup.connect('claude'); await tick();
  const applied = calls.filter(c=>/\/connect$/.test(c.url)).at(-1).body;
  assert.equal(applied.pinned,true); assert.equal(applied.expected_pin,'key-a'); assert.equal(applied.apply,true);
})().catch(e=>{console.error(e); process.exitCode=1;});
'''
    result = subprocess.run([node, "-e", script], cwd=ROOT, text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
