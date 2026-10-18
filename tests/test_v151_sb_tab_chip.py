"""v1.5.1 SB-F2 / SB-F5: a press from a tab whose chip is not the one open.

Two tabs share one server, and the server has one chip open. Chip Y was a
copy of chip X's design (the same fingerprint token). Tab A still showed X
after tab B opened Y; A's Ctrl+Z wrote Y's live state.json ("Undone -> live:
qubits.q2.T1"), and A's grid commit, Apply and Take live acted on Y under
confirms that blamed "another State Manager window".

Every page now carries the per-folder chip key (``window.__chipKey``), every
write request sends it (``X-SM-Chip-Key``, tab-chip.js), and the write doors
refuse a key that is not the open chip's -- naming both chips, before any
other gate, with nothing written. The live Ctrl+Z / redo toast names the chip
it wrote.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.core import working_copy
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

ROOT = Path(__file__).resolve().parent.parent
_WIRING = {"network": {"host": "127.0.0.1", "cluster_name": "C1"}}
H = "X-SM-Chip-Key"


def _state(t2=2.0e-5):
    qs = {f"q{i}": {"id": f"q{i}", "T1": 1.0e-5 * i} for i in (1, 2, 3)}
    qs["q2"]["T1"] = t2
    return {"qubits": qs, "qubit_pairs": {}, "active_qubit_names": sorted(qs)}


def _write(folder: Path, state: dict):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")


def _sha(folder: Path) -> str:
    return hashlib.sha256((folder / "state.json").read_bytes()
                          + (folder / "wiring.json").read_bytes()).hexdigest()


def _page_key(c) -> str:
    html = c.get("/").get_data(as_text=True)
    m = re.search(r"window\.__chipKey = (\"[^\"]*\");", html)
    assert m, "the page does not carry window.__chipKey"
    return json.loads(m.group(1))


@pytest.fixture
def env(tmp_path):
    x = tmp_path / "chip_x" / "quam_state"
    y = tmp_path / "chip_y" / "quam_state"
    _write(x, _state())
    _write(y, _state())                      # same design, same fingerprint token
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    # chip Y gets an applied edit, so its undo journal has a unit to walk live
    assert c.post("/load", data={"folder": str(y)}).status_code in (200, 302)
    r = c.post("/field/edit", data={"dot_path": "qubits.q2.T1", "value": "2.5e-05"})
    assert r.status_code == 200
    assert c.post("/state/apply-to-live").status_code == 200
    key_y = _page_key(c)
    tok_y = routes_mod_token(app, c)
    # tab A renders X; then tab B switches the server to Y
    assert c.post("/load", data={"folder": str(x)}).status_code in (200, 302)
    key_x = _page_key(c)
    tok_x = routes_mod_token(app, c)
    assert c.post("/load", data={"folder": str(y)}).status_code in (200, 302)
    assert key_x != key_y and tok_x == tok_y, "the fixture must be two copies of one design"
    return {"app": app, "c": c, "x": x, "y": y, "key_x": key_x, "key_y": key_y, "tok": tok_x}


def routes_mod_token(app, c) -> str:
    html = c.get("/").get_data(as_text=True)
    m = re.search(r"window\.__chipToken = (\"[^\"]*\");", html)
    return json.loads(m.group(1)) if m else ""


def _ctx(env):
    return env["app"].config["contexts"][env["app"].config["active_context"]]


def _refused(r, env, what):
    assert r.status_code == 409, (what, r.status_code, r.get_data(as_text=True)[:400])
    assert r.headers.get("X-SM-Chip-Mismatch") == "1", what
    body = r.get_data(as_text=True)
    try:
        msg = json.loads(body)["message"]
    except ValueError:
        msg = body
    assert "chip_x" in msg and "chip_y" in msg, (what, msg)
    assert "another" not in msg.lower(), f"{what}: a chip switch blamed on another window: {msg}"
    return msg


A_PRESSES = [
    ("Ctrl+Z", "/undo", {}, None),
    ("Ctrl+Shift+Z", "/redo", {}, None),
    ("grid commit", "/field/edit", {"dot_path": "qubits.q3.T1", "value": "3.3e-05"}, None),
    ("grid batch", "/field/edit-batch", None,
     {"updates": [{"dot_path": "qubits.q3.T1", "value": "3.3e-05"}]}),
    ("Apply", "/state/apply-to-live", {}, None),
    ("Keep mine", "/state/apply-to-live?force=1", {}, None),
    ("Take live", "/state/sync", {"mode": "discard", "force": "1"}, None),
    ("Pull & apply", "/state/sync", {"mode": "apply"}, None),
    ("Take live, keep edits", "/state/sync", {"mode": "reapply"}, None),
    ("Save", "/save", {}, None),
    ("Auto-Sync arm", "/auto-sync/set", {"pull": "1", "push": "1"}, None),
]


class TestAStaleTabIsRefusedByName:
    @pytest.mark.parametrize("what,url,form,js", A_PRESSES, ids=[p[0] for p in A_PRESSES])
    def test_a_press_from_chip_x_never_touches_chip_y(self, env, what, url, form, js):
        c = env["c"]
        # Y holds a staged edit of its own (B's), which a wrong-chip press must not touch
        assert c.post("/field/edit", data={"dot_path": "qubits.q1.T1", "value": "1.9e-05"}).status_code == 200
        before_live = _sha(env["y"])
        before_log = [(e.dot_path, e.new_value) for e in _ctx(env)["store"].change_log]
        hdr = {H: env["key_x"]}
        if js is not None:
            r = c.post(url, json={**js, "expect_chip": env["tok"]}, headers=hdr)
        else:
            r = c.post(url, data={**form, "expect_chip": env["tok"]}, headers=hdr)
        _refused(r, env, what)
        assert _sha(env["y"]) == before_live, f"{what} wrote chip Y's live files"
        assert [(e.dot_path, e.new_value) for e in _ctx(env)["store"].change_log] == before_log, \
            f"{what} changed chip Y's tray"
        assert not _ctx(env).get("auto_apply"), f"{what} armed chip Y's Auto-Sync"

    def test_ctrl_z_with_an_empty_tray_and_equal_tokens_is_refused(self, env):
        """The finding exactly: the token matches (copies), the tray is empty
        (no signature to compare), and the live walk would write Y."""
        c = env["c"]
        assert not _ctx(env)["store"].change_log
        before = _sha(env["y"])
        r = c.post("/undo", data={"expect_chip": env["tok"], "expect_sig": ""},
                   headers={H: env["key_x"]})
        _refused(r, env, "Ctrl+Z")
        assert _sha(env["y"]) == before
        assert int(_ctx(env).get("undo_cursor") or 0) == 1, "the walk moved"

    def test_the_agent_bridge_field_is_honoured_too(self, env):
        r = env["c"].post("/state/apply-to-live", data={"expect_chip_key": env["key_x"]},
                          headers={"Accept": "application/json"})
        assert r.status_code == 409 and r.get_json()["conflict"] == "chip_mismatch"

    def test_the_chip_check_runs_before_the_unseen_edit_gate(self, env):
        """F5: Apply's refusal named Y's edit as "another State Manager
        window changed them". The chip is what changed."""
        c = env["c"]
        c.post("/field/edit", data={"dot_path": "qubits.q1.T1", "value": "1.9e-05"})
        r = c.post("/state/sync", data={"mode": "apply", "seen_changes": "0", "seen_sig": "0" * 16},
                   headers={H: env["key_x"]})
        assert r.get_json()["status"] == "chip_mismatch", r.get_json()

    def test_a_dataset_archive_open_is_a_switch_not_a_write(self, env):
        from flask import request
        app = env["app"]
        with app.test_request_context("/dataset/x/load-state", method="POST",
                                      data={"mode": "archive"}, headers={H: env["key_x"]}):
            assert request.endpoint == "main.dataset_load_state"
            assert routes._tab_chip_gate() is None
        with app.test_request_context("/dataset/x/load-state", method="POST",
                                      data={}, headers={H: env["key_x"]}):
            assert routes._tab_chip_gate().status_code == 409


