"""The Agent home, the card feed and plans (docs/173 S6).

The feed is SM's own record: a person's message and the agent's answer are
events on disk, a plan is data the agent proposed or a /run line made, its
progress is what run_node reported. Rule 0 is a route: only a person's
click starts a plan, and that click arms, snapshots and tells the agent.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SELFCHECK = _ROOT / "tests" / "agent_panel_selfcheck.cjs"

from quam_state_manager.core import agent_plans, agent_session, limits, scheduler
from quam_state_manager.core import journal as journal_mod
from quam_state_manager.web import chat_api
from quam_state_manager.web.app import create_app
from tests.test_agent_runs import NODE_SRC, FakeRun, AGENT, HUMAN, _arm, _wait
from tests.test_chat_api import _FakeClaude, _FakeCodex


@pytest.fixture
def inst(tmp_path):
    return tmp_path / "_app_instance"


@pytest.fixture
def fakes(monkeypatch):
    monkeypatch.setitem(chat_api.BACKEND_CLASSES, "claude", _FakeClaude)
    monkeypatch.setitem(chat_api.BACKEND_CLASSES, "codex", _FakeCodex)
    monkeypatch.setattr(chat_api.ab, "detect", lambda exe: {"found": True, "version": "fake"})
    for k in ("FAKE_FAIL", "FAKE_LIMIT", "FAKE_CRASH", "FAKE_TOOL", "FAKE_ECHO_STDIN", "FAKE_LINGER"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def app(inst, fakes):
    return create_app(testing=True, instance_path=str(inst))


@pytest.fixture
def synth_folder(tmp_path):
    from tests.test_web import _make_state, _make_wiring
    d = tmp_path / "chip"
    d.mkdir()
    (d / "state.json").write_text(json.dumps(_make_state(), indent=2), encoding="utf-8")
    (d / "wiring.json").write_text(json.dumps(_make_wiring(), indent=2), encoding="utf-8")
    return d


@pytest.fixture
def cal(tmp_path):
    d = tmp_path / "calibrations"
    d.mkdir()
    (d / "05_power_rabi.py").write_text(NODE_SRC, encoding="utf-8")
    return d


@pytest.fixture
def c(app, synth_folder, cal):
    client = app.test_client()
    client.post("/load", data={"folder": str(synth_folder)})
    with app.app_context():
        from quam_state_manager.web import routes as r
        scheduler.save_settings(r._sched_inst(), {"env_python": sys.executable, "calibrations_folder": str(cal),
                                                  "global_simulate": False, "default_timeout_s": 60})
    yield client
    mgr = app.config.get("agent_chat")
    if mgr:
        for s in mgr.sessions.values():
            s.stop(now=True)
    reg = app.config.get("agent_run_registry")
    if reg:
        for m in reg.runs.values():
            reg.wait(m["key"], 15)


def _chip(c):
    """The records' KEY (session / approvals / plans / limits): <name>-<path hash>."""
    return c.get("/api/agent/chip").get_json()["chip_key"]


def _name(c):
    """The journal's name (what a person reads)."""
    return c.get("/api/agent/chip").get_json()["name"]


def _journal(c, inst):
    return journal_mod.read(str(inst), _name(c), datetime.now().strftime("%Y-%m-%d")) or ""


def _feed(c, after=0):
    return c.get(f"/api/agent/chat/cards?after={after}").get_json()


# ------------------------------------------------------------------ feed

