"""docs/254: while an unverified live write's mark is held, a person's press never carries the
values that write kept to the chip unnamed. Every live-writing door for a person answers 409 with
the kept paths and values; the person's confirm re-sends with the mark's id, and a mark set after
the confirm opened is named again. Also: the persisted mark shows on the control after a restart,
the approval's stale check reads the flag and the mark outside its fail-open probe."""
import ast
import json
import subprocess
from pathlib import Path

import pytest

from tests.test_approval_now_anchor import (UNVERIFIED_HELD, _case, _ctx, _refused_first, _restarted,
                                            _second_approval, _sync_control_text, press)
from tests.test_v151_r1_apply_to_chip import env as dataset_env

LEAD = "These values from an approval that may not have reached the chip will be written too."
LEAD_WHOLE = "These values from an earlier live write that may not have reached the chip will be written too."
JSON = {"Accept": "application/json"}


def _chip(case) -> dict:
    return json.loads((case[2] / "state.json").read_text(encoding="utf-8"))


def _mark(case) -> str | None:
    with case[0].app_context():
        return _ctx(case)["working_copy"].unverified_write


def _apply(client, **extra):
    return client.post("/state/apply-to-live", data={"seen_changes": "0", **extra}, headers=JSON)


def test_a_persons_apply_names_the_kept_values_and_writes_nothing(tmp_path, monkeypatch):
    case, _, _ = _refused_first(tmp_path, monkeypatch, "unknown", False)
    before = (case[2] / "state.json").read_bytes()
    r = _apply(case[1])
    assert r.status_code == 409, r.get_data(as_text=True)
    body = r.get_json()
    assert body["status"] == "unverified_kept" and body["conflict"] == "unverified_kept"
    assert body["paths"] == ["value"] and body["rows"] == [{"path": "value", "old": "10", "value": "30"}]
    assert body["message"] == LEAD + " Take live instead to see what the chip holds."
    assert body["token"] == _mark(case)
    assert (case[2] / "state.json").read_bytes() == before


def test_the_persons_confirm_writes_the_kept_values_and_clears_the_mark(tmp_path, monkeypatch):
    case, _, _ = _refused_first(tmp_path, monkeypatch, "unknown", False)
    token = _apply(case[1]).get_json()["token"]
    r = _apply(case[1], ack_unverified=token)
    assert r.status_code == 200, r.get_data(as_text=True)
    assert _chip(case) == {"value": 30, "third": 5}
    assert _mark(case) is None
    with case[0].app_context():
        meta = json.loads(Path(_ctx(case)["working_copy"].meta_path()).read_text(encoding="utf-8"))
    assert meta.get("unverified_write") is None and meta.get("unverified_kept") is None


def test_a_mark_set_after_the_confirm_opened_is_named_again(tmp_path, monkeypatch):
    from quam_state_manager.core import working_copy
    case, _, _ = _refused_first(tmp_path, monkeypatch, "unknown", False)
    before = (case[2] / "state.json").read_bytes()
    token = _apply(case[1]).get_json()["token"]
    with case[0].app_context():
        working_copy.mark_unverified_write(_ctx(case)["working_copy"],
                                           kept=[{"path": "value", "old": 10, "kept": 30}])
    r = _apply(case[1], ack_unverified=token)
    assert r.status_code == 409, r.get_data(as_text=True)
    assert r.get_json()["status"] == "unverified_kept"
    assert r.get_json()["token"] != token and r.get_json()["token"] == _mark(case)
    assert (case[2] / "state.json").read_bytes() == before


def test_a_reject_keeps_the_values_and_the_apply_still_names_them(tmp_path, monkeypatch):
    case, _, _ = _refused_first(tmp_path, monkeypatch, "unknown", True)
    before = (case[2] / "state.json").read_bytes()
    r = _apply(case[1])
    assert r.status_code == 409 and r.get_json()["paths"] == ["value"]
    assert r.get_json()["status"] == "unverified_kept"
    assert (case[2] / "state.json").read_bytes() == before


