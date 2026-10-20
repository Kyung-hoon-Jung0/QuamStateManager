"""v1.5.1 (r6, P2-13): a message names a chip as the top bar does.

The top bar shows a chip's declared ``extras.chip_name``; the undo toast
("written to the chip file of 'chip_x'") and the tab-chip refusal ("this tab
shows chip 'chip_x'; the server has chip 'chip_b'") named the folders. They
now use the top bar's ladder (declared name, else the folder rule); the JSON
fields a client may compare keep the folder names.
"""
from __future__ import annotations

import json
import re

import pytest

from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

_WIRING = {"network": {"host": "127.0.0.1", "cluster_name": "C1"}}
H = "X-SM-Chip-Key"


def _write(folder, name):
    folder.mkdir(parents=True, exist_ok=True)
    state = {"qubits": {f"q{i}": {"id": f"q{i}", "T1": 1.0e-5 * i} for i in (1, 2)},
             "qubit_pairs": {}, "active_qubit_names": ["q1", "q2"]}
    if name:
        state["extras"] = {"chip_name": name}
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")


def _page_key(c) -> str:
    m = re.search(r"window\.__chipKey = (\"[^\"]*\");", c.get("/").get_data(as_text=True))
    assert m
    return json.loads(m.group(1))


@pytest.fixture
def env(tmp_path):
    x = tmp_path / "chip_x" / "quam_state"
    y = tmp_path / "chip_b" / "quam_state"
    _write(x, "deviceA")
    _write(y, "deviceB")
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(x)}).status_code in (200, 302)
    return {"app": app, "c": c, "x": x, "y": y}


def _msg(r) -> str:
    raw = r.headers.get("HX-Trigger")
    assert raw, r.data[:300]
    return json.loads(raw)["cellsReverted"]["message"]


def test_the_top_bar_and_the_undo_toast_name_the_same_chip(env):
    c = env["c"]
    tray = c.get("/state/tray").get_data(as_text=True)
    assert re.search(r'class="state-status-name" data-chip-folder="chip_x"[^>]*>deviceA<', tray)
    r = c.post("/field/edit", data={"dot_path": "qubits.q2.T1", "value": "2.5e-05"})
    assert r.status_code == 200
    assert c.post("/state/apply-to-live").status_code == 200
    m = _msg(c.post("/undo"))
    assert m.startswith("Undone — written to the chip file of 'deviceA':"), m
    m = _msg(c.post("/redo"))
    assert m.startswith("Redone — written to the chip file of 'deviceA':"), m


def test_the_tab_chip_refusal_names_both_chips_as_the_top_bar_does(env):
    c = env["c"]
    key_x = _page_key(c)
    assert c.post("/load", data={"folder": str(env["y"])}).status_code in (200, 302)
    r = c.post("/field/edit-batch", json={"updates": [{"dot_path": "qubits.q1.T1", "value": "3e-05"}]},
               headers={H: key_x})
    assert r.status_code == 409
    body = r.get_json()
    assert body["message"] == ("Not done: this tab shows chip 'deviceA'; the server has chip "
                               "'deviceB' open now. Reload this page. Nothing was written."), body
    # the fields a client may compare keep the folders
    assert body["tab_chip"] == "chip_x" and body["loaded_chip"] == "chip_b"


def test_two_folders_of_one_declared_chip_are_told_apart_by_folder(tmp_path):
    x = tmp_path / "copy_1" / "quam_state"
    y = tmp_path / "copy_2" / "quam_state"
    _write(x, "deviceA")
    _write(y, "deviceA")
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(x)}).status_code in (200, 302)
    key_x = _page_key(c)
    assert c.post("/load", data={"folder": str(y)}).status_code in (200, 302)
    r = c.post("/field/edit-batch", json={"updates": [{"dot_path": "qubits.q1.T1", "value": "3e-05"}]},
               headers={H: key_x})
    msg = r.get_json()["message"]
    assert f"'deviceA' ({x})" in msg and f"'deviceA' ({y})" in msg, msg


def test_an_undeclared_chip_is_still_named_by_its_folder(tmp_path):
    x = tmp_path / "chip_x" / "quam_state"
    _write(x, None)
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(x)}).status_code in (200, 302)
    assert c.post("/field/edit", data={"dot_path": "qubits.q2.T1", "value": "2.5e-05"}).status_code == 200
    assert c.post("/state/apply-to-live").status_code == 200
    assert _msg(c.post("/undo")).startswith("Undone — written to the chip file of 'chip_x':")