class TestFeed:
    def test_a_persons_message_and_the_answer_are_cards_on_disk(self, c, inst):
        c.post("/api/agent/chat/start", json={"prompt": "hello there agent"}, headers=HUMAN)
        assert _wait(lambda: any(k["kind"] == "answer" for k in _feed(c)["cards"]))
        d = _feed(c)
        kinds = [k["kind"] for k in d["cards"]]
        assert kinds[0] == "user" and d["cards"][0]["text"] == "hello there agent" and d["cards"][0]["who"] == "human:kyunghoon"
        ans = [k for k in d["cards"] if k["kind"] == "answer"][0]
        assert ans["text"] == "answer 1: hello there agent" and "<p>answer 1: hello there agent</p>" in ans["html"]
        assert "tool" in kinds and [k for k in d["cards"] if k["kind"] == "tool"][0]["tool"] == "mcp__sm__sm_status"
        assert d["session"]["alive"] and d["file"]["backend"] == "claude" and d["qubits"] >= 1
        assert d["now"]["state"] in ("between", "running")
        # the cursor
        assert _feed(c, after=d["last"])["cards"] == []
        # on disk (a restart replays it)
        day = inst / "agent_events" / (datetime.now().strftime("%Y-%m-%d") + ".jsonl")
        lines = [json.loads(l) for l in day.read_text(encoding="utf-8").splitlines()]
        assert lines[0]["hook_event_name"] == "User" and lines[0]["origin"] == "chat"
        # a second message
        c.post("/api/agent/chat/send", json={"text": "and again"}, headers=HUMAN)
        assert _wait(lambda: sum(1 for k in _feed(c)["cards"] if k["kind"] == "user") == 2)

    def test_feed_without_a_chip_is_empty_but_ok(self, app):
        d = app.test_client().get("/api/agent/chat/cards").get_json()
        assert d["ok"] and d["chip"] is None and d["cards"] == [] and d["live"]["plans"] == []


# ----------------------------------------------------------------- plans

