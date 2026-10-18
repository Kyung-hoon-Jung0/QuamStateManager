"""v1.5.1 SA-F5 + SA-F6: a node run never writes a dataset run's frozen record.

With a run opened as an archive and "Use open chip" as the target, the
Experiment Runner's pre-flight passed the path and identity checks, and a
forced start ran a node against the archive folder (its state.json rewritten,
SM adopting it). Auto Calibrate's start accepted an archive the same way, and
its writer (apply_rows, revert_patches, restore_values) wrote the archive's
files.

Pinned: the pre-flight fails a named "archive" check; /scheduler/start and
/autofit/start refuse an archive -- the open chip, or the run target -- with a
named reason that ``force`` does not pass; the writer refuses a non-live
origin and a run-archive folder, and the archive's files stay byte-identical.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from quam_state_manager.core import scheduler
from quam_state_manager.core.autofit import writer as af_writer
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

_WIRING = {"network": {"host": "127.0.0.1", "cluster_name": "C1"}}


def _state(t1=1.0e-5):
    return {"qubits": {"qA1": {"id": "qA1", "T1": t1, "z": {"joint_offset": 0.08}}},
            "qubit_pairs": {}, "active_qubit_names": ["qA1"]}


def _write_chip(folder: Path, state: dict):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")


def _seed_run(root: Path, run_id: int, state: dict) -> Path:
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
    return run / "quam_state"


@pytest.fixture
def env(tmp_path):
    live = tmp_path / "chips" / "live"
    _write_chip(live, _state())
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    root = tmp_path / "data"
    archive = _seed_run(root, 31, _state(t1=2.0e-5))
    c.post("/workspace/add", data={"folder": str(root)})
    e = {"app": app, "client": c, "live": live, "archive": archive,
         "uid": f"{routes._folder_key(root)}:31",
         "bytes": (archive / "state.json").read_bytes()}
    yield e
    try:
        with app.test_request_context("/"):
            scheduler.cancel(routes._sched_inst())
    except Exception:  # noqa: BLE001 -- best effort, a refused start started nothing
        pass


def _ctx(env):
    return env["app"].config["contexts"][env["app"].config["active_context"]]


def _open_archive(env):
    r = env["client"].post(f"/dataset/{env['uid']}/load-state?mode=archive")
    assert r.status_code in (200, 302), r.data[:300]
    assert _ctx(env).get("origin") == "dataset_archive"


def _inst(env):
    with env["app"].test_request_context("/"):
        return routes._sched_inst()


# --------------------------------------------------------------------------
# Experiment Runner
# --------------------------------------------------------------------------

def test_the_preflight_names_the_archive(env):
    _open_archive(env)
    d = env["client"].post("/scheduler/preflight", json={}).get_json()
    checks = {c["key"]: c for c in d["checks"]}
    assert "archive" in checks and checks["archive"]["status"] == "fail", d["checks"]
    assert d["ok"] is False


def test_a_forced_runner_start_on_an_open_archive_is_refused(env):
    _open_archive(env)
    r = env["client"].post("/scheduler/start",
                           json={"force": True, "quam_state_path": str(env["archive"])})
    assert r.status_code == 409, r.data[:400]
    d = r.get_json()
    assert d["reason"] == "archive" and d["error"] == "archive_target", d
    assert "frozen" in d["message"]
    assert not scheduler.is_active(_inst(env))
    assert (env["archive"] / "state.json").read_bytes() == env["bytes"]


def test_a_forced_runner_start_aimed_at_a_run_folder_is_refused(env):
    # a live chip is open, but the run target is a run's quam_state
    r = env["client"].post("/scheduler/start",
                           json={"force": True, "quam_state_path": str(env["archive"])})
    assert r.status_code == 409, r.data[:400]
    assert r.get_json()["error"] == "archive_target"
    assert not scheduler.is_active(_inst(env))


def test_a_live_chip_preflight_has_no_archive_check(env):
    d = env["client"].post("/scheduler/preflight", json={"quam_state_path": str(env["live"])}).get_json()
    assert "archive" not in {c["key"] for c in d["checks"]}


# --------------------------------------------------------------------------
# Auto Calibrate
# --------------------------------------------------------------------------

def test_a_forced_auto_calibrate_start_on_an_archive_is_refused(env):
    _open_archive(env)
    r = env["client"].post("/autofit/start", json={"preset": "1q_bringup", "backend": "real",
                                                   "force": True})
    assert r.status_code == 409, r.data[:400]
    d = r.get_json()
    assert d.get("error") == "archive_target", d
    assert "frozen" in d.get("message", "")


def _handle(ctx, **kw):
    return af_writer.ChipHandle(ctx["store"], ctx["modifier"], ctx["saver"],
                                ctx["working_copy"], threading.RLock(), ctx["path"], **kw)


@pytest.mark.parametrize("call", ["apply_rows", "revert_patches", "restore_values"])
def test_the_writer_refuses_an_archive(env, call):
    _open_archive(env)
    ctx = _ctx(env)
    for handle in (_handle(ctx), _handle(ctx, origin="dataset_archive")):
        if call == "apply_rows":
            out = af_writer.apply_rows(handle, [{"path": "qubits.qA1.T1", "value": 3.3e-5}],
                                       apply_live=True, label="t")
        elif call == "revert_patches":
            out = af_writer.revert_patches(
                handle, [{"op": "replace", "path": "/qubits/qA1/T1", "value": 2.0e-5, "old": 3.3e-5}],
                apply_live=True, label="t")
        else:
            out = af_writer.restore_values(handle, [{"path": "qubits.qA1.T1", "value": 3.3e-5}],
                                           apply_live=True, label="t")
        assert out.ok is False and "archive" in (out.error or ""), out.as_dict()
    assert (env["archive"] / "state.json").read_bytes() == env["bytes"]
    assert ctx["store"].get_value("qubits.qA1.T1") == 2.0e-5


def test_the_writer_still_writes_a_live_chip(env):
    ctx = _ctx(env)
    with env["app"].test_request_context("/"):
        hub_dir = routes._hub_chip_dir(ctx["path"])
    out = af_writer.apply_rows(_handle(ctx, hub_dir=hub_dir), [{"path": "qubits.qA1.T1", "value": 3.3e-5}],
                               apply_live=True, label="t")
    assert out.ok is True, out.as_dict()
    assert json.loads((env["live"] / "state.json").read_text())["qubits"]["qA1"]["T1"] == 3.3e-5


def test_the_pages_say_the_archive_refusal_selfcheck():
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
    r = subprocess.run([node, str(root / "tests" / "v151_archive_run_refusal_selfcheck.cjs")],
                       capture_output=True, text=True, encoding="utf-8", timeout=120, cwd=str(root))
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.count("ok - ") >= 5, r.stdout
