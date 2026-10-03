"""A-22 recovery rejects partial checkpoints before publishing any data."""
import copy
import json
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from quam_state_manager.core import pending_tray, safe_io, working_copy
from quam_state_manager.core.loader import ChangeEntry, QuamStore


def _checkpoint(tmp_path):
    base = {"value": 1, "other": 2}
    live, folder = tmp_path / "live", tmp_path / "working"
    for target in (live, folder):
        target.mkdir()
        safe_io.write_state_wiring(target, base, {})
    wc = SimpleNamespace(live_folder=live, working_folder=folder, key="chip",
                         synced_live_hash=working_copy.content_hash(base, {}))
    staged = QuamStore.from_dicts({"value": 3, "other": 4}, {})
    staged.change_log = [ChangeEntry("value", 1, 3, "state"),
                         ChangeEntry("other", 2, 4, "state")]
    ctx = {"store": staged, "working_copy": wc, "origin": "live",
           "working_dirty": False, "pending_reapply": None}
    pending_tray.checkpoint(ctx)
    ctx["store"] = QuamStore.from_dicts(copy.deepcopy(base), {})
    ctx["wiring_json"] = "{}"
    return ctx, pending_tray.sidecar_path(wc)


@pytest.mark.parametrize("field,value", [
    ("entries", []), ("entries", {}), ("entries", [None]),
    ("version", 2), ("version", True), ("live_folder", "wrong-chip"),
    ("flags", None), ("flags", {}),
    ("flags.working_dirty", "false"), ("flags.staged_base", "true"),
    ("flags.pending_reapply", []), ("flags.pending_reapply_orig", []),
    ("mutation_seq", -1), ("mutation_seq", True), ("mutation_seq", "3"),
    ("state", []), ("wiring", []),
    ("row.dot_path", None), ("row.dot_path", ""),
    ("row.source_file", "unknown"), ("row.created", 1),
    ("row.deleted", "false"), ("row.actor", None), ("row.group_id", []),
    ("synced_live_hash", "wrong-sync-point"),
], ids=["empty-rows", "rows-shape", "partial-row", "version", "version-bool",
        "identity", "flags-shape", "flags-missing", "dirty-type", "base-type",
        "stash-type", "originals-type", "negative-seq", "bool-seq", "string-seq",
        "state-shape", "wiring-shape", "path-type", "path-empty", "source-type",
        "created-type", "deleted-type", "actor-type", "group-type", "sync-hash"])