class TestPlans:
    def test_run_line_makes_a_draft_card_with_what_may_change(self, c, inst):
        r = c.post("/api/agent/plans", json={"run_line": "/run 05_power_rabi qA1 num_shots=200"}, headers=HUMAN).get_json()
        assert r["ok"], r
        p = r["plan"]
        assert p["status"] == "draft" and p["source"] == "run_cmd" and p["title"] == "/run 05_power_rabi qA1"
        assert p["steps"][0]["node"] == "05_power_rabi" and p["steps"][0]["targets"] == ["qA1"] and p["steps"][0]["params"] == {"num_shots": 200}
        assert p["may_change"], "the families' own update targets, filled per target"
        assert all(m["target"] == "qA1" and m["path"].startswith("qubits.qA1.") for m in p["may_change"])
        assert "waiting for Start" in _journal(c, inst)
        assert c.post("/api/agent/plans", json={"run_line": "/run 05_power_rabi nope"}, headers=HUMAN).status_code == 400
        assert c.post("/api/agent/plans", json={"run_line": "/run"}, headers=HUMAN).status_code == 400
        assert _feed(c)["live"]["plans"][0]["id"] == p["id"]
        assert c.get("/api/agent/chip").get_json()["plan"]["status"] == "draft"

    def test_the_agent_proposes_and_a_person_starts(self, c, inst, app):
        chip = _chip(c)
        r = c.post("/api/agent/plans", json={"title": "1Q bringup q1", "why": "the user asked",
                                              "steps": [{"node": "05_power_rabi", "targets": ["qA1"], "why": "rabi first"},
                                                        {"node": "05_power_rabi", "targets": ["qA1"], "params": {"num_shots": 400}}]}, headers=AGENT).get_json()
        assert r["ok"] and "nothing runs until a person presses Start" in r["how"]
        pid = r["plan"]["id"]
        assert "plan `1Q bringup q1` proposed (2 step(s))" in _journal(c, inst)
        assert c.post("/api/agent/plans", json={"title": "x", "steps": []}, headers=AGENT).status_code == 400
        assert c.post("/api/agent/plans", json={"title": "x", "steps": [{"node": "n", "targets": ["zz"]}]}, headers=AGENT).status_code == 400
        # the agent cannot start it
        assert c.post(f"/api/agent/plans/{pid}/start", json={}, headers=AGENT).status_code == 403
        # mode on the card
        assert c.post(f"/api/agent/plans/{pid}/mode", json={"mode": "auto"}, headers=HUMAN).get_json()["plan"]["mode"] == "auto"
        assert c.post(f"/api/agent/plans/{pid}/mode", json={"mode": "nope"}, headers=HUMAN).status_code == 400
        assert "mode set to auto by human:kyunghoon" in _journal(c, inst), "docs/173 S8: a mode change is a journal line"
        # THE click
        d = c.post(f"/api/agent/plans/{pid}/start", json={}, headers=HUMAN).get_json()
        assert d["ok"] and d["session_started"] is True and d["pre_ts"]
        p = d["plan"]
        assert p["status"] == "running" and p["started_by"] == "human:kyunghoon" and p["mode"] == "auto"
        rec = agent_session.load(str(inst), chip)
        assert rec["start_token"] and rec["plan_id"] == pid and rec["mode"] == "auto", "Start arms; the session carries the plan's mode"
        assert limits.load(str(inst), chip)["mode"] == "ask-writes", "review R1-M4: the chip's default never changes"
        assert "STARTED by human:kyunghoon (mode auto" in _journal(c, inst)
        # the snapshot the plan can be reverted to, labelled
        with app.app_context():
            from quam_state_manager.web import routes as rt
            snaps = rt._history().list_snapshots(rt._active_path())
        labels = [getattr(s, "label", None) or (s.get("label") if isinstance(s, dict) else None) for s in snaps]
        assert any(l and l.startswith("before plan 1Q bringup") for l in labels), labels
        # the agent got the plan as its first message (the fake echoes the first 20 chars)
        assert _wait(lambda: any(k["kind"] == "answer" and k["text"].startswith("answer 1: The human (") for k in _feed(c)["cards"]))
        assert any(k["kind"] == "user" and k["text"].startswith("[Start] plan") for k in _feed(c)["cards"])
        # not twice
        assert c.post(f"/api/agent/plans/{pid}/start", json={}, headers=HUMAN).status_code == 409

    def test_run_node_reports_into_the_plan_card(self, c, inst, monkeypatch):
        fr = FakeRun()
        monkeypatch.setattr(scheduler, "_run_item", fr)
        chip = _chip(c)
        r = c.post("/api/agent/plans", json={"title": "p", "steps": [{"node": "05_power_rabi", "targets": ["qA1"]}]}, headers=AGENT).get_json()
        pid = r["plan"]["id"]
        c.post(f"/api/agent/plans/{pid}/mode", json={"mode": "auto"}, headers=HUMAN)
        assert c.post(f"/api/agent/plans/{pid}/start", json={}, headers=HUMAN).get_json()["ok"]
        body = {"node": "05_power_rabi", "targets": ["qA1"], "reason": "step 0", "wait_s": 20, "plan_id": pid, "step": 0}
        res = c.post("/api/agent/run-node", json=body, headers=AGENT).get_json()
        assert res["ok"] and res["result"]["applied"] is True
        p = c.get(f"/api/agent/plans/{pid}").get_json()["plan"]
        st = p["steps"][0]
        assert st["status"] == "done" and st["run_key"] == res["key"] and st["n_writes"] == 1 and st["applied"] is True
        assert p["status"] == "done" and p["counts"]["done"] == 1 and p["counts"]["applied"] == 1 and p["ended"]
        assert c.get("/api/agent/chip").get_json()["plan"]["counts"]["done"] == 1
        feed = _feed(c)
        assert feed["live"]["runs"][-1]["key"] == res["key"] and feed["live"]["plans"][-1]["status"] == "done"

    def test_cancel_and_stop_leave_the_record(self, c, inst):
        r = c.post("/api/agent/plans", json={"run_line": "/run 05_power_rabi qA1"}, headers=HUMAN).get_json()
        pid = r["plan"]["id"]
        d = c.post(f"/api/agent/plans/{pid}/cancel", json={}, headers=HUMAN).get_json()
        assert d["plan"]["status"] == "cancelled" and d["plan"]["steps"][0]["status"] == "cancelled"
        assert "cancelled by human:kyunghoon" in _journal(c, inst)
        assert c.post(f"/api/agent/plans/{pid}/start", json={}, headers=HUMAN).status_code == 409
        assert c.get("/api/agent/plans/nope").status_code == 404
        # a running plan closes on Stop: "Stopped by <who>", pending steps cancelled
        r = c.post("/api/agent/plans", json={"run_line": "/run 05_power_rabi qA1"}, headers=HUMAN).get_json()
        pid2 = r["plan"]["id"]
        assert c.post(f"/api/agent/plans/{pid2}/start", json={}, headers=HUMAN).get_json()["ok"]
        assert c.post("/api/agent/session/stop", json={"mode": "now"}, headers=HUMAN).get_json()["ok"]
        p = c.get(f"/api/agent/plans/{pid2}").get_json()["plan"]
        assert p["status"] == "stopped" and p["ended_by"] == "human:kyunghoon" and p["note"] == "stop now"
        assert p["steps"][0]["status"] == "cancelled"
        assert c.get("/api/agent/chip").get_json()["plan"]["status"] == "stopped"

    def test_parse_run_line(self):
        assert agent_plans.parse_run_line("/run 05_power_rabi q1 q2 num_shots=200 simulate=true amp=0.5 name=x") == \
            {"node": "05_power_rabi", "targets": ["q1", "q2"], "params": {"num_shots": 200, "simulate": True, "amp": 0.5, "name": "x"}}
        assert agent_plans.parse_run_line("/run 02_res q1,q2")["targets"] == ["q1", "q2"]
        assert agent_plans.parse_run_line("hello") is None
        assert "usage" in agent_plans.parse_run_line("/run")["error"]

    def test_plan_model(self, tmp_path):
        rec = agent_plans.add(tmp_path, "PJ", title="t", steps=[{"node": "a", "targets": ["q1"]}, {"node": "b", "targets": ["q1"]}],
                              mode="auto", created_by="by_claude")
        assert rec["status"] == "draft" and agent_plans.counts(rec)["pending"] == 2
        agent_plans.update(tmp_path, "PJ", rec["id"], status="running")
        assert agent_plans.step_for(agent_plans.get(tmp_path, "PJ", rec["id"]), step=None, node="b", targets=["q1"])["i"] == 1
        agent_plans.step_update(tmp_path, "PJ", rec["id"], 0, status="done", n_writes=2, applied=True)
        assert agent_plans.get(tmp_path, "PJ", rec["id"])["status"] == "running", "one step left"
        agent_plans.step_update(tmp_path, "PJ", rec["id"], 1, status="failed")
        p = agent_plans.get(tmp_path, "PJ", rec["id"])
        assert p["status"] == "failed" and p["summary"]["failed"] == 1 and p["ended"]
        assert agent_plans.running(tmp_path, "PJ") is None
        with pytest.raises(ValueError):
            agent_plans.normalize_steps([{"targets": ["q1"]}])
        with pytest.raises(ValueError):
            agent_plans.normalize_steps([{"node": "n"}] * (agent_plans.MAX_STEPS + 1))


