"""Plot origins are optional metadata on the existing staging doors."""
import json

import pytest

from tests.state_model.repros._rig import Chip


ORIGIN = {"src": "plot_click", "run_uid": "run-7", "request_hash": "abc123",
          "text": "derived from run #7: learned from proposal; clicked * 2 + 1"}
PATH = "qubits.q1.T1"


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("bad", [None, [], "text", 3, True, {"extra": "x"},
                                  {"text": []}, {"text": None}, {"src": 7},
                                  {"text": "x" * 301}, {"run_uid": "x" * 301},
                                  {"text": "\ud800"}])
def test_bad_origin_refuses_without_changes(tmp_path, batch, bad):
    chip = Chip(tmp_path)
    before = (chip.log(), chip.journal(), chip.live_state(), chip.ctx["store"].mutation_seq)
    if batch:
        r = chip.c.post("/field/edit-batch", json={
            "updates": [{"dot_path": PATH, "value": "0.00004"}], "provenance": bad})
    else:
        r = chip.c.post("/field/edit", data={
            "dot_path": PATH, "value": "0.00004", "provenance": json.dumps(bad)})
    assert r.status_code == 400, r.data
    assert (chip.log(), chip.journal(), chip.live_state(), chip.ctx["store"].mutation_seq) == before


def test_malformed_form_origin_and_bad_late_batch_row(tmp_path):
    chip = Chip(tmp_path)
    for raw in ("", "{", "[" * 2000):
        r = chip.c.post("/field/edit", data={"dot_path": PATH, "value": "4", "provenance": raw})
        assert r.status_code == 400
    r = chip.c.post("/field/edit-batch", json={"updates": [
        {"dot_path": PATH, "value": "4"},
        {"dot_path": "qubits.q2.T1", "value": "5", "provenance": {"text": 3}}]})
    assert r.status_code == 400
    assert chip.log() == []


def test_optional_bounded_origin(tmp_path):
    from quam_state_manager.web.request_body import provenance
    chip = Chip(tmp_path)
    with chip.app.app_context():
        assert provenance({}) is None
        assert provenance({"provenance": {}}) == {}
        full = {key: "x" * 300 for key in ORIGIN}
        assert provenance({"provenance": full}) == full
        assert provenance({"provenance": json.dumps(ORIGIN)}, form=True) == ORIGIN
    assert chip.edit(PATH, "0.00004", provenance=json.dumps(ORIGIN)).get_json()["ok"]


def _apply(chip):
    r = chip.c.post("/state/apply-to-live")
    assert r.status_code == 200, r.data
    assert not chip.ctx["store"].change_log
    assert chip.journal()[0][-1]["meta"].get("hub"), r.data


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("save_first", [False, True])
def test_origin_survives_restart_apply_journal_and_ledger(tmp_path, batch, save_first):
    from quam_state_manager.core import pending_tray, hub
    from quam_state_manager.core.hub_store import HubStore
    from quam_state_manager.core.value_history import _sm_info
    from quam_state_manager.web import routes
    from tests.test_tray_restart import _open, _ctx
    chip = Chip(tmp_path)
    live_before = chip.live_state()
    if batch:
        r = chip.c.post("/field/edit-batch", json={"updates": [
            {"dot_path": PATH, "value": "0.00004"},
            {"dot_path": "qubits.q2.T1", "value": "0.00005"}], "provenance": ORIGIN})
        assert r.status_code == 200 and r.get_json()["ok"]
    else:
        chip.edit(PATH, "0.00004", provenance=json.dumps(ORIGIN))
    assert all(e.origin == ORIGIN for e in chip.ctx["store"].change_log)
    html = chip.c.get("/state/review").get_data(as_text=True)
    assert ORIGIN["text"] in html and 'sp-sub muted' in html
    pending_tray.flush_all()
    sidecar = json.loads(pending_tray.sidecar_path(chip.ctx["working_copy"]).read_text())
    assert all(e.get("origin") == ORIGIN for e in sidecar["entries"])
    app, client, response = _open(chip.inst, chip.live)
    chip.app, chip.c = app, client
    assert all(e.origin == ORIGIN for e in _ctx(app)["store"].change_log)
    assert ORIGIN["text"] in client.get("/state/review").get_data(as_text=True)
    assert chip.live_state() == live_before
    if save_first:
        assert client.post("/save").status_code == 200
    _apply(chip)
    unit = chip.journal()[0][-1]
    assert all(e.get("origin") == ORIGIN for e in unit["entries"])
    with app.app_context():
        chip_dir = routes._hub_chip_dir(chip.ctx["path"])
    event = hub.Hub.for_chip(chip_dir).events()[-1]
    assert event["src"] == ORIGIN["src"]
    assert event["run_uid"] == ORIGIN["run_uid"]
    assert event["ref"] == {k: ORIGIN[k] for k in ("text", "request_hash")}
    with HubStore(chip_dir) as ledger:
        row = ledger.conn.execute("SELECT eid, ref FROM sm_events WHERE sm_id=?", (event["id"],)).fetchone()
        assert row[1] and json.loads(row[1]) == event["ref"]
        info = _sm_info(ledger.conn, [row[0]])[row[0]]
        assert info.get("ref") == event["ref"]
    with app.test_request_context():
        shown = routes._vh_present({"provenance": "sm", "kind": "sm_apply", "src": event["src"],
                                   "ref": info["ref"], "flags": [], "value": 0.00004,
                                   "removed": False, "run_uid": ORIGIN["run_uid"]}, [], {})
    assert ORIGIN["text"] in shown["sub"]
    assert ORIGIN["text"] in shown["title"]
    assert "derived from a run" in shown["label"]
    assert shown["link_title"] == "Open the run this value was derived from"