class TestThePressOnItsOwnChipWorks:
    def test_the_matching_key_passes_and_the_live_toast_names_the_chip(self, env):
        c = env["c"]
        before = _sha(env["y"])
        r = c.post("/undo", headers={H: env["key_y"]})
        assert r.status_code == 200, r.get_data(as_text=True)[:300]
        trig = json.loads(r.headers["HX-Trigger"])["cellsReverted"]
        assert trig["live"] is True and trig["message"].startswith("Undone → live on 'chip_y'"), trig
        assert _sha(env["y"]) != before
        r = c.post("/redo", headers={H: env["key_y"]})
        trig = json.loads(r.headers["HX-Trigger"])["cellsReverted"]
        assert trig["message"].startswith("Redone → live on 'chip_y'"), trig

    def test_no_key_is_judged_as_before(self, env):
        r = env["c"].post("/field/edit", data={"dot_path": "qubits.q3.T1", "value": "3.3e-05"})
        assert r.status_code == 200

    def test_every_gated_door_is_a_real_route(self, env):
        names = {r.endpoint for r in env["app"].url_map.iter_rules()}
        missing = sorted(routes._TAB_CHIP_DOORS - names)
        assert not missing, f"gated doors that no longer exist: {missing}"
        assert routes._TAB_CHIP_JSON_DOORS <= routes._TAB_CHIP_DOORS

    def test_the_page_key_is_the_working_copy_key(self, env):
        assert env["key_y"] == working_copy.key_for(env["y"])
        assert env["key_x"] == working_copy.key_for(env["x"])


def test_the_page_loads_tab_chip_before_app():
    base = (ROOT / "quam_state_manager" / "web" / "templates" / "base.html").read_text(encoding="utf-8")
    assert "window.__chipKey" in base
    i, j = base.index("asset_url('tab-chip.js')"), base.index("asset_url('app.js')")
    assert i < j


def _node_selfcheck(name: str, minimum: int):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    try:
        subprocess.run([node, "-e", "require('jsdom')"], check=True, capture_output=True,
                       timeout=30, cwd=str(ROOT))
    except Exception:
        pytest.skip("jsdom not installed")
    r = subprocess.run([node, str(ROOT / "tests" / name)], capture_output=True, text=True,
                       encoding="utf-8", timeout=180, cwd=str(ROOT))
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.count("ok - ") >= minimum, r.stdout


def test_every_write_request_carries_the_page_chip_selfcheck():
    _node_selfcheck("v151_tab_chip_selfcheck.cjs", 17)
