"""v1.5.1 (r6, P2-4): a refused checkpoint whose rows SM already holds.

After a mixed-version run the same edits were listed twice: "3 not
recovered" next to the very 3 edits already in the tray, and Restore then
said "Nothing to restore: the chip already holds these values" -- false, they
were unapplied tray rows, not on the live chip. Pinned here:

* a checkpoint whose every row is already in the tray with the same value is
  not counted as not recovered; the panel still lists it, tagged "already in
  the tray", with nothing to restore;
* a row that is NOT held is never hidden: it is still counted and listed;
* Restore says which: "already in the tray" or "the live chip already holds".
"""
import json
import re

from quam_state_manager.core import pending_tray
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app


def _ctx(app):
    return app.config["contexts"][app.config["active_context"]]


def _open(instance, live):
    routes._quam_cache.clear()          # create_app alone is not a process restart
    app = create_app(testing=True, instance_path=str(instance))
    client = app.test_client()
    response = client.post("/load", data={"folder": str(live)}, follow_redirects=True)
    assert response.status_code == 200
    return app, client


def _chip(tmp_path):
    live = tmp_path / "chip" / "quam_state"
    live.mkdir(parents=True)
    (live / "state.json").write_text(json.dumps({
        "qubits": {"q1": {"id": "q1", "f_01": 5000000000, "T1": 1.0e-5,
                           "z": {"joint_offset": 0.08}}},
        "qubit_pairs": {}, "active_qubit_names": ["q1"],
    }), encoding="utf-8")
    (live / "wiring.json").write_text("{}", encoding="utf-8")
    return tmp_path / "instance", live


def _row(path, old, new):
    return {"dot_path": path, "old_value": old, "new_value": new, "source_file": "state",
            "created": False, "deleted": False, "actor": "human"}


def _checkpoint(app, rows, file_id="abc12345"):
    """A refused checkpoint as this SM names it (what an older SM's refusal
    of the plain sidecar becomes once this SM opens the chip)."""
    wc = _ctx(app)["working_copy"]
    doc = {"version": 2, "live_folder": str(wc.live_folder), "base_hash": None,
           "applied": 0, "synced_live_hash": wc.synced_live_hash, "entries": rows,
           "mutation_seq": 0, "flags": {"working_dirty": False, "pending_reapply": None}}
    path = wc.working_folder.parent / f"{wc.key}.pending_tray.kept.unrecovered-{file_id}.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return file_id


def _marker(client):
    tray = client.get("/state/tray").get_data(as_text=True)
    m = re.search(r'class="sync-marker sync-marker-unrecovered"[^>]*>([^<]*)<', tray)
    return m.group(1) if m else None


def _box(panel, file_id):
    m = re.search(r'<div class="sp-note[^"]*" data-recovery-id="' + file_id
                  + r'">(.*?)<div class="sp-unrec-acts">(.*?)</div>', panel, re.S)
    assert m, panel[:3000]
    return m.group(1), m.group(2)


def _staged_twice(tmp_path):
    """Two edits in the tray AND the same two in a refused checkpoint."""
    instance, live = _chip(tmp_path)
    app, client = _open(instance, live)
    for path, value in [("qubits.q1.f_01", "5100000000"), ("qubits.q1.T1", "1.2e-05")]:
        r = client.post("/field/edit", data={"dot_path": path, "value": value})
        assert r.status_code == 200 and r.get_json()["ok"], r.get_json()
    pending_tray.flush_all()
    file_id = _checkpoint(app, [_row("qubits.q1.f_01", 5000000000, 5100000000),
                                _row("qubits.q1.T1", 1.0e-5, 1.2e-5)])
    return live, app, client, file_id


def test_a_checkpoint_already_in_the_tray_is_not_counted_as_not_recovered(tmp_path):
    _live, app, client, file_id = _staged_twice(tmp_path)
    assert len(_ctx(app)["store"].change_log) == 2
    assert _marker(client) is None
    panel = client.get("/state/review").get_data(as_text=True)
    body, acts = _box(panel, file_id)
    assert "2 edits from an earlier session: already in the tray" in body
    assert "not recovered" not in body
    assert body.count("already in the tray</small>") == 2      # every row still listed
    assert "qubits.q1.f_01" in body and "qubits.q1.T1" in body
    assert "sp-unrec-restore" not in acts and "Set aside" in acts


def test_restore_says_the_edits_are_already_in_the_tray(tmp_path):
    live, app, client, file_id = _staged_twice(tmp_path)
    live_bytes = (live / "state.json").read_bytes()
    r = client.post("/state/recovery/restore", json={"id": file_id})
    body = r.get_json()
    assert r.status_code == 200 and body["ok"], body
    assert body["message"] == ("Nothing to restore: these edits are already in the tray "
                               "(not on the live chip yet)."), body["message"]
    assert "chip already holds" not in body["message"]
    assert len(_ctx(app)["store"].change_log) == 2
    assert (live / "state.json").read_bytes() == live_bytes


def test_a_row_not_in_the_tray_is_still_counted_and_listed(tmp_path):
    _live, app, client, _ = _staged_twice(tmp_path)
    file_id = _checkpoint(app, [_row("qubits.q1.f_01", 5000000000, 5100000000),
                                _row("qubits.q1.z.joint_offset", 0.08, 0.12)], "def67890")
    assert _marker(client) == "&#9888; 1 not recovered"
    panel = client.get("/state/review").get_data(as_text=True)
    body, acts = _box(panel, file_id)
    assert "1 staged edit not recovered" in body and "of 2; 1 already held" in body
    assert "qubits.q1.z.joint_offset</code></td>" in body      # untagged: not held
    assert "qubits.q1.f_01</code> <small" in body and "already in the tray" in body
    assert "sp-unrec-restore" in acts
    r = client.post("/state/recovery/restore", json={"id": file_id})
    body = r.get_json()
    assert r.status_code == 200 and body["ok"], body
    assert body["message"].startswith("Restored 1 edit to the tray (not on the live chip yet).")
    assert "1 already in the tray." in body["message"]
    assert _ctx(app)["store"].get_value("qubits.q1.z.joint_offset") == 0.12


def test_restore_says_the_live_chip_holds_values_that_are_not_in_the_tray(tmp_path):
    instance, live = _chip(tmp_path)
    app, client = _open(instance, live)
    # the chip already holds the row's value; the tray is empty
    file_id = _checkpoint(app, [_row("qubits.q1.T1", 9.0e-6, 1.0e-5)])
    assert _marker(client) is None
    body, _acts = _box(client.get("/state/review").get_data(as_text=True), file_id)
    assert "1 edit from an earlier session: already on the chip" in body
    r = client.post("/state/recovery/restore", json={"id": file_id})
    assert r.get_json()["message"] == "Nothing to restore: the live chip already holds this value."
