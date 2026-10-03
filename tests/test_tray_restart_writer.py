"""A-22 redesign (docs/265 §Redesign): the tray is durable without a write on
the request thread, without the documents in the sidecar, and a restart
re-applies the ROWS to the verified working files exactly."""
import copy
import json
import threading
import time
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from quam_state_manager.core import pending_tray, safe_io, working_copy
from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.modifier import Modifier
from tests.test_tray_restart import _ctx, _open, _staged


def _spy(monkeypatch, path):
    writes = []
    original = safe_io.atomic_write_json

    def recorded(target, doc, **kwargs):
        if target == path:
            writes.append((threading.get_ident(), copy.deepcopy(doc)))
        return original(target, doc, **kwargs)
    monkeypatch.setattr(safe_io, "atomic_write_json", recorded)
    return writes


# --------------------------------------------------------------------------
# HTTP: what a request costs, and when the write lands
# --------------------------------------------------------------------------

def test_the_sidecar_carries_rows_not_documents(tmp_path):
    instance, live, app, _ = _staged(tmp_path)
    ctx = _ctx(app)
    # a chip with a big document: the sidecar must not grow with it
    ctx["store"].state["bulk"] = ["x" * 100] * 20000          # ~2 MB in memory
    path = pending_tray.sidecar_path(ctx["working_copy"])
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert set(doc) == {"version", "live_folder", "base_hash", "applied",
                        "synced_live_hash", "entries", "mutation_seq", "flags"}
    assert doc["version"] == pending_tray.VERSION and doc["applied"] == 0
    assert doc["base_hash"] == working_copy.working_content_hash(ctx["working_copy"])
    assert [e["dot_path"] for e in doc["entries"]] == ["qubits.q1.f_01", "qubits.q1.z.joint_offset"]
    pending_tray.checkpoint(ctx)
    assert path.stat().st_size < 4096


def test_requests_inside_the_window_coalesce_into_one_write(tmp_path, monkeypatch):
    instance, live, app, client = _staged(tmp_path)
    ctx = _ctx(app)
    path = pending_tray.sidecar_path(ctx["working_copy"])
    monkeypatch.setattr(pending_tray, "DEBOUNCE_S", 60.0)
    monkeypatch.setattr(pending_tray, "MAX_DELAY_S", 60.0)
    writes = _spy(monkeypatch, path)
    for i in range(5):
        r = client.post("/field/edit", data={"dot_path": "qubits.q1.f_01",
                                             "value": str(5100000001 + i)})
        assert r.status_code == 200 and r.get_json()["ok"]
    assert writes == []                    # five requests, nothing written yet
    pending_tray.flush_all()
    assert len(writes) == 1
    assert len(writes[0][1]["entries"]) == 7
    assert writes[0][1]["entries"][-1]["new_value"] == 5100000005


def test_the_background_writer_lands_a_request_within_the_window(tmp_path):
    instance, live, app, client = _staged(tmp_path)
    ctx = _ctx(app)
    path = pending_tray.sidecar_path(ctx["working_copy"])
    r = client.post("/field/edit", data={"dot_path": "qubits.q1.f_01", "value": "5200000000"})
    assert r.status_code == 200 and r.get_json()["ok"]
    t0 = time.monotonic()
    deadline = t0 + pending_tray.MAX_DELAY_S + 5.0
    rows = None
    while time.monotonic() < deadline:     # no flush: only the writer thread can land it
        # read as SM reads it (share-delete + retry): the writer may be
        # replacing the file at this very moment
        rows = safe_io.read_json(path)["entries"]
        if len(rows) == 3:
            break
        time.sleep(0.02)
    assert rows is not None and len(rows) == 3 and rows[-1]["new_value"] == 5200000000