def test_origin_in_ordinary_redo_and_staged_journal_inverse(tmp_path):
    chip = Chip(tmp_path)
    chip.c.post("/settings/undo-live", data={"enabled": "0"})
    chip.edit(PATH, "0.00004", provenance=json.dumps(ORIGIN))
    assert chip.c.post("/undo").status_code == 200
    assert chip.c.post("/redo").status_code == 200
    assert chip.ctx["store"].change_log[-1].origin == ORIGIN
    _apply(chip)
    assert chip.c.post("/undo").status_code == 200
    assert chip.ctx["store"].change_log[-1].origin == ORIGIN


def test_origin_in_live_journal_redo(tmp_path):
    chip = Chip(tmp_path)
    chip.edit(PATH, "0.00004", provenance=json.dumps(ORIGIN))
    _apply(chip)
    assert chip.c.post("/undo").status_code == 200
    # Capture the forward staging immediately before the existing live door.
    from quam_state_manager.web import routes
    from unittest.mock import patch
    original = routes._flush_walk_step_live
    origins = []
    def observe(ctx, **kw):
        origins.extend(e.origin for e in ctx["store"].change_log)
        return original(ctx, **kw)
    with patch.object(routes, "_flush_walk_step_live", observe):
        assert chip.c.post("/redo").status_code == 200
    assert origins and all(o == ORIGIN for o in origins)


def test_origin_survives_capture_composition_and_replay(tmp_path):
    from quam_state_manager.web import routes
    from quam_state_manager.core import pending_tray
    from tests.test_tray_restart import _open
    chip = Chip(tmp_path)
    chip.edit(PATH, "0.00004", provenance=json.dumps(ORIGIN))
    updates = routes._capture_change_log_as_updates(chip.ctx["store"])
    composed = routes._merge_reapply({}, updates)
    assert len(composed[PATH]) == 4
    assert composed[PATH][3] == ORIGIN
    chip.ctx["pending_reapply"] = composed
    pending_tray.flush_all()
    chip.app, chip.c, _ = _open(chip.inst, chip.live)
    assert isinstance(chip.ctx["pending_reapply"][PATH], tuple)
    assert chip.ctx["pending_reapply"][PATH][3] == ORIGIN
    chip.ctx["modifier"].undo_all()
    assert routes._replay_updates(chip.ctx["modifier"], composed)["applied"] == 1
    assert chip.ctx["store"].change_log[-1].origin == ORIGIN


def test_per_row_origins_and_mixed_apply_are_not_misattributed(tmp_path):
    from quam_state_manager.core import hub
    from quam_state_manager.web import routes
    chip = Chip(tmp_path)
    other = {**ORIGIN, "text": "derived from smoothed map", "src": "map_fit"}
    r = chip.c.post("/field/edit-batch", json={"updates": [
        {"dot_path": PATH, "value": "0.00004", "provenance": ORIGIN},
        {"dot_path": PATH, "value": "0.00005", "provenance": other}]})
    assert r.status_code == 200 and r.get_json()["ok"]
    assert [e.origin for e in chip.ctx["store"].change_log] == [ORIGIN, other]
    chip.edit("qubits.q2.T1", "0.00006")
    _apply(chip)
    with chip.app.app_context():
        event = hub.Hub.for_chip(routes._hub_chip_dir(chip.ctx["path"])).events()[-1]
    assert event["src"] == "apply"
    assert event.get("run_uid") is None
    assert event["ref"]["origins"] == {PATH: other}
    from quam_state_manager.core import value_history
    with chip.app.app_context():
        chip_dir = routes._hub_chip_dir(chip.ctx["path"])
    answer = value_history.read(chip_dir, {"v": value_history.target(chip.ctx["store"].merged, PATH)})
    point = answer["rows"]["v"]["points"][-1]
    assert point["ref"].get("text") == other["text"]
    assert point["run_uid"] == other["run_uid"]


