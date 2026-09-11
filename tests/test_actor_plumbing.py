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


class TestTheNameBoxIsEnglishOnly:
    """A Hangul name killed the whole Agent panel; the answer is the rule, not
    a transport trick.

    Found by the browser stress round and confirmed twice in real Chrome:
    `agent.js` put the name straight into an HTTP header, a header value must
    be ISO-8859-1, and Chrome refuses the whole fetch BEFORE sending —

        TypeError: Failed to execute 'fetch' on 'Window': ... String contains
        non ISO-8859-1 code point.

    so the feed, plan creation, Start, Stop and approvals were all dead against
    a healthy server, and the name lives in localStorage so it stayed dead;
    `/agent/setup`, the page that could clear it, rendered blank.

    The first fix percent-encoded the header. The user's decision (2026-09-11)
    was the simpler one — *"한글이름 그냥 폐기해. 그냥 SM에서는 영어로만"* — so
    the encoding is gone and SM works in English. **What you SAY to the agent
    is the explicit exception** and is pinned as such below: that is a JSON
    body and takes any language.

    Why every earlier pin missed it: they hand the raw string to Flask's test
    client, which is not a path a browser can take. Hence the first test here
    pins the BROWSER's constraint, not the server's tolerance.
    """

    _ROOT = Path(__file__).resolve().parent.parent

    def test_the_client_strips_a_name_a_browser_could_not_send(self):
        js = (self._ROOT / "quam_state_manager/web/static/agent.js").read_text(encoding="utf-8")
        assert "function asciiActor(" in js
        # the stripper is on the READ path too, so a name saved before the rule
        # existed cannot reach a header either
        assert "asciiActor(localStorage.getItem(\"quam_actor_name\"))" in js
        assert "v = asciiActor(v);" in js
        setup = (self._ROOT / "quam_state_manager/web/static/agent-setup.js").read_text(encoding="utf-8")
        assert "replace(/[^\\x20-\\x7E]/g" in setup

    def test_nothing_percent_encodes_the_header_any_more(self):
        """The encoding was reverted with the decision: with English-only names
        it is a no-op, and `unquote` on the server would mangle a literal `%`
        in a name for no gain."""
        for rel in ("quam_state_manager/web/static/agent.js",
                    "quam_state_manager/web/static/agent-setup.js"):
            js = (self._ROOT / rel).read_text(encoding="utf-8")
            assert 'h["X-SM-Actor"] = who;' in js, rel
            assert "encodeURIComponent(who)" not in js, rel
        rt = (self._ROOT / "quam_state_manager/web/routes.py").read_text(encoding="utf-8")
        blk = rt[rt.index("def _request_actor("):rt.index("def _active_ctx(")]
        assert "unquote" not in blk

    def test_the_server_side_writer_follows_the_same_rule(self):
        from quam_state_manager.web.agent_api import _ascii_actor
        assert _ascii_actor("kyunghoon") == "kyunghoon"
        assert _ascii_actor("정경훈") == ""
        assert _ascii_actor("Min 정 ji") == "Min  ji".strip()
        assert _ascii_actor("a%b") == "a%b"        # ASCII punctuation is a name
        assert _ascii_actor(None) == ""

    def test_the_writer_in_agent_api_calls_the_rule(self):
        """`_ascii_actor` existing is not the rule; being CALLED is.

        `_stage_writes` builds an `X-SM-Actor` header for the internal apply,
        and a sweep found that reverting it to the raw value broke nothing —
        the rule was pinned, its only server-side caller was not."""
        import io
        import tokenize
        src = (self._ROOT / "quam_state_manager/web/agent_api.py").read_text(encoding="utf-8")
        blk = src[src.index("def _stage_writes("):]
        blk = blk[:blk.index("\ndef ", 10)]
        code = " ".join(
            t.string for t in tokenize.generate_tokens(io.StringIO(blk).readline)
            if t.type not in (tokenize.COMMENT, tokenize.STRING))
        assert "X-SM-Actor" in blk
        assert "_ascii_actor" in code, "the header is built without the rule"

    def test_a_header_a_browser_cannot_send_is_never_built(self):
        """The property that actually broke it, stated once: whatever survives
        the rule must be encodable as ISO-8859-1."""
        from quam_state_manager.web.agent_api import _ascii_actor
        for raw in ["kyunghoon", "정경훈", "kyunghoon 🙂", "박OO", "a\tb", ""]:
            _ascii_actor(raw).encode("latin-1")

    def test_but_what_you_say_to_the_agent_is_not_restricted(self, client):
        """The named exception. A chat message is a JSON body, not a header."""
        js = (self._ROOT / "quam_state_manager/web/static/agent.js").read_text(encoding="utf-8")
        # the composer's text is sent as JSON, never as a header
        assert "JSON.stringify(body)" in js
        sent = js[js.index("function submit("):]
        sent = sent[:2000]
        assert "asciiActor" not in sent, "the message itself must not be stripped"

    def test_the_setup_page_survives_a_refused_fetch(self):
        """Kept from the first fix and independent of the language rule: this
        is the page a person comes to when something is already wrong, and it
        rendered NOTHING."""
        js = (self._ROOT / "quam_state_manager/web/static/agent-setup.js").read_text(encoding="utf-8")
        api = js[js.index("function api("):js.index("function pretty(")]
        assert "status: 0" in api and "Promise.reject" in api
        load = js[js.index("function load()"):]
        load = load[:load.index("function preview(")]
        assert "ag-err" in load