def test_an_emptied_tray_is_dropped_by_its_own_request(tmp_path, monkeypatch):
    live = tmp_path / "chip" / "quam_state"
    live.mkdir(parents=True)
    names = [f"q{i}" for i in range(1, 51)]
    (live / "state.json").write_text(json.dumps({
        "qubits": {n: {"id": n, "f_01": 5000000000 + i} for i, n in enumerate(names)},
        "qubit_pairs": {}, "active_qubit_names": names}), encoding="utf-8")
    (live / "wiring.json").write_text("{}", encoding="utf-8")
    app, client, _ = _open(tmp_path / "instance", live)
    ctx = _ctx(app)
    path = pending_tray.sidecar_path(ctx["working_copy"])
    assert client.post("/field/edit", data={"dot_path": "qubits.q1.f_01",
                                            "value": "5100000000"}).get_json()["ok"]
    r = client.post("/field/edit-batch", json={"updates": [
        {"dot_path": f"qubits.{n}.f_01", "value": str(5200000000 + i)}
        for i, n in enumerate(names)]})
    assert r.status_code == 200 and r.get_json()["ok"]
    assert len(ctx["store"].change_log) == 51
    pending_tray.flush_all()
    writes = _spy(monkeypatch, path)
    assert client.post("/undo").status_code == 200       # ONE group of 50 rows
    assert len(ctx["store"].change_log) == 1
    pending_tray.flush_all()
    assert len(writes) == 1                               # never 50
    assert client.post("/undo").status_code == 200       # the tray is now empty
    # no flush: the request that emptied the tray removed the file itself
    assert not path.exists()
    assert len(writes) == 1


# --------------------------------------------------------------------------
# Recovery: rows re-applied through the Modifier, exactly
# --------------------------------------------------------------------------

def _chip(tmp_path, base):
    live, folder = tmp_path / "live", tmp_path / "working"
    for target in (live, folder):
        target.mkdir()
        safe_io.write_state_wiring(target, copy.deepcopy(base), {})
    wc = SimpleNamespace(live_folder=live, working_folder=folder, key="chip",
                         synced_live_hash=working_copy.content_hash(base, {}))
    return wc


def _attached(wc, state, build_lock=None):
    ctx = {"store": QuamStore.from_dicts(copy.deepcopy(state), {}), "working_copy": wc,
           "origin": "live", "working_dirty": False, "pending_reapply": None}
    pending_tray.attach(ctx, build_lock=build_lock)
    return ctx


def test_restore_replays_created_deleted_and_nested_rows_exactly(tmp_path):
    base = {"value": 1, "other": 2, "box": {"a": 1, "b": [1, 2]}}
    wc = _chip(tmp_path, base)
    ctx = _attached(wc, base)
    mod = Modifier(ctx["store"])
    mod.create_subtree("pulse", {"amp": 0.1, "len": 20})
    mod.set_value("pulse.amp", 0.2)            # inside the container the row above created
    mod.set_value("box.b", [3, 4, 5])          # a container written whole ...
    mod.set_value("box.b.1", 9)                # ... then edited inside
    mod.delete_subtree("box.a")
    mod.batch_set({"value": 5, "other": 6})
    ctx["store"].change_log[-1].actor = "by_codex"
    held = copy.deepcopy(ctx["store"].state)
    rows = [asdict(e) for e in ctx["store"].change_log]
    again = _attached(wc, base)                # a restart: a fresh store of the files
    assert "_tray_recovery_notice" not in again
    assert again["store"].state == held
    assert again["store"].merged == held
    assert [asdict(e) for e in again["store"].change_log] == rows
    undo = Modifier(again["store"])
    while again["store"].change_log:
        undo.undo_group()
    assert again["store"].state == base


def test_interrupted_save_then_a_new_edit_restores_every_row(tmp_path):
    base = {"value": 1, "other": 2}
    staged = {"value": 3, "other": 4}
    wc = _chip(tmp_path, base)
    ctx = _attached(wc, base)
    mod = Modifier(ctx["store"])
    mod.set_value("value", 3)
    mod.set_value("other", 4)
    # the save installed the pair, then the process died before clearing the log
    safe_io.write_state_wiring(wc.working_folder, copy.deepcopy(staged), {})
    first = _attached(wc, staged)
    assert len(first["store"].change_log) == 2 and first["store"].state == staged
    Modifier(first["store"]).set_value("value", 7)   # files hold rows 1-2, not row 3
    doc = json.loads(pending_tray.sidecar_path(wc).read_text(encoding="utf-8"))
    assert doc["applied"] == 2
    second = _attached(wc, staged)
    assert "_tray_recovery_notice" not in second
    assert second["store"].state == {"value": 7, "other": 4}
    assert [e.new_value for e in second["store"].change_log] == [3, 4, 7]