# --------------------------------------------------------------------------
# the other surfaces that name the open chip in a sentence
# --------------------------------------------------------------------------

def _seed_run(root, run_id, state):
    run = root / "2026-07-29" / f"#{run_id}_31_chevron_010000"
    run.mkdir(parents=True)
    (run / "node.json").write_text(json.dumps({
        "metadata": {"name": "31_chevron", "status": "successful",
                     "run_start": "2026-07-29T01:00:00", "run_end": "2026-07-29T01:00:01"},
        "data": {"parameters": {"model": {"qubits": ["q1"]}}, "outcomes": {}},
        "id": run_id, "parents": [], "created_at": "2026-07-29T01:00:00",
    }), encoding="utf-8")
    (run / "data.json").write_text("{}", encoding="utf-8")
    qs = run / "quam_state"
    qs.mkdir()
    (qs / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (qs / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")


@pytest.fixture
def with_run(env, tmp_path):
    state = json.loads((env["x"] / "state.json").read_text(encoding="utf-8"))
    state["qubits"]["q1"]["T1"] = 3.5e-05
    root = tmp_path / "data"
    _seed_run(root, 1, state)
    c = env["c"]
    c.post("/workspace/add", data={"folder": str(root)})
    return dict(env, uid=f"{routes._folder_key(root)}:1")


def test_load_for_review_names_the_chip_as_the_top_bar_does(with_run):
    r = with_run["c"].post(f"/dataset/{with_run['uid']}/load-state")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "WORKING state of deviceA" in body, body[:1500]


def test_apply_selected_names_the_chip_as_the_top_bar_does(with_run):
    r = with_run["c"].post(f"/dataset/{with_run['uid']}/apply-selected/preview",
                           data=json.dumps({"file": "state", "paths": ["qubits.q1.T1"]}),
                           content_type="application/json")
    assert r.status_code == 200 and r.get_json()["chip"] == "deviceA"


def test_the_archive_back_button_names_the_chip_as_the_top_bar_does(with_run):
    c = with_run["c"]
    r = c.post(f"/dataset/{with_run['uid']}/load-state?mode=archive")
    assert r.headers.get("HX-Redirect") == "/qubits"
    body = c.get("/state/tray").get_data(as_text=True)
    assert "Back to deviceA</button>" in body, body[:3000]


def test_the_fit_pre_check_names_the_declared_chip_first(env):
    assert env["c"].get("/chip/active-token").get_json()["name"] == "deviceA (folder chip_x)"


def test_the_autofit_readiness_names_the_chip_as_the_top_bar_does(env):
    html = env["c"].get("/autofit").get_data(as_text=True)
    assert re.search(r"Chip: deviceA</span>", html), "the readiness chip names the folder"


def test_a_version_of_another_chip_is_refused_by_the_open_chips_name(env):
    r = env["c"].get("/state/versions/20260101_000000_000000/diff?chip_key=some_other_chip")
    body = r.get_data(as_text=True)
    assert "This version belongs to chip &#39;some_other_chip&#39; but &#39;deviceA&#39; is open" in body \
        or "This version belongs to chip 'some_other_chip' but 'deviceA' is open" in body, body[:800]


def test_an_apply_seen_on_a_chip_sm_does_not_hold_names_the_open_one(env):
    # a chip key SM holds no context for is named by its folder part
    r = env["c"].post("/state/apply-to-live", data={"expect_chip_key": "chip_q-00000000"})
    assert r.status_code == 409
    text = r.get_data(as_text=True)
    assert ("this tab shows chip &#39;chip_q&#39;; the server has chip &#39;deviceA&#39; "
            "open now") in text, text[:600]


def test_a_stale_page_token_is_refused_by_the_open_chips_name(env):
    r = env["c"].post("/field/delete", data={"dot_path": "qubits.q1.T1",
                                             "expect_chip": "deadbeefdeadbeef"})
    assert r.status_code == 409
    body = r.get_json()
    assert "this app now has 'deviceA' loaded" in body["error"], body
    assert "continue on 'deviceA'" in body["error"]
    assert body["loaded_chip"] == "chip_x"          # the field keeps the folder