def test_the_kept_rows_are_saved_beside_the_mark(tmp_path, monkeypatch):
    from quam_state_manager.core import working_copy
    case, _, _ = _refused_first(tmp_path, monkeypatch, "unknown", False)
    with case[0].app_context():
        wc = _ctx(case)["working_copy"]
    meta = json.loads(Path(wc.meta_path()).read_text(encoding="utf-8"))
    assert meta["unverified_kept"] == [{"path": "value", "old": 10, "kept": 30}]
    assert working_copy.load(case[3], case[2]).unverified_kept == meta["unverified_kept"]


def test_a_mark_without_rows_names_what_the_working_copy_holds_that_live_does_not(tmp_path, monkeypatch):
    """Meta written before the mark named its rows: the preflight lists
    the working copy against live, outside the tray's own edits."""
    case, _, _ = _refused_first(tmp_path, monkeypatch, "unknown", False)
    with case[0].app_context():
        mp = Path(_ctx(case)["working_copy"].meta_path())
    meta = json.loads(mp.read_text(encoding="utf-8"))
    meta.pop("unverified_kept", None)
    mp.write_text(json.dumps(meta), encoding="utf-8")
    _, client = _restarted(case)
    before = (case[2] / "state.json").read_bytes()
    r = _apply(client)
    assert r.status_code == 409, r.get_data(as_text=True)
    assert r.get_json()["status"] == "unverified_kept"
    assert r.get_json()["rows"] == [{"path": "value", "value": "30"}]
    assert r.get_json()["message"] == LEAD_WHOLE + " Take live instead to see what the chip holds."
    assert (case[2] / "state.json").read_bytes() == before


def test_after_a_restart_the_control_and_the_poll_still_say_not_verified(tmp_path, monkeypatch):
    case, _, _ = _refused_first(tmp_path, monkeypatch, "unknown", False)
    tip = "the write may have reached the live chip; take live to see what it holds."
    assert "Apply not verified: check the chip" in _sync_control_text(case[1])
    assert tip in case[1].get("/").get_data(as_text=True)
    _, client = _restarted(case)
    assert "Apply not verified: check the chip" in _sync_control_text(client)
    assert tip in client.get("/").get_data(as_text=True)
    sync = client.get("/state/drift").get_json()["sync"]
    assert sync["state"] == "refused" and sync["unverified"] is True


def test_an_error_inside_the_hash_probe_still_refuses_while_the_mark_is_held(tmp_path, monkeypatch):
    from quam_state_manager.core import working_copy
    case, _, _ = _refused_first(tmp_path, monkeypatch, "unknown", False)
    before = (case[2] / "state.json").read_bytes()
    with case[0].app_context():
        _ctx(case)["live_diverged"] = False

    def boom(wc):
        raise RuntimeError("probe failed")
    monkeypatch.setattr(working_copy, "live_diverged_now", boom)
    r = _second_approval(case, case[1])
    assert r.status_code == 409, r.get_json()
    assert r.get_json()["error"].startswith("stale_live: " + UNVERIFIED_HELD)
    assert (case[2] / "state.json").read_bytes() == before


def test_a_raised_flag_alone_refuses_an_approval(tmp_path, monkeypatch):
    """No mark and no moved hash: the drift poll's raise-only flag (live moved, then moved back on
    a dirty context) still holds the approval."""
    from quam_state_manager.core import working_copy
    case = _case(tmp_path, {"value": 10, "third": 5}, [{"path": "third", "old": 5, "new": 7}])
    before = (case[2] / "state.json").read_bytes()
    with case[0].app_context():
        ctx = _ctx(case)
        assert ctx["working_copy"].unverified_write is None
        ctx["live_diverged"] = True
    monkeypatch.setattr(working_copy, "live_diverged_now", lambda wc: False)
    r = press(case, [{"path": "third", "new": 7, "seen_now": 5}])
    assert r.status_code == 409, r.get_json()
    assert r.get_json()["error"].startswith("stale_live: " + UNVERIFIED_HELD)
    assert (case[2] / "state.json").read_bytes() == before


