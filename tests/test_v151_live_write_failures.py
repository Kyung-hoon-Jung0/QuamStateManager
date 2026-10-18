"""v1.5.1 SA-F2: a live write that fails is a named 409, never a 500.

Every manual live-write door -- Apply (both routes), Keep mine, Pull & apply,
the auto-apply flush, dataset "Apply to chip" and restore-live -- answered
HTTP 500 when the live write (or the backup it needs) failed: a deny ACL, a
read-only attribute, a file held open. The message was honest, nothing was
written and the ledger recorded the failure, but the page treated it as a
crash: a 4.5 s toast, and the read-only pill only if the denial already
existed when the chip was opened.

Pinned: each door answers 409 with ``live_write_failed`` / ``backup_failed``
(``X-SM-Error-Code`` or the JSON ``error_code``), the same honest message,
the tray carrying the read-only pill when the write discovered a denial (and
not for a file merely held open), the edits kept, and the ledger's failed
record kept.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
from pathlib import Path

import pytest

from quam_state_manager.core import hub, safe_io
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

_WIRING = {"network": {"host": "127.0.0.1", "cluster_name": "C1"}}
JSON = {"Accept": "application/json"}


def _state(off=0.08):
    return {"qubits": {"qA1": {"id": "qA1", "f_01": 5.0e9, "z": {"joint_offset": off}}},
            "qubit_pairs": {}, "active_qubit_names": ["qA1"]}


def _write_chip(folder: Path, state: dict):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")


def _set_readonly(folder: Path, on: bool):
    for name in ("state.json", "wiring.json"):
        f = folder / name
        if f.exists():
            os.chmod(f, stat.S_IREAD if on else (stat.S_IREAD | stat.S_IWRITE))


@pytest.fixture
def env(tmp_path):
    live = tmp_path / "chips" / "live"
    _write_chip(live, _state())
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    e = {"app": app, "client": c, "live": live, "tmp": tmp_path}
    yield e
    _set_readonly(live, False)


def _ctx(env):
    return env["app"].config["contexts"][env["app"].config["active_context"]]


def _edit(env, value="0.09"):
    r = env["client"].post("/field/edit", data={"dot_path": "qubits.qA1.z.joint_offset",
                                                "value": value})
    assert r.status_code == 200 and r.get_json()["ok"]


def _live_off(env):
    return json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))["qubits"]["qA1"]["z"]["joint_offset"]


def _deny(env):
    """The live files turn read-only AFTER the chip was opened: the open-time
    probe has not seen it, so only the write can discover it."""
    _set_readonly(env["live"], True)
    assert not _ctx(env).get("live_readonly_hint")


def _failed_events(env):
    with env["app"].app_context():
        chip = routes._hub_chip_dir(_ctx(env)["path"])
    return [e for e in hub.Hub.for_chip(chip).events() if e.get("outcome") == "failed"]


def _kept(env):
    ctx = _ctx(env)
    return bool(ctx["store"].change_log) or bool(ctx.get("working_dirty"))


# --------------------------------------------------------------------------
# the doors
# --------------------------------------------------------------------------

def test_apply_through_sync_is_a_named_409_with_the_pill(env):
    _edit(env)
    _deny(env)
    r = env["client"].post("/state/sync", data={"mode": "apply", "check_collisions": "1"})
    assert r.status_code == 409, (r.status_code, r.data[:400])
    d = r.get_json()
    assert d["status"] == "error" and d["error_code"] == "live_write_failed", d
    assert "refused the write" in d["message"] and "Your edits are kept" in d["message"]
    assert "tray-ro-lock" in d["tray_html"], "the denial found at write time raises the pill"
    assert _live_off(env) == 0.08 and _kept(env)
    assert _failed_events(env), "the ledger keeps the failed write"


def test_apply_to_live_is_a_named_409_with_tray_and_persistent_error(env):
    _edit(env)
    _deny(env)
    r = env["client"].post("/state/apply-to-live")
    assert r.status_code == 409, (r.status_code, r.data[:400])
    assert r.headers.get("X-SM-Error-Code") == "live_write_failed"
    html = r.get_data(as_text=True)
    assert 'id="pending-tray"' in html and "tray-ro-lock" in html
    assert 'id="status-bar" hx-swap-oob="innerHTML"' in html and "toast-error" in html
    assert "refused the write" in html
    assert _live_off(env) == 0.08 and _kept(env)
    assert _failed_events(env)
    # JSON callers get the code too
    r = env["client"].post("/state/apply-to-live", headers=JSON)
    assert r.status_code == 409 and r.get_json()["error"] == "live_write_failed"


def test_keep_mine_is_a_named_409(env):
    _edit(env)
    _deny(env)
    r = env["client"].post("/state/apply-to-live?force=1")
    assert r.status_code == 409, (r.status_code, r.data[:400])
    assert r.headers.get("X-SM-Error-Code") == "live_write_failed"
    assert _live_off(env) == 0.08 and _kept(env)


def test_the_auto_apply_flush_is_a_named_409_and_disarms(env):
    _edit(env)
    _ctx(env)["auto_apply"] = routes._new_auto_session(pull=False, pull_replace=False, push=True)
    _deny(env)
    r = env["client"].post("/state/apply-to-live")
    assert r.status_code == 409, (r.status_code, r.data[:400])
    assert r.headers.get("X-SM-Error-Code") == "live_write_failed"
    assert "autoApplyDisarm" in r.headers.get("HX-Trigger", "")
    assert "auto_apply" not in _ctx(env)
    assert 'data-auto-apply="1"' not in r.get_data(as_text=True)
    assert _kept(env)


def test_pull_and_apply_after_live_drift_is_a_named_409(env):
    _edit(env)
    other = _state()
    other["qubits"]["qA1"]["f_01"] = 5.1e9                    # an outside write, another field
    _write_chip(env["live"], other)
    _deny(env)
    r = env["client"].post("/state/sync", data={"mode": "apply"})
    assert r.status_code == 409, (r.status_code, r.data[:400])
    assert r.get_json()["error_code"] == "live_write_failed"


def test_a_held_file_is_named_and_raises_no_read_only_pill(env, monkeypatch):
    _edit(env)

    def held(*_a, **_k):
        raise OSError(13, "The process cannot access the file", "state.json", 32)

    monkeypatch.setattr(safe_io, "write_state_wiring_bytes", held)
    r = env["client"].post("/state/sync", data={"mode": "apply"})
    assert r.status_code == 409, (r.status_code, r.data[:400])
    d = r.get_json()
    assert d["error_code"] == "live_write_failed"
    assert "holds a live file open" in d["message"]
    assert "tray-ro-lock" not in d["tray_html"]
    assert not _ctx(env).get("live_readonly_hint")


def test_a_landed_write_clears_a_pill_a_failure_raised(env):
    _edit(env)
    _deny(env)
    assert env["client"].post("/state/apply-to-live").status_code == 409
    assert _ctx(env).get("live_readonly_hint")
    _set_readonly(env["live"], False)
    r = env["client"].post("/state/apply-to-live")
    assert r.status_code == 200, r.data[:400]
    assert _live_off(env) == 0.09
    assert not _ctx(env).get("live_readonly_hint")


def test_restore_live_write_failure_is_a_named_409(env):
    hm = env["app"].config["history_manager"]
    hm.check_and_snapshot(_ctx(env)["path"], "manual", force=True)
    ts = hm.list_snapshots(_ctx(env)["path"])[0].timestamp
    _write_chip(env["live"], _state(off=0.5))
    _deny(env)
    r = env["client"].post(f"/state-history/{ts}/restore-live?force=1")
    assert r.status_code == 409, (r.status_code, r.data[:400])
    assert r.headers.get("X-SM-Error-Code") == "live_write_failed"
    html = r.get_data(as_text=True)
    assert "Restore to live failed" in html and "tray-ro-lock" in html
    assert _live_off(env) == 0.5


def test_restore_live_without_a_backup_is_a_named_409(env):
    hm = env["app"].config["history_manager"]
    hm.check_and_snapshot(_ctx(env)["path"], "manual", force=True)
    ts = hm.list_snapshots(_ctx(env)["path"])[0].timestamp
    shutil.rmtree(env["live"])                    # nothing to back up -> no backup
    r = env["client"].post(f"/state-history/{ts}/restore-live?force=1")
    assert r.status_code == 409, (r.status_code, r.data[:400])
    assert r.headers.get("X-SM-Error-Code") == "backup_failed"
    assert b"Could not snapshot the current state" in r.data


def _seed_run(root: Path, run_id: int, state: dict) -> None:
    run = root / "2026-12-30" / f"#{run_id}_08_spec_010000"
    run.mkdir(parents=True)
    (run / "node.json").write_text(json.dumps({
        "metadata": {"name": "08_spec", "status": "successful",
                     "run_start": "2026-12-30T01:00:00", "run_end": "2026-12-30T01:00:01"},
        "data": {"parameters": {"model": {"qubits": ["qA1"]}}, "outcomes": {}},
        "id": run_id, "parents": [], "created_at": "2026-12-30T01:00:00",
    }), encoding="utf-8")
    (run / "data.json").write_text("{}", encoding="utf-8")
    _write_chip(run / "quam_state", state)


def test_dataset_apply_to_chip_write_failure_is_a_named_409(env):
    root = env["tmp"] / "data"
    _seed_run(root, 31, _state(off=0.079))
    env["client"].post("/workspace/add", data={"folder": str(root)})
    uid = f"{routes._folder_key(root)}:31"
    _deny(env)
    r = env["client"].post(f"/dataset/{uid}/load-state?apply=1")
    assert r.status_code == 409, (r.status_code, r.data[:400])
    assert r.headers.get("X-SM-Error-Code") == "live_write_failed"
    html = r.get_data(as_text=True)
    assert "IS staged in the working copy" in html and "tray-ro-lock" in html
    assert _live_off(env) == 0.08


# --------------------------------------------------------------------------
# the page side
# --------------------------------------------------------------------------

def test_the_page_keeps_the_failure_on_screen_selfcheck():
    import shutil as _sh
    import subprocess
    node = _sh.which("node")
    if node is None:
        pytest.skip("node not available")
    root = Path(__file__).resolve().parents[1]
    try:
        subprocess.run([node, "-e", "require('jsdom')"], check=True, capture_output=True,
                       timeout=30, cwd=str(root))
    except Exception:
        pytest.skip("jsdom not installed")
    r = subprocess.run([node, str(root / "tests" / "v151_live_write_failure_selfcheck.cjs")],
                       capture_output=True, text=True, encoding="utf-8", timeout=120, cwd=str(root))
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.count("ok - ") >= 9, r.stdout