# ------------------------------------------------------------------ home

class TestHome:
    def test_home_is_the_agent_home_when_a_chip_is_open(self, c):
        html = c.get("/").get_data(as_text=True)
        assert 'id="agent-home"' in html and 'id="agent-popover"' in html
        # customer feedback 2026-09-08: no tool-row Agent button any more -- the Agent is a
        # nav entry (id nav-agent, above the Calibration log) that opens /agent in the pane
        assert 'class="sidebar-tool agent-btn"' not in html and 'id="nav-agent"' in html
        assert re.search(r'href="/agent"[^>]*hx-get="/agent"[^>]*hx-target="#table-pane"', html)
        assert re.search(r'id="nav-agent" class="active"', html), "with a chip open, / IS the Agent home: the entry is active"
        assert "agent.js" in html and '"label": "Agent home"' in html and '"url": "/agent"' in html

    def test_agent_is_a_page_in_the_pane_and_a_full_page(self, c):
        # htmx (the sidebar click): the partial only -- the mount point, no shell
        part = c.get("/agent", headers={"HX-Request": "true"}).get_data(as_text=True)
        assert 'id="agent-home"' in part and "<aside" not in part and 'id="sidebar"' not in part
        # a full load (F5 on /agent): the whole page with the entry active
        full = c.get("/agent").get_data(as_text=True)
        assert 'id="agent-home"' in full and 'id="sidebar"' in full and re.search(r'id="nav-agent" class="active"', full)

    def test_agent_without_a_chip_says_so(self, app):
        cl = app.test_client()
        part = cl.get("/agent", headers={"HX-Request": "true"}).get_data(as_text=True)
        assert "Open a chip first" in part and 'id="agent-home"' not in part
        r = cl.get("/agent")
        assert r.status_code == 302 and "landing=1" in r.headers["Location"]

    def test_home_without_a_chip_is_the_landing_with_the_current_project_button(self, app, monkeypatch):
        from quam_state_manager.core import qualibrate_config
        monkeypatch.setattr(qualibrate_config, "tray_status", lambda: {"config_exists": True, "active": "PJ_10082026",
                                                                       "state_raw": None, "state_native": None, "state_exists": False})
        html = app.test_client().get("/").get_data(as_text=True)
        assert 'id="agent-home"' not in html and 'class="landing-projects"' in html
        assert "Open QUAlibrate's current project: PJ_10082026" in html and 'name="project" value="PJ_10082026"' in html
        assert 'id="agent-popover"' in html, "the floating panel is on every page"

    def test_the_floats_home_link_is_a_plain_navigation(self, c):
        """review R2-8: the Agent home is a full page, so the float's 'home' is a
        plain href -- an hx-get into #table-pane put the landing INSIDE a pane."""
        import re
        html = c.get("/").get_data(as_text=True)
        m = re.search(r'<a class="agent-home-link"[^>]*>', html)
        assert m and 'href="/"' in m.group(0) and "hx-get" not in m.group(0) and "hx-target" not in m.group(0)

    def test_the_setup_page_is_wired(self, c):
        """docs/173 S7: /agent/setup renders the shell inside the shell page with
        its own bundle; the HX form is the partial alone."""
        html = c.get("/agent/setup").get_data(as_text=True)
        assert 'id="agent-setup"' in html and "agent-setup.js" in html and 'id="as-body"' in html
        assert '"agent_setup": ["agent-setup.js"]' in html and '"agent_setup": ["agent_setup"]' in html, "bundle + page table"
        part = c.get("/agent/setup", headers={"HX-Request": "true"}).get_data(as_text=True)
        assert 'id="agent-setup"' in part and "<html" not in part


    def test_the_agent_css_carries_the_strip_the_timeline_and_the_pinned_composer(self):
        """customer feedback 2026-09-08 ("hard to read"): one column, a sticky status strip,
        a timeline feed that scrolls inside the pane, the composer pinned at the bottom.
        Layout is CSS, so the rules are pinned by text; what they DO on screen (the composer
        never cut off, buttons never stretched) was measured in real Chrome."""
        css = (_ROOT / "quam_state_manager" / "web" / "static" / "style.css").read_text(encoding="utf-8")
        start = css.index("docs/173 S6: the Agent home")
        blk = css[start:css.index("/* docs/168 + on-site 2026-09-07", start)]
        # one column: the pane is a flex column, the feed fills it, the old two-column grid is gone
        assert "#table-pane:has(> .agent-home) { display: flex; flex-direction: column; overflow: hidden; }" in blk
        assert ".ag-root { display: flex; flex-direction: column; flex: 1 1 auto; min-height: 0; gap: .4rem; font-size: .93em; }" in blk
        assert "grid-template-columns: minmax(0, 1fr) 17rem" not in css and ".ag-left" not in css
        assert re.search(r"\.ag-now \{[^}]*position: sticky; top: 0;", blk)
        assert re.search(r"\.ag-cards \{[^}]*flex: 1 1 auto; min-height: 0; overflow-y: auto;", blk)
        assert "#table-pane:has(> .agent-home) .ag-cards, .agent-popover .ag-cards { max-height: none; }" in blk
        assert re.search(r"\.ag-composer \{[^}]*flex: 0 0 auto; border-top:", blk)
        # timeline rows: a 3.2rem monospace gutter; the person's bubble tinted from the primary colour
        assert re.search(r"\.ag-card \{ display: grid; grid-template-columns: 3\.2rem minmax\(0, 1fr\)", blk)
        assert re.search(r"\.ag-t \{[^}]*monospace", blk)
        assert "color-mix(in srgb, var(--pico-primary) 10%, transparent)" in blk and "max-width: 78%" in blk
        # the clamp, the tool group, one pill style, the approval accent
        assert re.search(r"\.ag-md\.ag-clamp \{ max-height: [\d.]+em; overflow: hidden;", blk)
        assert '.ag-card[data-expanded="1"] .ag-md.ag-clamp { max-height: none; }' in blk
        assert ".ag-toolgroup > summary" in blk and ".ag-card.ag-in-group .ag-body" in blk
        assert re.search(r"\.ag-plan-st, \.ag-step-st \{[^}]*border-radius: 999px", blk)
        assert ".ag-card.ag-approval .ag-body { border-left: 3px solid var(--ag-accent, #d98c00); }" in blk
        # review round 1: the accent is defined (it never was), and it is a BORDER colour --
        # as pill TEXT the bare #d98c00 measured 2.7:1 on the light card, so the waiting pill
        # mixes it towards the theme's own foreground; no `color:` in the block may use it bare
        assert ".ag-root { --ag-accent: #d98c00; }" in blk
        assert re.search(r"\.ag-st-waiting \{ color: color-mix\(in srgb, var\(--ag-accent, #d98c00\) \d+%, var\(--pico-color\)\); \}", blk)
        for m in re.finditer(r"(?<![-\w])color:\s*([^;}]+)", blk):
            v = m.group(1).strip()
            assert "#d98c00" not in v or v.startswith("color-mix("), f"the accent used bare as a text colour: {m.group(0)}"
        # review round 2, measured from Chrome's computed colours: the same trap in three
        # more places -- #2e7d32 as TEXT is 2.98:1 on the dark card, #8b5cf6 likewise. Any
        # literal green/violet used as a text colour must be mixed towards the foreground.
        for m in re.finditer(r"(?<![-\w])color:\s*([^;}]+)", blk):
            v = m.group(1).strip()
            for lit in ("#2e7d32", "#8b5cf6"):
                assert lit not in v or v.startswith("color-mix("), f"a literal used bare as a text colour: {m.group(0)}"
        # the strip is ONE row at a normal width: the state text truncates, the doors stay
        assert re.search(r"\.ag-now-main \{[^}]*flex: 1 1 0;[^}]*white-space: nowrap;[^}]*flex-wrap: nowrap;", blk, re.S)
        # ...and only the STATE text gives way: "armed" and the counts are short and
        # load-bearing, so a per-segment ellipsis ("ar…", "today 9 ev…") is the wrong cut
        assert ".ag-now-main > * { flex: 0 0 auto; }" in blk
        assert re.search(r"\.ag-now-main > \.ag-now-state \{[^}]*text-overflow: ellipsis;", blk)
        # ...but the FLOAT is narrow by design: there the strip wraps rather than clipping
        # its links off the panel edge (seen in the round-2 screenshots)
        assert ".ag-compact .ag-now-main, .ag-compact .ag-now-acts { flex-wrap: wrap; white-space: normal; }" in blk
        assert re.search(r"\.ag-now-acts \{[^}]*flex-wrap: nowrap;[^}]*flex: 0 0 auto;", blk)
        # the float's own minimum keeps strip + composer on screen (the customer's first
        # complaint reproduced at the panel's smallest size): the feed absorbs the squeeze
        assert ".agent-popover .ag-compact .ag-cards { min-height: 0; }" in blk
        assert re.search(r"\.agent-popover \{[^}]*min-height: (2[4-9]|[3-9]\d)rem;", blk, re.S)
        assert re.search(r"\.ag-md h1, \.ag-md h2, \.ag-md h3[^{]*\{ font-size: 1em; font-weight: 700;", blk)
        # Pico's width:100% never reaches a button / input / select inside the page
        assert (".ag-root button, .ag-root [type=submit], .ag-root [type=button], .ag-root select, "
                ".ag-root input:not([type=checkbox]) { width: auto; margin: 0; }") in blk
        # The only uppercase text on the page is a STATUS PILL: the plan/step
        # states, the simulated flag, and the wiring strip's CONNECTED /
        # NOT CONNECTED / NO CLI badge — which is the same kind of thing and
        # deliberately wears the same shape. Anything else shouting in capitals
        # is the defect this pin exists to catch.
        upper = [ln for ln in blk.splitlines() if "text-transform: uppercase" in ln]
        assert len(upper) == 3 and all(
            ln.startswith((".ag-plan-st, .ag-step-st", ".ag-sim", ".ag-wire-badge"))
            for ln in upper), upper
        # both themes: no hard-coded surface colour in the block -- every background is a var(),
        # a color-mix of one, none/transparent, or the state DOT's literal (the topbar pill's
        # own accents, .agent-pill-dot, already shown in both themes)
        # (judged per DECLARATION -- a rule whose border is a var() must not excuse its background)
        for rule in blk.split("}"):
            if "{" not in rule:
                continue
            sel, decl = rule.rsplit("{", 1)
            for val in re.findall(r"background:\s*([^;]+)", decl):
                v = val.strip()
                if v.startswith(("var(", "color-mix(", "linear-gradient(", "none", "transparent")) or ".agent-pill-dot" in sel:
                    continue
                raise AssertionError(f"a background that ignores the theme: {sel.strip().splitlines()[-1]} -> {v}")

    def test_bridge_knows_the_plan_tools(self):
        from quam_state_manager import mcp
        assert "plan_propose" in mcp.TOOLS and "plan_status" in mcp.TOOLS
        assert "plan_propose" in mcp.WRITE_TOOLS and "plan_status" not in mcp.WRITE_TOOLS
        props = mcp.TOOLS["run_node"][0]["inputSchema"]["properties"]
        assert "step" in props and "plan_id" in props