def test_a_mark_whose_values_were_taken_out_asks_nothing(tmp_path, monkeypatch):
    """Ctrl+Z took the kept value out: the preflight has nothing to name, so the Apply goes on.
    The approving window presses Ctrl+Z, so its own step asks no other-window question."""
    case, _, _ = _refused_first(tmp_path, monkeypatch, "unknown", False, window="window-a")
    assert case[1].post("/undo").status_code == 200
    r = case[1].post("/state/apply-to-live", data={"seen_changes": "1"}, headers=JSON)
    assert r.status_code == 200, r.get_data(as_text=True)
    assert _chip(case) == {"value": 10, "third": 5}


def _active_flush_session(case):
    """Exercise an active flush at the gate, rather than the outer session-off refusal."""
    from quam_state_manager.web import routes
    with case[0].app_context():
        _ctx(case)["auto_apply"] = routes._new_auto_session(pull=False, pull_replace=False, push=True)


def test_an_unarmed_flush_is_stopped_before_the_kept_values_gate(tmp_path, monkeypatch):
    case, _, _ = _refused_first(tmp_path, monkeypatch, "unknown", False)
    before = (case[2] / "state.json").read_bytes()
    assert case[1].post("/auto-apply/arm").status_code == 409
    r = _door_auto_flush(case[1])
    assert r.status_code == 409
    assert r.get_json() == {"status": "stopped", "message": "Auto-Sync is off"}
    assert (case[2] / "state.json").read_bytes() == before
    assert _mark(case)


def test_auto_sync_after_an_unverified_write_writes_nothing(tmp_path, monkeypatch):
    case, _, _ = _refused_first(tmp_path, monkeypatch, "unknown", False)
    before = (case[2] / "state.json").read_bytes()
    client = case[1]
    _active_flush_session(case)
    assert client.post("/field/edit", data={"dot_path": "third", "value": "7"}).status_code == 200
    r = client.post("/state/apply-to-live", data={"auto_flush": "1"}, headers=JSON)
    assert r.status_code == 409, r.get_data(as_text=True)
    assert r.get_json()["status"] == "unverified_kept"
    assert (case[2] / "state.json").read_bytes() == before


def test_a_persons_own_unverified_press_sets_no_mark(tmp_path, monkeypatch):
    """A person's own Apply whose outcome is unknown keeps its edits as that person's unapplied
    edits (nothing of an approval rides on them): no mark, so no preflight."""
    from quam_state_manager.core import working_copy
    from quam_state_manager.web import routes
    case = _case(tmp_path, {"value": 10, "third": 5}, [{"path": "value", "old": 10, "new": 30}])

    def raises(*a, **k):
        raise OSError(5, "read-back failed")
    monkeypatch.setattr(working_copy, "apply_to_live", raises)
    monkeypatch.setattr(routes, "_write_outcome", lambda pending: "unknown")
    assert case[1].post("/field/edit", data={"dot_path": "value", "value": "30"}).status_code == 200
    r = case[1].post("/state/apply-to-live", data={"seen_changes": "1"}, headers=JSON)
    assert r.status_code == 409
    assert (r.get_json() or {}).get("status") != "unverified_kept"
    assert _mark(case) is None


def test_legacy_meta_without_a_sync_point_hash_still_holds_an_approval(tmp_path, monkeypatch):
    case, _, _ = _refused_first(tmp_path, monkeypatch, "unknown", False)
    with case[0].app_context():
        mp = Path(_ctx(case)["working_copy"].meta_path())
    meta = json.loads(mp.read_text(encoding="utf-8"))
    meta.pop("synced_live_hash", None)
    mp.write_text(json.dumps(meta), encoding="utf-8")
    before = (case[2] / "state.json").read_bytes()
    _, client = _restarted(case)
    r = _second_approval(case, client)
    assert r.status_code == 409 and r.get_json()["error"].startswith("stale_live: ")
    assert (case[2] / "state.json").read_bytes() == before
    assert json.loads(mp.read_text(encoding="utf-8")).get("unverified_write")