def test_partial_checkpoint_never_publishes(tmp_path, field, value):
    ctx, path = _checkpoint(tmp_path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    if field.startswith("row."):
        doc["entries"][1][field[4:]] = value
    elif field.startswith("flags."):
        doc["flags"][field[6:]] = value
    else:
        doc[field] = value
    path.write_text(json.dumps(doc), encoding="utf-8")
    pending_tray.attach(ctx)
    assert ctx["store"].state == {"value": 1, "other": 2}
    assert ctx["store"].merged == {"value": 1, "other": 2}
    assert not ctx["store"].change_log
    assert ctx["working_dirty"] is False and ctx["pending_reapply"] is None
    assert "No rows were restored" in ctx["_tray_recovery_notice"]
    assert list(tmp_path.glob("*.unrecovered-*.json"))


def test_rejected_checkpoint_rename_failure_does_not_abort_load(tmp_path, monkeypatch):
    ctx, path = _checkpoint(tmp_path)
    path.write_text("{partial", encoding="utf-8")
    def denied(*args):
        raise PermissionError("checkpoint is locked")
    monkeypatch.setattr(pending_tray.os, "replace", denied)
    pending_tray.attach(ctx)
    assert ctx["store"].state == {"value": 1, "other": 2}
    assert not ctx["store"].change_log
    assert path.read_text(encoding="utf-8") == "{partial"
    assert "could not rename" in ctx["_tray_recovery_notice"]
    assert str(path) in ctx["_tray_recovery_notice"]


def test_snapshot_preparation_failure_does_not_publish(tmp_path, monkeypatch):
    ctx, _ = _checkpoint(tmp_path)
    def broken_merge(*args):
        raise ValueError("snapshot cannot be merged")
    monkeypatch.setattr(pending_tray, "merge_state_wiring", broken_merge)
    pending_tray.attach(ctx)
    assert ctx["store"].state == {"value": 1, "other": 2}
    assert not ctx["store"].change_log
    assert "snapshot cannot be merged" in ctx["_tray_recovery_notice"]


def test_save_interruption_recovers_complete_snapshot_without_writing(tmp_path, monkeypatch):
    ctx, path = _checkpoint(tmp_path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    folder = ctx["working_copy"].working_folder
    safe_io.write_state_wiring(folder, doc["state"], doc["wiring"])
    ctx["store"] = QuamStore.from_dicts(doc["state"], doc["wiring"])
    def forbidden(*args, **kwargs):
        pytest.fail("recovery wrote a chip file")
    monkeypatch.setattr(safe_io, "write_state_wiring", forbidden)
    monkeypatch.setattr(safe_io, "atomic_write_json", forbidden)
    pending_tray.attach(ctx)
    assert [asdict(e) for e in ctx["store"].change_log] == doc["entries"]
    assert ctx["store"].state == {"value": 3, "other": 4}
    assert "_tray_recovery_notice" not in ctx


def test_changed_sync_metadata_refuses_checkpoint(tmp_path):
    ctx, _ = _checkpoint(tmp_path)
    ctx["working_copy"].synced_live_hash = "new-sync-point"
    pending_tray.attach(ctx)
    assert not ctx["store"].change_log
    assert ctx["store"].state == {"value": 1, "other": 2}
    assert "sync point changed" in ctx["_tray_recovery_notice"]


def test_wholesale_content_stays_logless_on_restart(tmp_path):
    ctx, path = _checkpoint(tmp_path)
    path.unlink()
    ctx["store"] = QuamStore.from_dicts({"value": 7}, {})
    ctx["working_dirty"] = True
    pending_tray.attach(ctx)
    assert not ctx["store"].change_log
    assert ctx["store"].state == {"value": 7}
    assert ctx["working_dirty"] is True
    assert not path.exists()


@pytest.mark.parametrize("operation", ["append", "extend", "insert", "pop", "remove",
    "clear", "setitem", "delitem", "iadd", "reverse", "sort"])
def test_each_log_mutation_checkpoints_complete_rows(tmp_path, operation):
    ctx, path = _checkpoint(tmp_path)
    pending_tray.attach(ctx)
    log = ctx["store"].change_log
    extra = ChangeEntry("extra", 0, 5, "state")
    if operation == "append":
        log.append(extra)
    elif operation == "extend":
        log.extend([extra])
    elif operation == "insert":
        log.insert(0, extra)
    elif operation == "pop":
        log.pop()
    elif operation == "remove":
        log.remove(log[0])
    elif operation == "clear":
        log.clear()
    elif operation == "setitem":
        log[:] = [extra]
    elif operation == "delitem":
        del log[:1]
    elif operation == "iadd":
        log += [extra]
    elif operation == "reverse":
        log.reverse()
    elif operation == "sort":
        log.sort(key=lambda row: row.dot_path)
    if log:
        assert json.loads(path.read_text(encoding="utf-8"))["entries"] == [asdict(e) for e in log]
    else:
        assert not path.exists()


def test_row_metadata_assignment_is_durable_without_request(tmp_path):
    ctx, path = _checkpoint(tmp_path)
    pending_tray.attach(ctx)
    ctx["store"].change_log[0].actor = "by_codex"
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["entries"][0]["actor"] == "by_codex"


def test_request_completion_checkpoints_flags_set_after_log_mutation(tmp_path):
    from flask import Flask, Response
    from quam_state_manager.web import routes
    ctx, path = _checkpoint(tmp_path)
    pending_tray.attach(ctx)
    ctx["working_dirty"] = True
    ctx["staged_base"] = True
    ctx["pending_reapply"] = {"value": ["set", 3]}
    ctx["pending_reapply_orig"] = {"value": 1}
    app = Flask(__name__)
    app.config["contexts"] = {"chip": ctx}
    with app.test_request_context("/edit", method="POST"):
        routes._checkpoint_pending_tray_flags(Response())
    assert json.loads(path.read_text(encoding="utf-8"))["flags"] == {
        "working_dirty": True, "staged_base": True,
        "pending_reapply": {"value": ["set", 3]}, "pending_reapply_orig": {"value": 1}}


def test_replay_stash_recovers_operation_value_and_group(tmp_path):
    from quam_state_manager.web import routes
    ctx, _ = _checkpoint(tmp_path)
    pending_tray.attach(ctx)
    expected = {"value": ("set", 3, "group-1"), "other": ("literal", 4)}
    ctx["pending_reapply"] = expected
    ctx["pending_reapply_orig"] = {"value": 1, "other": 2}
    pending_tray.checkpoint(ctx)
    ctx["store"] = QuamStore.from_dicts({"value": 1, "other": 2}, {})
    ctx["pending_reapply"] = None
    ctx.pop("pending_reapply_orig")
    pending_tray.attach(ctx)
    assert ctx["pending_reapply"] == expected
    assert routes._untag(ctx["pending_reapply"]["value"]) == ("set", 3, "group-1")
    assert routes._untag(ctx["pending_reapply"]["other"]) == ("literal", 4, None)
    assert ctx["pending_reapply_orig"] == {"value": 1, "other": 2}


def test_batch_checkpoints_once_after_all_rows_and_metadata(tmp_path, monkeypatch):
    ctx, path = _checkpoint(tmp_path)
    pending_tray.attach(ctx)
    previous = path.read_bytes()
    writes = []
    original = safe_io.atomic_write_json
    def recorded(target, doc, **kwargs):
        if target == path:
            writes.append(copy.deepcopy(doc))
        return original(target, doc, **kwargs)
    monkeypatch.setattr(safe_io, "atomic_write_json", recorded)
    with pending_tray.batch(ctx["store"]):
        ctx["store"].change_log.append(ChangeEntry("extra", 0, 5, "state"))
        ctx["store"].change_log[0].actor = "by_codex"
        assert path.read_bytes() == previous
        assert not writes
    assert len(writes) == 1
    assert len(writes[0]["entries"]) == 3
    assert writes[0]["entries"][0]["actor"] == "by_codex"


def test_nested_batch_waits_for_outer_completion(tmp_path):
    ctx, path = _checkpoint(tmp_path)
    pending_tray.attach(ctx)
    previous = path.read_bytes()
    with pending_tray.batch(ctx["store"]):
        with pending_tray.batch(ctx["store"]):
            ctx["store"].change_log.append(ChangeEntry("extra", 0, 5, "state"))
        assert path.read_bytes() == previous
    assert len(json.loads(path.read_text(encoding="utf-8"))["entries"]) == 3


def test_http_batch_persists_only_complete_rows_and_actor(tmp_path, monkeypatch):
    from tests.test_tray_restart import _ctx, _staged
    _, _, app, client = _staged(tmp_path)
    ctx = _ctx(app)
    path = pending_tray.sidecar_path(ctx["working_copy"])
    writes = []
    original = safe_io.atomic_write_json
    def recorded(target, doc, **kwargs):
        if target == path:
            writes.append(copy.deepcopy(doc))
        return original(target, doc, **kwargs)
    monkeypatch.setattr(safe_io, "atomic_write_json", recorded)
    result = client.post("/field/edit-batch", json={"updates": [
        {"dot_path": "qubits.q1.f_01", "value": "5200000000"},
        {"dot_path": "qubits.q1.z.joint_offset", "value": "0.16"}]},
        headers={"X-SM-Agent": "codex"})
    assert result.status_code == 200 and result.get_json()["ok"]
    # One batch commit, then the request-completion flags checkpoint.
    assert len(writes) == 2
    assert all(len(doc["entries"]) == 4 for doc in writes)
    assert all(row["actor"] == "by_codex" for doc in writes for row in doc["entries"][-2:])


def test_retired_context_cannot_write_or_delete_current_checkpoint(tmp_path):
    ctx, path = _checkpoint(tmp_path)
    pending_tray.attach(ctx)
    previous = path.read_bytes()
    ctx["_pending_tray_retired"] = True
    ctx["store"].change_log[0].actor = "by_stale_request"
    assert path.read_bytes() == previous
    fresh = dict(ctx, store=QuamStore.from_dicts({"value": 1, "other": 2}, {}))
    fresh.pop("_pending_tray_retired")
    pending_tray.attach(fresh)
    assert fresh["store"].change_log[0].actor == "human"
    fresh["store"].change_log[0].actor = "by_current_request"
    current = path.read_bytes()
    assert current != previous
    ctx["store"].change_log.clear()
    assert path.read_bytes() == current
