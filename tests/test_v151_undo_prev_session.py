"""v1.5.1 T1-F2: Ctrl+Z on reopen reaches an earlier session's journal.

The undo journal survives a restart, so the first Ctrl+Z after reopening SM
undid a 2-day-old edit, and with "Ctrl+Z writes live: ON" one keypress would
have written that inverse to the live chip; the toast never said how old the
edit was, and it covered the Undo / Redo panel's button.

Now: every journal-walk toast names the edit's age; a step that would write
the live chip with an EARLIER session's edit asks once first (nothing staged
or written while it asks; the answer travels as ``ack_prev_session``, and
after one yes the session is not asked again); the page side
(tests/v151_undo_prev_session_selfcheck.cjs) asks, shows the age in the
panel and lifts the toasts above it (the style rule pinned here).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

ROOT = Path(__file__).resolve().parents[1]
_WIRING = {"network": {"host": "1.1.1.1", "cluster_name": "C1"}}
TWO_DAYS = 2 * 86400 + 600


def _state(off=0.08, f01=5.0e9):
    return {"qubits": {"qA1": {"id": "qA1", "f_01": f01, "z": {"joint_offset": off}}},
            "qubit_pairs": {}, "active_qubit_names": ["qA1"]}


@pytest.fixture
def env(tmp_path):
    live = tmp_path / "chips" / "live"
    live.mkdir(parents=True)
    (live / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    assert c.post("/settings/undo-live", data={"enabled": "1"}).get_json()["enabled"] is True
    return {"app": app, "client": c, "live": live}


def _ctx(env):
    return env["app"].config["contexts"][env["app"].config["active_context"]]


def _trig(r) -> dict:
    raw = r.headers.get("HX-Trigger")
    return json.loads(raw) if raw else {}


def _edit_apply(c, value, dot_path="qubits.qA1.z.joint_offset"):
    r = c.post("/field/edit-batch", json={"updates": [{"dot_path": dot_path, "value": str(value)}],
                                          "expect_chip": ""})
    assert r.status_code == 200 and r.get_json()["ok"], r.data
    assert c.post("/state/apply-to-live").status_code == 200


def _live(env, key="joint_offset"):
    q = json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))["qubits"]["qA1"]
    return q["z"]["joint_offset"] if key == "joint_offset" else q[key]


def _age_units(env, monkeypatch, seconds=TWO_DAYS):
    """The journal as a restart finds it: every unit recorded before this
    process started, ``seconds`` ago."""
    ctx = _ctx(env)
    then = time.time() - seconds
    for u in ctx["undo_units"]:
        u["ts"] = then
        (u.setdefault("meta", {}))["at"] = then
    monkeypatch.setattr(routes, "_PROCESS_STARTED_AT", time.time())


def test_an_earlier_sessions_edit_is_not_written_live_without_a_yes(env, monkeypatch):
    c = env["client"]
    _edit_apply(c, 0.10)
    _age_units(env, monkeypatch)
    ctx = _ctx(env)
    cursor = ctx["undo_cursor"]
    r = c.post("/undo")
    t = _trig(r)
    assert "undoAskPrevSession" in t, t
    q = t["undoAskPrevSession"]
    assert "2 days ago" in q["message"] and "earlier SM session" in q["message"], q
    assert "qubits.qA1.z.joint_offset" in q["message"]
    assert q["unit_id"] == str(ctx["undo_units"][cursor - 1]["id"])
    # nothing happened anywhere while the question is open
    assert _live(env) == 0.10
    assert not ctx["store"].change_log and ctx["undo_cursor"] == cursor
    assert "cellsReverted" not in t

    # the yes for THIS unit writes it, and the toast says how old it was
    r = c.post("/undo", data={"ack_prev_session": q["unit_id"]})
    cr = _trig(r)["cellsReverted"]
    assert cr["live"] is True and _live(env) == 0.08, cr
    assert "(edit from 2 days ago)" in cr["message"], cr["message"]
    assert cr["age"] == "from 2 days ago"


def test_a_yes_for_another_unit_is_not_a_yes(env, monkeypatch):
    c = env["client"]
    _edit_apply(c, 0.10)
    _age_units(env, monkeypatch)
    r = c.post("/undo", data={"ack_prev_session": "some-other-unit"})
    assert "undoAskPrevSession" in _trig(r)
    assert _live(env) == 0.10


def test_asked_once_per_session(env, monkeypatch):
    c = env["client"]
    _edit_apply(c, 0.10)
    _edit_apply(c, 5.1e9, "qubits.qA1.f_01")
    _age_units(env, monkeypatch)
    q = _trig(c.post("/undo"))["undoAskPrevSession"]
    assert _trig(c.post("/undo", data={"ack_prev_session": q["unit_id"]}))["cellsReverted"]["live"] is True
    assert _live(env, "f_01") == 5.0e9
    r = c.post("/undo")                       # the next earlier-session unit: no second question
    t = _trig(r)
    assert "undoAskPrevSession" not in t and t["cellsReverted"]["live"] is True, t
    assert _live(env) == 0.08


def test_this_sessions_edit_is_undone_live_without_a_question(env):
    c = env["client"]
    _edit_apply(c, 0.10)
    t = _trig(c.post("/undo"))
    assert "undoAskPrevSession" not in t
    assert t["cellsReverted"]["live"] is True and _live(env) == 0.08
    assert "edit from" not in t["cellsReverted"]["message"], "a fresh edit needs no age"


def test_this_sessions_older_edit_says_its_age(env, monkeypatch):
    c = env["client"]
    _edit_apply(c, 0.10)
    then = time.time() - 3 * 3600
    for u in _ctx(env)["undo_units"]:
        u["ts"] = then
    monkeypatch.setattr(routes, "_PROCESS_STARTED_AT", then - 60)    # same session
    t = _trig(c.post("/undo"))
    assert "undoAskPrevSession" not in t
    cr = t["cellsReverted"]
    assert cr["live"] is True and "(edit from 3 h ago)" in cr["message"], cr["message"]


def test_with_ctrl_z_writing_live_off_nothing_is_asked_and_the_age_is_said(env, monkeypatch):
    c = env["client"]
    assert c.post("/settings/undo-live", data={"enabled": "0"}).get_json()["enabled"] is False
    _edit_apply(c, 0.10)
    _age_units(env, monkeypatch)
    t = _trig(c.post("/undo"))
    assert "undoAskPrevSession" not in t
    cr = t["cellsReverted"]
    assert cr["live"] is False and "(edit from 2 days ago)" in cr["message"], cr
    assert _live(env) == 0.10                # staged only, as before


def test_the_toasts_sit_above_the_open_undo_panel():
    css = (ROOT / "quam_state_manager" / "web" / "static" / "style.css").read_text(encoding="utf-8")
    m = re.search(r"body\.undo-trail-on #status-bar\s*\{([^}]*)\}", css)
    assert m, "no lift rule for the toast sink"
    assert re.search(r"bottom:\s*calc\(5rem \+ var\(--undo-trail-h", m.group(1)), m.group(1)
    panel = re.search(r"\.undo-trail \{([^}]*)\}", css).group(1)
    assert "bottom: 5rem" in panel           # the rule lifts above THIS bottom
    # the lift rule is the last word on #status-bar's bottom (after the composer's)
    assert css.rindex("body.undo-trail-on #status-bar") > css.rindex("body.ag-composer-on #status-bar")


def test_the_page_asks_and_shows_the_age_selfcheck():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    try:
        subprocess.run([node, "-e", "require('jsdom')"], check=True, capture_output=True,
                       timeout=30, cwd=str(ROOT))
    except Exception:
        pytest.skip("jsdom not installed")
    r = subprocess.run([node, str(ROOT / "tests" / "v151_undo_prev_session_selfcheck.cjs")],
                       capture_output=True, text=True, encoding="utf-8", timeout=180, cwd=str(ROOT))
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.count("ok - ") >= 9, r.stdout