# Ratchet: every live-writing door for a person --------------------------------------------

_WRITERS = ("apply_to_live", "_sync_pull_apply_to_live", "state_apply_to_live")
#: every function in the web package that calls a live writer, and why the kept values cannot
#: ride on it unnamed. A new caller fails this test until it is listed here with its reason.
_DOORS = {
    "routes.state_apply_to_live": "gated: _unverified_kept_refusal before the save",
    "routes._sync_pull_apply_to_live": "gated: _unverified_kept_refusal unless kept_gate=False",
    "routes.state_sync": "gated before its pull (the pull would clear the mark, then replay)",
    "routes._flush_walk_step_live": "through _sync_pull_apply_to_live (gated)",
    "routes.state_take_live_restore": "through state_apply_to_live (gated)",
    "routes.dataset_load_state": "exempt: the run's state replaces the working content wholesale",
    "routes.state_history_restore_live": "exempt: writes the chosen version, not the working copy",
    "agent_api._stage_writes": "an approval: refused while the mark is held (stale_live)",
}


def test_every_live_writing_door_is_listed_with_its_gate():
    pkg = Path(__file__).resolve().parents[1] / "quam_state_manager" / "web"
    found = set()
    for f in sorted(pkg.glob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for n in ast.walk(fn):
                if isinstance(n, ast.Call):
                    c = n.func
                    name = c.attr if isinstance(c, ast.Attribute) else getattr(c, "id", None)
                    if name in _WRITERS:
                        found.add(f"{f.stem}.{fn.name}")
    assert found == set(_DOORS), sorted(found ^ set(_DOORS))
    routes_src = (pkg / "routes.py").read_text(encoding="utf-8")
    assert routes_src.count("kept_gate=False)") == 2      # the dataset door's two calls, nothing else
    assert routes_src.count("_kept_ref = _unverified_kept_refusal(ctx)\n") == 2   # apply-to-live, /state/sync


def _door_plain(client):
    return _apply(client)


def _door_keep_mine(client):
    return client.post("/state/apply-to-live?force=1", data={"seen_changes": "0"}, headers=JSON)


def _door_review(client):
    return client.post("/state/apply-to-live", data={"seen_changes": "0"})


def _door_auto_flush(client):
    return client.post("/state/apply-to-live", data={"auto_flush": "1"}, headers=JSON)


def _door_sync_apply(client):
    return client.post("/state/sync", data={"mode": "apply", "seen_changes": "0"})


def _door_sync_apply_forced(client):
    return client.post("/state/sync", data={"mode": "apply", "force": "1", "seen_changes": "0"})


@pytest.mark.parametrize("door", [_door_plain, _door_keep_mine, _door_review, _door_auto_flush,
                                  _door_sync_apply, _door_sync_apply_forced],
                         ids=lambda d: d.__name__[6:])
def test_no_persons_door_writes_kept_values_without_the_acknowledgement(tmp_path, monkeypatch, door):
    case, _, _ = _refused_first(tmp_path, monkeypatch, "unknown", False)
    before = (case[2] / "state.json").read_bytes()
    if door is _door_auto_flush:
        _active_flush_session(case)
    r = door(case[1])
    assert r.status_code == 409, (r.status_code, r.get_data(as_text=True)[:300])
    assert r.get_json()["status"] == "unverified_kept"
    assert (case[2] / "state.json").read_bytes() == before
    assert _mark(case)


def test_the_sync_door_writes_them_with_the_acknowledgement(tmp_path, monkeypatch):
    case, _, _ = _refused_first(tmp_path, monkeypatch, "unknown", False)
    first = case[1].post("/state/sync", data={"mode": "apply", "seen_changes": "0"})
    body = first.get_json()
    assert body["status"] == "unverified_kept" and body["paths"] == ["value"]
    r = case[1].post("/state/sync", data={"mode": "apply", "seen_changes": "0", "ack_unverified": body["token"]})
    assert r.status_code == 200 and r.get_json()["status"] == "ok", r.get_data(as_text=True)[:300]
    assert _chip(case) == {"value": 30, "third": 5}
    assert _mark(case) is None


@pytest.fixture
def two_kept_rows(tmp_path, monkeypatch):
    from quam_state_manager.core import working_copy
    from quam_state_manager.web import routes
    case = _case(tmp_path, {"value": 10, "third": 5}, [
        {"path": "value", "old": 10, "new": 30}, {"path": "third", "old": 5, "new": 7}])

    def raises(*args, **kwargs):
        raise OSError(5, "read-back failed")

    with monkeypatch.context() as held:
        held.setattr(working_copy, "apply_to_live", raises)
        held.setattr(routes, "_write_outcome", lambda pending: "unknown")
        r = press(case, [{"path": "value", "new": 30, "seen_now": 10},
                         {"path": "third", "new": 7, "seen_now": 5}])
        assert r.status_code == 409
    return case


def test_every_meta_row_is_named_with_its_old_and_kept_value(two_kept_rows):
    case = two_kept_rows
    with case[0].app_context():
        wc = _ctx(case)["working_copy"]
    meta = json.loads(Path(wc.meta_path()).read_text(encoding="utf-8"))
    before = (case[2] / "state.json").read_bytes()
    body = _apply(case[1]).get_json()
    assert body["status"] == "unverified_kept"
    assert len(body["rows"]) == len(meta["unverified_kept"]) == 2
    assert body["paths"] == ["value", "third"]
    assert body["rows"] == [{"path": "value", "old": "10", "value": "30"},
                            {"path": "third", "old": "5", "value": "7"}]
    assert body["message"] == LEAD + " Take live instead to see what the chip holds."
    tray = case[1].get("/state/tray").get_data(as_text=True)
    assert 'data-change-count="0"' in tray
    assert "Apply not verified" in _sync_control_text(case[1])
    assert (case[2] / "state.json").read_bytes() == before


@pytest.mark.parametrize("legacy", [False, True])
def test_the_dialog_lists_values_once_and_explains_cancel(two_kept_rows, legacy):
    body = _apply(two_kept_rows[1]).get_json()
    if legacy:
        for row in body["rows"]:
            row.pop("old")
    result = _dialog_result(body)
    text = result["message"]
    assert result["ok"] is False
    assert text.count("value  " + ("" if legacy else "10 -> ") + "30") == 1
    assert text.count("third  " + ("" if legacy else "5 -> ") + "7") == 1
    assert text.count("30") == 1 and text.count("7") == 1
    assert "Cancel = write nothing; the values stay in SM." in text
    assert "In the top bar, open Apply not verified and choose Take live" in text
    assert "the tray shows them" not in text


def _dialog_result(body):
    script = r"""
const fs = require('fs'), vm = require('vm');
const data = JSON.parse(fs.readFileSync(0, 'utf8'));
const src = fs.readFileSync('quam_state_manager/web/static/app.js', 'utf8');
let message = '';
const window = { smConfirm: text => { message = text; return Promise.resolve(false); } };
const ctx = vm.createContext({ window, Promise });
const start = src.indexOf('window.ValueDelta = (function () {');
vm.runInContext(src.slice(start, src.indexOf('})();', start) + 5), ctx);
const ask = src.indexOf('window._confirmUnverifiedKept = function');
vm.runInContext(src.slice(ask, src.indexOf('\n};', ask) + 3), ctx);
window._confirmUnverifiedKept(data).then(ok => process.stdout.write(JSON.stringify({ ok, message })));
"""
    run = subprocess.run(["node", "-e", script], input=json.dumps(body), text=True,
                         capture_output=True, check=True, timeout=30)
    return json.loads(run.stdout)


def test_a_legacy_named_row_without_old_shows_only_the_kept_value(tmp_path, monkeypatch):
    case, _, _ = _refused_first(tmp_path, monkeypatch, "unknown", False)
    with case[0].app_context():
        mp = Path(_ctx(case)["working_copy"].meta_path())
    meta = json.loads(mp.read_text(encoding="utf-8"))
    meta["unverified_kept"][0].pop("old")
    mp.write_text(json.dumps(meta), encoding="utf-8")
    _, client = _restarted(case)
    body = _apply(client).get_json()
    assert body["status"] == "unverified_kept"
    assert body["rows"] == [{"path": "value", "value": "30"}]
    assert body["message"] == LEAD + " Take live instead to see what the chip holds."


@pytest.mark.parametrize("old, kept", [
    (0.12345671, 0.12345674),
    (5221600123.0, 5221600523.5),
    ([1, 2], [True, None]),
    ([1, 2], [True, None] * 30),
    ("ns", "\u00b5s"),
])
def test_displayed_values_round_trip_to_every_persisted_value(tmp_path, monkeypatch, old, kept):
    from quam_state_manager.core import working_copy
    from quam_state_manager.web import routes
    case = _case(tmp_path, {"value": old, "third": 5}, [
        {"path": "value", "old": old, "new": kept}, {"path": "third", "old": 5, "new": 7}])

    def raises(*args, **kwargs):
        raise OSError(5, "read-back failed")

    with monkeypatch.context() as held:
        held.setattr(working_copy, "apply_to_live", raises)
        held.setattr(routes, "_write_outcome", lambda pending: "unknown")
        response = press(case, [{"path": "value", "seen_now": old, "new": kept},
                                {"path": "third", "seen_now": 5, "new": 7}])
        assert response.status_code == 409
    with case[0].app_context():
        meta_path = Path(_ctx(case)["working_copy"].meta_path())
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    body = _apply(case[1]).get_json()
    assert body["status"] == "unverified_kept"
    assert len(body["rows"]) == len(meta["unverified_kept"]) == 2
    persisted = {row["path"]: row for row in meta["unverified_kept"]}
    text = _dialog_result(body)["message"]
    for row in body["rows"]:
        saved = persisted[row["path"]]
        assert row["old"] == json.dumps(saved["old"], ensure_ascii=False)
        assert row["value"] == json.dumps(saved["kept"], ensure_ascii=False)
        assert json.loads(row["old"]) == saved["old"]
        assert json.loads(row["value"]) == saved["kept"]
        assert row["old"] != row["value"]
        assert row["path"] + "  " + row["old"] + " -> " + row["value"] in text
    assert _apply(case[1], ack_unverified=body["token"]).status_code == 200
    assert _chip(case) == {"value": kept, "third": 7}


def test_a_dataset_mark_records_the_live_old_value_before_writing(dataset_env, monkeypatch):
    from quam_state_manager.core import safe_io, working_copy
    from quam_state_manager.web import routes
    env = dataset_env
    live = env["live"].resolve()
    state = json.loads((live / "state.json").read_text(encoding="utf-8"))
    name = next(iter(state["qubits"]))
    state["qubits"][name]["z"]["joint_offset"] = 0.081
    state["removed_value"] = 42
    (live / "state.json").write_text(json.dumps(state), encoding="utf-8")
    before = (live / "state.json").read_bytes()
    real_write = safe_io.write_state_wiring_bytes

    def raises(folder, *args, **kwargs):
        if Path(folder).resolve() == live:
            raise OSError(5, "write could not be confirmed")
        return real_write(folder, *args, **kwargs)

    with monkeypatch.context() as held:
        held.setattr(safe_io, "write_state_wiring_bytes", raises)
        held.setattr(routes, "_write_outcome", lambda pending: "unknown")
        response = env["client"].post(f"/dataset/{env['uid']}/load-state?apply=1")
        assert response.status_code == 409
    with env["app"].app_context():
        wc = routes._active_ctx()["working_copy"]
    meta = json.loads(Path(wc.meta_path()).read_text(encoding="utf-8"))
    rows = {row["path"]: row for row in meta["unverified_kept"]}
    path = f"qubits.{name}.z.joint_offset"
    assert rows[path] == {"path": path, "old": 0.081, "kept": 0.079, "src": "dataset_apply"}
    assert rows["removed_value"] == {"path": "removed_value", "old": 42, "kept": None,
                                     "src": "dataset_apply", "removed": True}
    assert working_copy.load(env["app"].instance_path, live).unverified_kept == meta["unverified_kept"]
    body = _door_keep_mine(env["client"]).get_json()
    assert body["status"] == "unverified_kept"
    assert body["message"] == LEAD_WHOLE + " Take live instead to see what the chip holds."
    shown = next(row for row in body["rows"] if row["path"] == path)
    assert shown == {"path": path, "old": "0.081", "value": "0.079"}
    text = _dialog_result(body)["message"]
    assert path + "  0.081 -> 0.079" in text
    assert "removed_value  42 -> (removed)" in text
    assert (live / "state.json").read_bytes() == before


@pytest.fixture
def live_and_kept_values():
    return {"present_value": 2, "null_value": None}, {"present_value": 1, "null_value": 1, "new_in_run": 1}


@pytest.mark.parametrize("door", ["dataset", "approval"])
def test_every_kept_row_builder_distinguishes_absent_from_null(
        dataset_env, tmp_path, monkeypatch, live_and_kept_values, door):
    from quam_state_manager.core import safe_io
    from quam_state_manager.web import routes
    live_values, kept_values = live_and_kept_values
    env = dataset_env
    for folder, values in [(env["live"], live_values), (env["run"] / "quam_state", kept_values)]:
        path = folder / "state.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        state.update(values)
        path.write_text(json.dumps(state), encoding="utf-8")
    if door == "approval":
        approval_dir = tmp_path / "approval"
        approval_dir.mkdir()
        case = _case(approval_dir, live_values, [
            {"path": path, "old": live_values.get(path), "new": value, "created": path not in live_values}
            for path, value in kept_values.items()])
        app, client, folder = case[:3]
    else:
        app, client, folder = env["app"], env["client"], env["live"]
    real_write = safe_io.write_state_wiring_bytes

    def raises(folder, *args, **kwargs):
        if Path(folder).resolve() == live_folder:
            raise OSError(5, "write could not be confirmed")
        return real_write(folder, *args, **kwargs)

    live_folder = folder.resolve()
    before = (folder / "state.json").read_bytes()
    with monkeypatch.context() as held:
        held.setattr(safe_io, "write_state_wiring_bytes", raises)
        held.setattr(routes, "_write_outcome", lambda pending: "unknown")
        if door == "dataset":
            response = client.post(f"/dataset/{env['uid']}/load-state?apply=1")
        else:
            response = press(case, [
                {"path": path, "new": value, **({"seen_now": live_values[path]} if path in live_values
                                               else {"seen_unknown": True})}
                for path, value in kept_values.items()])
        assert response.status_code == 409, response.get_json()
    with app.app_context():
        wc = routes._active_ctx()["working_copy"]
    meta = json.loads(Path(wc.meta_path()).read_text(encoding="utf-8"))
    rows = {row["path"]: row for row in meta["unverified_kept"]}
    for path in kept_values:
        assert rows[path].get("absent", False) is (path not in live_values)
        assert ("absent" in rows[path]) is (path not in live_values)
        assert rows[path]["old"] == live_values.get(path)
    body = _door_keep_mine(client).get_json()
    assert body["status"] == "unverified_kept"
    text = _dialog_result(body)["message"]
    assert "new_in_run  not set -> 1" in text
    assert "null_value  null -> 1" in text
    assert "present_value  2 -> 1" in text
    assert (folder / "state.json").read_bytes() == before


def test_every_preflight_value_uses_the_exact_json_helper():
    from quam_state_manager.web import routes
    import inspect
    tree = ast.parse(inspect.getsource(routes._unverified_kept_rows))
    calls = [node.func for node in ast.walk(tree) if isinstance(node, ast.Call)]
    assert sum(isinstance(fn, ast.Name) and fn.id == "_unverified_kept_value" for fn in calls) == 3
    assert not any(isinstance(fn, ast.Attribute) and fn.attr == "dumps" for fn in calls)