def test_undoing_a_row_the_files_already_hold_refuses_instead_of_guessing(tmp_path):
    base = {"value": 1, "other": 2}
    staged = {"value": 3, "other": 4}
    wc = _chip(tmp_path, base)
    ctx = _attached(wc, base)
    mod = Modifier(ctx["store"])
    mod.set_value("value", 3)
    mod.set_value("other", 4)
    safe_io.write_state_wiring(wc.working_folder, copy.deepcopy(staged), {})
    first = _attached(wc, staged)
    Modifier(first["store"]).undo()            # memory: other=2; the files still say 4
    assert first["store"].state == {"value": 3, "other": 2}
    second = _attached(wc, staged)
    assert not second["store"].change_log
    assert second["store"].state == staged
    assert "No rows were restored" in second["_tray_recovery_notice"]


def test_a_write_never_snapshots_inside_a_wholesale_replacement(tmp_path):
    base = {"value": 1, "other": 2}
    wc = _chip(tmp_path, base)
    lock = threading.RLock()
    ctx = _attached(wc, base, build_lock=lock)
    path = pending_tray.sidecar_path(wc)
    held, release = threading.Event(), threading.Event()

    def replacer():                            # a sync/stage holding the build lock
        with lock:
            held.set()
            release.wait(10)
    t = threading.Thread(target=replacer)
    t.start()
    held.wait(5)
    Modifier(ctx["store"]).set_value("value", 5)   # outside a request: written at once ...
    time.sleep(pending_tray.MAX_DELAY_S + 0.3)
    assert not path.exists()                   # ... unless a replacement is in flight
    release.set()
    t.join(5)
    deadline = time.monotonic() + 5.0
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert safe_io.read_json(path)["entries"][0]["new_value"] == 5


def test_a_failing_background_write_is_announced_once(tmp_path, monkeypatch):
    instance, live, app, client = _staged(tmp_path)
    ctx = _ctx(app)
    path = pending_tray.sidecar_path(ctx["working_copy"])
    original = safe_io.atomic_write_json

    def full(target, doc, **kwargs):
        if target == path:
            raise OSError("disk full")
        return original(target, doc, **kwargs)
    monkeypatch.setattr(safe_io, "atomic_write_json", full)
    r = client.post("/field/edit", data={"dot_path": "qubits.q1.f_01", "value": "5200000000"})
    assert r.status_code == 200 and r.get_json()["ok"]
    deadline = time.monotonic() + 5.0
    while "_tray_recovery_notice" not in ctx and time.monotonic() < deadline:
        time.sleep(0.02)
    notice = ctx.pop("_tray_recovery_notice", "")
    assert "not being saved for a restart" in notice and "disk full" in notice
    time.sleep(pending_tray.RETRY_S + 0.5)     # it is retried, and not announced again
    assert "_tray_recovery_notice" not in ctx
    ctx["_pending_tray_retired"] = True        # stop the retries before the patch is undone


def test_a_batch_outside_a_request_is_one_write(tmp_path, monkeypatch):
    base = {f"v{i}": i for i in range(20)}
    wc = _chip(tmp_path, base)
    ctx = _attached(wc, base)
    path = pending_tray.sidecar_path(wc)
    writes = _spy(monkeypatch, path)
    Modifier(ctx["store"]).batch_set({f"v{i}": i + 100 for i in range(20)})
    assert len(writes) == 1                    # the autofit writer's path: not 20
    assert [r["new_value"] for r in writes[0][1]["entries"]] == [i + 100 for i in range(20)]