class TestReviewRound1:
    """The review of the 2026-09-08 redesign, three confirmed issues."""

    def test_the_pane_height_covers_the_banners_above_the_layout(self):
        """The composer was pinned to a pane sized `100vh - topbar`, but the banner slots
        between the bar and .app-layout (the docs/80 two-window banner, live-diverged, the
        type alarm, GC, diagnostics) push the layout down -- measured in real Chrome: a
        1000px viewport, a 1070px document, the composer's form row below the fold. The
        published number is now the layout's own document-relative top, and the observer
        watches every in-flow sibling above it. jsdom computes no layout, so the effect is
        measured in Chrome; this pins the mechanism."""
        js = (_ROOT / "quam_state_manager" / "web" / "static" / "app.js").read_text(encoding="utf-8")
        blk = js.split("window.TopbarHeight = (function () {", 1)[1][:3000]
        # measure = .app-layout's top in DOCUMENT coordinates (sticky bar or not, scrolled or not)
        measure = blk.split("function measure() {", 1)[1].split("function publish", 1)[0]
        assert "querySelector('.app-layout')" in measure
        assert re.search(r"lr\.top \+ \(window\.pageYOffset \|\| 0\)", measure)
        # the bar-only path survives as the fallback (test_web pins the hidden-bar zero on it)
        assert "classList.contains('topbar-hidden')) return 0" in measure
        # the ResizeObserver covers every sibling before the layout, not only the bar.
        # Round 2 caught this assert being vacuous: the SEEDING line already contains
        # "previousElementSibling", so a walk cut to one element passed. Pin the WALK.
        above = blk.split("function aboveLayout() {", 1)[1].split("function start", 1)[0]
        assert re.search(r"while \(el\)\s*\{[^}]*out\.push\(el\)[^}]*el = el\.previousElementSibling", above), \
            "aboveLayout must WALK up the siblings, not take only the one before the layout"
        start = blk.split("function start() {", 1)[1]
        assert "aboveLayout().forEach(function (el) { if (seen.indexOf(el) < 0) { seen.push(el); ro.observe(el); } })" in start
        assert "MutationObserver" in start and "{ childList: true }" in start

    def test_the_strip_is_not_a_live_region_and_keeps_the_focus(self):
        """role=status on the whole strip re-announced state, counts and every button label
        on every poll; the wholesale innerHTML dropped the focus to <body>. The executed
        pins live in agent_panel_selfcheck.cjs (jsdom asserts document.activeElement); this
        guards the markup for an environment without node."""
        js = (_ROOT / "quam_state_manager" / "web" / "static" / "agent.js").read_text(encoding="utf-8")
        assert '<div class="ag-now"></div>' in js and '"ag-now" role=' not in js
        assert '<span class="ag-now-sr visually-hidden" role="status"></span>' in js
        assert "if (sr && sr.textContent !== srText) sr.textContent = srText;" in js
        assert "function swapHtml(el, html)" in js and "el.__agHtml === html) return false" in js
        assert "again.focus()" in js


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_agent_panel_selfcheck():
    """The REAL agent.js under jsdom (tests/agent_panel_selfcheck.cjs): the S6/S8 pins plus
    the 2026-09-08 redesign -- the status strip, the timeline rows, consecutive tool events
    folding into one row (a failed one breaks it), the show-more clamp whose state survives a
    re-render, the pinned composer in both mounts. Whether Pico still STRETCHES a button or
    where the composer lands on screen are layout facts jsdom does not compute: measured in
    real Chrome instead."""
    node = shutil.which("node")
    try:
        subprocess.run([node, "-e", "require('jsdom')"], check=True, capture_output=True, timeout=30)
    except Exception:
        pytest.skip("jsdom not installed")
    r = subprocess.run([node, str(_SELFCHECK)], capture_output=True, text=True, encoding="utf-8",
                       timeout=180, cwd=str(_ROOT))
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.count("ok - ") >= 90, r.stdout


