"""v1.5.1 R1: a refused recovery checkpoint never strands the user's edits.

A restart that cannot restore the tray's checkpoint keeps it as
``<key>.pending_tray.unrecovered-<id>.json``. Before v1.5.1 the only notice
was popped on the first tray render and the page's own tray refresh removed
it ~150 ms later; nothing listed the file again, so the staged edits were
silently gone. Pinned here:

* the file is listed on every render (a marker on the status control, the
  rows + the reason in the sync panel) until the user decides;
* Restore stages the rows through the tray (live untouched), naming a
  collision or a missing path before staging over it; Discard renames the
  file aside, never deletes it; nothing is pruned behind the user's back;
* the reason tells an older format from another chip folder;
* the checkpoint is written without null optional row fields, so an older SM
  (whose rows have no ``origin``) can still read a checkpoint of plain edits
  (the real v1.3.0 reader is driven in test_v151_older_sm_reads_checkpoint.py,
  which also pins that it refuses one it cannot decode).
"""
import json
from dataclasses import dataclass
from typing import Any

import pytest

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
    return app, client, response


def _staged(tmp_path):
    live = tmp_path / "chip" / "quam_state"
    live.mkdir(parents=True)
    (live / "state.json").write_text(json.dumps({
        "qubits": {"q1": {"id": "q1", "f_01": 5000000000, "T1": 1.0e-5,
                           "z": {"joint_offset": 0.08}}},
        "qubit_pairs": {}, "active_qubit_names": ["q1"],
    }), encoding="utf-8")
    (live / "wiring.json").write_text("{}", encoding="utf-8")
    instance = tmp_path / "instance"
    app, client, _ = _open(instance, live)
    for path, value in [("qubits.q1.f_01", "5100000000"),
                        ("qubits.q1.z.joint_offset", "0.12")]:
        r = client.post("/field/edit", data={"dot_path": path, "value": value})
        assert r.status_code == 200 and r.get_json()["ok"]
    pending_tray.flush_all()
    return instance, live, app, client


def _rewrite_sidecar(app, **changes):
    wc = _ctx(app)["working_copy"]
    path = pending_tray.sidecar_path(wc)
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc.update(changes)
    path.write_text(json.dumps(doc), encoding="utf-8")
    return wc


def _refused(tmp_path, **changes):
    """A restart that refuses the checkpoint (an older format by default)."""
    instance, live, first, _ = _staged(tmp_path)
    wc = _rewrite_sidecar(first, **(changes or {"version": 1}))
    app, client, response = _open(instance, live)
    return instance, live, wc, app, client, response


def _unrecovered(wc):
    # v1.5.1 C1/MINOR 5: this SM names them ``.kept.unrecovered-`` (outside an
    # older SM's prune); an older name is listed too, and renamed at open
    return sorted(wc.working_folder.parent.glob(f"{wc.key}.pending_tray*.unrecovered-*.json"))


# --------------------------------------------------------------------------
# (a) listed on every render until decided
# --------------------------------------------------------------------------

def test_a_refused_checkpoint_stays_listed_after_the_first_render(tmp_path):
    _i, _l, wc, app, client, response = _refused(tmp_path)
    assert not _ctx(app)["store"].change_log
    assert len(_unrecovered(wc)) == 1
    # the page's own tray refresh (what removed the notice ~150 ms later)
    for _ in range(3):
        tray = client.get("/state/tray").get_data(as_text=True)
        assert "sync-marker-unrecovered" in tray, tray[:2000]
        assert "2 not recovered" in tray
    # another page renders it too
    assert "sync-marker-unrecovered" in client.get("/explorer").get_data(as_text=True)
    panel = client.get("/state/review").get_data(as_text=True)
    assert "sp-unrecovered" in panel
    assert "qubits.q1.f_01" in panel and "qubits.q1.z.joint_offset" in panel
    assert "older format" in panel
    assert "sp-unrec-restore" in panel and "sp-unrec-discard" in panel


def test_the_one_shot_refusal_text_rides_a_toast_not_the_top_bar(tmp_path):
    *_rest, response = _refused(tmp_path)
    html = response.get_data(as_text=True)
    assert "Staged edits could not be recovered" in html
    assert 'class="tray-recovery-notice tray-recovery-error" data-toast="error" hidden' in html


# --------------------------------------------------------------------------
# Restore / Discard
# --------------------------------------------------------------------------

def test_restore_stages_the_rows_in_the_tray_and_never_touches_live(tmp_path):
    _i, live, wc, app, client, _r = _refused(tmp_path)
    live_bytes = (live / "state.json").read_bytes()
    [path] = _unrecovered(wc)
    file_id = path.name.split(".unrecovered-")[1][:-len(".json")]
    r = client.post("/state/recovery/restore", json={"id": file_id})
    body = r.get_json()
    assert r.status_code == 200 and body["ok"], body
    store = _ctx(app)["store"]
    assert [e.dot_path for e in store.change_log] == ["qubits.q1.f_01", "qubits.q1.z.joint_offset"]
    assert store.get_value("qubits.q1.f_01") == 5100000000
    assert store.get_value("qubits.q1.z.joint_offset") == 0.12
    # one group: one Ctrl+Z takes the whole restore back
    assert len({e.group_id for e in store.change_log}) == 1
    assert (live / "state.json").read_bytes() == live_bytes
    assert not _unrecovered(wc)
    kept = list(wc.working_folder.parent.glob(f"{wc.key}.pending_tray.restored-{file_id}.json"))
    assert kept, "the restored file is set aside, not deleted"
    assert "sync-marker-unrecovered" not in client.get("/state/tray").get_data(as_text=True)
    assert "2 unapplied" in body["tray_html"] or "Apply 2" in body["tray_html"]


