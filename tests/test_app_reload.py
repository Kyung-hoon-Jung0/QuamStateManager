"""docs/237: the top bar's Reload (customers 2026-10-01).

POST /app/reload mode=state re-reads the open chip through the shared rebuild
entrypoint and re-checks the live pair; mode=both also re-inspects the
selected Python env. Unapplied edits live only in memory and are NEVER
dropped: with edits pending the files are not re-read.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

ROOT = Path(__file__).resolve().parent.parent


def _chip(folder: Path, t1: float = 1e-5) -> Path:
    folder.mkdir(parents=True)
    state = {"qubits": {"q1": {"id": "q1", "T1": t1, "f_01": 5e9}}}
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps({"wiring": {}}), encoding="utf-8")
    return folder


@pytest.fixture
def app_chip(tmp_path):
    app = create_app(testing=True, instance_path=str(tmp_path / "inst"))
    c = app.test_client()
    chip = _chip(tmp_path / "chip")
    assert c.post("/load", data={"folder": str(chip)}).status_code in (200, 302)
    return app, c, chip


def _ctx(app):
    with app.test_request_context("/"):
        return routes._active_ctx()


def _t1(app):
    return _ctx(app)["store"].state["qubits"]["q1"]["T1"]


def test_state_reload_rereads_the_working_files(app_chip):
    app, c, _ = app_chip
    ctx = _ctx(app)
    wf = Path(ctx["working_copy"].working_folder) / "state.json"
    doc = json.loads(wf.read_text(encoding="utf-8"))
    doc["qubits"]["q1"]["T1"] = 2e-5               # the working file moved under SM
    wf.write_text(json.dumps(doc), encoding="utf-8")
    d = c.post("/app/reload", data={"mode": "state"}).get_json()
    assert d["ok"] and d["mode"] == "state" and d["chip"] and d["reread"] and d["kept_edits"] == 0
    assert _t1(app) == 2e-5, "the store now holds what the working file says"


def test_pending_edits_are_kept_not_reread(app_chip):
    app, c, _ = app_chip
    r = c.post("/field/edit", data={"dot_path": "qubits.q1.T1", "value": "3e-05"})
    assert r.status_code == 200
    d = c.post("/app/reload", data={"mode": "state"}).get_json()
    assert d["ok"] and d["kept_edits"] == 1 and not d["reread"]
    assert _t1(app) == 3e-5, "the unapplied edit survived the reload"
    assert len(_ctx(app)["modifier"].get_change_log()) == 1


def test_saved_not_applied_and_staged_flags_survive(app_chip):
    app, c, _ = app_chip
    ctx = _ctx(app)
    ctx["working_dirty"] = True
    ctx["staged_base"] = True
    d = c.post("/app/reload", data={"mode": "state"}).get_json()
    assert d["reread"]
    ctx = _ctx(app)
    assert ctx.get("working_dirty") is True and ctx.get("staged_base") is True, \
        "a reload describes no change to the files -- the working-state flags stay"


def test_both_reinspects_the_selected_env(app_chip, monkeypatch):
    app, c, _ = app_chip
    from quam_state_manager.core import config_generator, lab_waveform
    calls = {"probe": [], "retire": [], "prewarm": 0}
    monkeypatch.setattr(config_generator, "get_selected_env", lambda inst: r"C:\envs\x\python.exe")
    monkeypatch.setattr(config_generator, "probe_capabilities",
                        lambda py, inst, force=False, **k: calls["probe"].append((py, force)) or {})
    monkeypatch.setattr(lab_waveform, "retire_except",
                        lambda py, background=False: calls["retire"].append(py) or 0)
    monkeypatch.setattr(routes, "_maybe_prewarm_lab_worker",
                        lambda ctx, inst, reason="": calls.__setitem__("prewarm", calls["prewarm"] + 1))
    monkeypatch.setattr(routes, "_warm_state_schema_async", lambda *a, **k: None)
    d = c.post("/app/reload", data={"mode": "both"}).get_json()
    assert d["ok"] and d["mode"] == "both" and d["env"] == r"C:\envs\x\python.exe"
    import time
    for _ in range(50):
        if calls["probe"]:
            break
        time.sleep(0.05)
    assert calls["probe"] == [(r"C:\envs\x\python.exe", True)], "the capability probe is FORCED"
    assert calls["retire"] == [None], "every lab worker is retired"
    assert calls["prewarm"] == 1, "and the open chip's is started again"


def test_state_mode_leaves_the_env_alone(app_chip, monkeypatch):
    app, c, _ = app_chip
    from quam_state_manager.core import config_generator, lab_waveform
    seen = []
    # an env IS selected -- State only must still leave it alone
    monkeypatch.setattr(config_generator, "get_selected_env", lambda inst: "C:/envs/x/python.exe")
    monkeypatch.setattr(config_generator, "probe_capabilities", lambda *a, **k: seen.append("probe") or {})
    monkeypatch.setattr(lab_waveform, "retire_except", lambda py, background=False: seen.append(py) or 0)
    c.post("/app/reload", data={"mode": "state"})
    assert seen == []


def test_no_chip_is_fine(tmp_path):
    c = create_app(testing=True, instance_path=str(tmp_path / "inst")).test_client()
    d = c.post("/app/reload", data={"mode": "state"}).get_json()
    assert d["ok"] and d["chip"] is False


def test_the_button_is_in_the_top_bar(app_chip):
    _, c, _ = app_chip
    html = c.get("/qubits").get_data(as_text=True)
    assert 'id="topbar-reload"' in html
    assert '<span class="rl-long"> Reload</span><span class="rl-short"> Re</span>' in html


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_reload_selfcheck():
    r = subprocess.run(["node", str(ROOT / "tests" / "sm_reload_selfcheck.cjs")], capture_output=True,
                       text=True, encoding="utf-8", cwd=str(ROOT), timeout=180)
    if "Cannot find module 'jsdom'" in (r.stderr or ""):
        pytest.skip("jsdom not installed")
    out = r.stdout + r.stderr
    assert r.returncode == 0 and "FAIL" not in out, out[-3000:]