@pytest.mark.parametrize("batch", [False, True])
def test_plot_origin_obeys_existing_mutator_lock(tmp_path, batch):
    from quam_state_manager.web import routes
    chip = Chip(tmp_path)
    assert {"main.field_edit", "main.field_edit_batch"} <= routes._SCHEDULER_MUTATOR_ENDPOINTS
    chip.app.config["agent_edit_lock"] = {"path": chip.ctx["path"], "node": "plan"}
    if batch:
        r = chip.c.post("/field/edit-batch", json={"updates": [{"dot_path": PATH, "value": "4"}],
                                                  "provenance": ORIGIN})
    else:
        r = chip.c.post("/field/edit", data={"dot_path": PATH, "value": "4", "provenance": json.dumps(ORIGIN)})
    assert r.status_code == 409
    assert chip.log() == []


def test_origin_is_copied_and_absence_keeps_legacy_shapes(tmp_path):
    from quam_state_manager.core.loader import ChangeEntry
    from quam_state_manager.core import undo_journal
    from quam_state_manager.web import routes
    entry = ChangeEntry(PATH, 1, 2, "state")
    assert entry.origin is None
    assert "origin" not in undo_journal.make_unit([entry])["entries"][0]
    assert routes._tagged("set", 2, None) == ("set", 2)
    assert routes._tagged("set", 2, "g") == ("set", 2, "g")
    entry.origin = dict(ORIGIN)
    unit = undo_journal.make_unit([entry])
    entry.origin["text"] = "changed later"
    assert unit["entries"][0]["origin"] == ORIGIN


@pytest.mark.parametrize("bad", [{"text": [3]}, {"text": "\ud800"}])
def test_corrupt_origin_refuses_restart_recovery(tmp_path, bad):
    from quam_state_manager.core import pending_tray
    from tests.test_tray_restart import _open
    chip = Chip(tmp_path)
    chip.edit(PATH, "0.00004", provenance=json.dumps(ORIGIN))
    pending_tray.flush_all()
    p = pending_tray.sidecar_path(chip.ctx["working_copy"])
    doc = json.loads(p.read_text())
    doc["entries"][0]["origin"] = bad
    p.write_text(json.dumps(doc), encoding="utf-8")
    chip.app, chip.c, response = _open(chip.inst, chip.live)
    assert chip.log() == []
    assert "No rows were restored" in response.get_data(as_text=True)


def test_origin_on_fsp_compensation_uses_existing_batch(tmp_path):
    chip = Chip(tmp_path)
    outputs = chip.ctx["store"].merged["ports"]["mw_outputs"]["con1"]
    fem = next(iter(outputs))
    port = next(iter(outputs[fem]))
    path = f"ports.mw_outputs.con1.{fem}.{port}.full_scale_power_dbm"
    value = str(outputs[fem][port]["full_scale_power_dbm"] + 2)
    data = {"dot_path": path, "value": value, "provenance": json.dumps(ORIGIN)}
    offer = chip.c.post("/field/edit", data=data)
    assert offer.status_code == 409 and offer.get_json().get("fsp_compensation"), offer.data
    assert chip.log() == []
    applied = chip.c.post("/field/edit", data={**data, "fsp_ack": "comp"})
    assert applied.status_code == 200 and applied.get_json()["ok"], applied.data
    log = chip.ctx["store"].change_log
    assert len(log) > 1 and len({e.group_id for e in log}) == 1
    assert all(e.origin == ORIGIN for e in log)


def test_mixed_origin_is_visible_through_a_pointer(tmp_path):
    from quam_state_manager.core import value_history
    from quam_state_manager.web import routes
    chip = Chip(tmp_path)
    leaf = "qubits.q1.anharmonicity"
    alias = "qubits.q1.xy.operations.x180_DragCosine.anharmonicity"
    chip.edit(leaf, "-220000000", provenance=json.dumps(ORIGIN))
    chip.edit("qubits.q2.T1", "0.00004")
    _apply(chip)
    target = value_history.target(chip.ctx["store"].merged, alias)
    assert target["holder_path"] == leaf
    with chip.app.app_context():
        chip_dir = routes._hub_chip_dir(chip.ctx["path"])
    answer = value_history.read(chip_dir, {"v": target})
    point = answer["rows"]["v"]["points"][-1]
    assert (point.get("ref") or {}).get("text") == ORIGIN["text"]
    assert point["run_uid"] == ORIGIN["run_uid"]