def test_restore_names_a_collision_before_staging_over_it(tmp_path):
    _i, _l, wc, app, client, _r = _refused(tmp_path)
    # the field moved since the checkpoint was written (a third value)
    assert client.post("/field/edit", data={"dot_path": "qubits.q1.f_01",
                                            "value": "5200000000"}).get_json()["ok"]
    [path] = _unrecovered(wc)
    file_id = path.name.split(".unrecovered-")[1][:-len(".json")]
    r = client.post("/state/recovery/restore", json={"id": file_id})
    body = r.get_json()
    assert r.status_code == 409 and body["error_kind"] == "recovery_collision", body
    assert "qubits.q1.f_01" in body["error"] and "Nothing staged" in body["error"]
    store = _ctx(app)["store"]
    assert store.get_value("qubits.q1.f_01") == 5200000000
    assert store.get_value("qubits.q1.z.joint_offset") == 0.08
    assert _unrecovered(wc), "a refused restore keeps the file listed"
    r = client.post("/state/recovery/restore", json={"id": file_id, "force": "1"})
    assert r.status_code == 200 and r.get_json()["ok"], r.get_json()
    assert store.get_value("qubits.q1.f_01") == 5100000000
    assert store.get_value("qubits.q1.z.joint_offset") == 0.12


def test_discard_sets_the_file_aside_and_never_deletes_it(tmp_path):
    _i, _l, wc, _app, client, _r = _refused(tmp_path)
    [path] = _unrecovered(wc)
    raw = path.read_bytes()
    file_id = path.name.split(".unrecovered-")[1][:-len(".json")]
    r = client.post("/state/recovery/discard", json={"id": file_id})
    body = r.get_json()
    assert r.status_code == 200 and body["ok"], body
    kept = wc.working_folder.parent / f"{wc.key}.pending_tray.discarded-{file_id}.json"
    assert kept.read_bytes() == raw
    assert str(kept) in body["message"]
    assert not _unrecovered(wc)
    assert "sync-marker-unrecovered" not in client.get("/state/tray").get_data(as_text=True)
    # a second press on a file that is gone is named, not a 500
    again = client.post("/state/recovery/discard", json={"id": file_id})
    assert again.status_code == 404 and again.get_json()["error_kind"] == "recovery_gone"


def test_refused_files_are_never_pruned(tmp_path):
    instance, live, first, _ = _staged(tmp_path)
    wc = _ctx(first)["working_copy"]
    folder = wc.working_folder.parent
    for i in range(7):
        (folder / f"{wc.key}.pending_tray.unrecovered-old{i}.json").write_text("{}", encoding="utf-8")
    _rewrite_sidecar(first, version=1)
    _open(instance, live)
    assert len(_unrecovered(wc)) == 8


# --------------------------------------------------------------------------
# (b) distinct reasons
# --------------------------------------------------------------------------

@pytest.mark.parametrize("change, words", [
    ({"version": 1}, "older format"),
    ({"live_folder": "C:/elsewhere/other_chip/quam_state"}, "another chip folder"),
])
def test_the_refusal_reason_tells_the_cases_apart(tmp_path, change, words):
    _i, _l, _wc, _app, client, response = _refused(tmp_path, **change)
    html = response.get_data(as_text=True)
    assert words in html, html[:3000]
    assert "the recovery checkpoint is invalid" not in html
    assert words in client.get("/state/review").get_data(as_text=True)


# --------------------------------------------------------------------------
# (c) an older SM can read the file
# --------------------------------------------------------------------------

@dataclass
class _OlderRow:
    """The row type of an SM that predates ``origin`` (its fields, verbatim)."""
    dot_path: str
    old_value: Any
    new_value: Any
    source_file: str
    created: bool = False
    deleted: bool = False
    group_id: str | None = None
    actor: str = "human"


def test_the_checkpoint_carries_no_null_optional_fields(tmp_path):
    _i, _l, app, _c = _staged(tmp_path)
    path = pending_tray.sidecar_path(_ctx(app)["working_copy"])
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["entries"]
    for row in doc["entries"]:
        assert "origin" not in row, row
        assert all(v is not None for k, v in row.items() if k in ("group_id", "origin")), row
        _OlderRow(**row)                  # what the older reader does with each row


def test_the_sync_panel_restore_and_discard_selfcheck():
    import shutil
    import subprocess
    from pathlib import Path
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    root = Path(__file__).resolve().parents[1]
    try:
        subprocess.run([node, "-e", "require('jsdom')"], check=True, capture_output=True,
                       timeout=30, cwd=str(root))
    except Exception:
        pytest.skip("jsdom not installed")
    r = subprocess.run([node, str(root / "tests" / "v151_recovery_panel_selfcheck.cjs")],
                       capture_output=True, text=True, encoding="utf-8", timeout=120, cwd=str(root))
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.count("ok - ") >= 12, r.stdout