class TestOneBadTokenCannotFreezeTheFeed:
    """`/run 12_ramsey q2 detuning=NaN` froze the Agent panel for good.

    Found by the browser stress round and confirmed twice. `_coerce` turned the
    token into `float('nan')`, it was persisted into the plan store, and
    `jsonify` emitted a bare `NaN` — which is not JSON. Every later
    `GET /api/agent/chat/cards` then returned **200 with an unparseable body**:
    the client's `r.json().catch(()=>({}))` swallowed it, so no error was shown
    anywhere, the composer still said "plan card ready", and the feed never
    updated again. It survived a server restart, because the poison was on
    disk; the only escape was pushing the plan out of the last-6 window.
    """

    BAD = ["NaN", "nan", "-NaN", "inf", "-inf", "Infinity", "1e999", "-1e999"]

    def test_a_non_finite_token_stays_the_word_that_was_typed(self):
        from quam_state_manager.core import agent_plans as ap
        for tok in self.BAD:
            assert ap._coerce(tok) == tok, tok
        # …and a real number is still a number
        assert ap._coerce("12") == 12
        assert ap._coerce("1.5") == 1.5
        assert ap._coerce("1e3") == 1000.0
        assert ap._coerce("true") is True and ap._coerce("none") is None

    def test_the_feed_stays_parseable_after_one(self, c, inst):
        """The property that actually matters: valid JSON on the wire."""
        import json as _json
        for tok in self.BAD:
            r = c.post("/api/agent/plans",
                       json={"run_line": "/run 05_power_rabi qA1 detuning=" + tok},
                       headers=HUMAN)
            assert r.status_code in (200, 400), (tok, r.status_code)
            body = c.get("/api/agent/chat/cards").get_data(as_text=True)
            feed = _json.loads(body)    # raises on a BARE NaN / Infinity
            # …and the token survived as the word, quoted, which is what makes
            # the body parseable in the first place
            got = [st.get("params", {}).get("detuning")
                   for pl in feed["live"]["plans"] for st in pl.get("steps", [])]
            assert tok in got, (tok, got)
            assert all(not isinstance(v, float) for v in got), got

    def test_the_store_refuses_to_write_one_even_if_it_gets_that_far(self, inst):
        """Defence in depth: a value from the bridge, a hand-edited file or a
        future caller must fail the write that produced it, not the next
        twenty reads."""
        import pytest as _pytest
        from quam_state_manager.core import agent_plans as ap
        with _pytest.raises(ValueError):
            ap._save(inst, "chipA", [{"id": "p1", "steps": [{"params": {"x": float("nan")}}]}])
