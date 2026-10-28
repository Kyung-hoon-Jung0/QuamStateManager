"""v1.5.1 U P1-6: after a refused live write the edits stay ROWS, the
read-only pill follows the folder, and the notice says "Apply failed".

U24 (deny ACL, then restored): the refused Apply turned the tray's "Apply 1"
into a whole saved version; after one lab write the panel said the two
"cannot be told apart field by field" and Pull & apply was gone. The
read-only pill stayed through reloads until the next successful write, and
the notice read "Sync failed: Apply to live failed: ... [Errno 13] ...
state.json.tmp".

Pinned over the real routes (both Apply doors), and the notice on the real
response under jsdom (tests/v151_refused_write_rows_selfcheck.cjs).
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import sys
import subprocess
import time
from pathlib import Path

import pytest

from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

ROOT = Path(__file__).resolve().parent.parent
_WIRING = {"network": {"host": "127.0.0.1", "cluster_name": "C1"}}
HX = {"HX-Request": "true"}


def _state(off=0.08, f01=5.0e9):
    return {"qubits": {"qA1": {"id": "qA1", "f_01": f01, "z": {"joint_offset": off}}},
            "qubit_pairs": {}, "active_qubit_names": ["qA1"]}


def _set_readonly(folder: Path, on: bool):
    for name in ("state.json", "wiring.json"):
        f = folder / name
        if f.exists():
            os.chmod(f, stat.S_IREAD if on else (stat.S_IREAD | stat.S_IWRITE))
    _set_dir_readonly(folder, on)


def _set_dir_readonly(folder: Path, on: bool) -> None:
    """On POSIX a read-only FILE is still replaced by rename; the refusal is
    real only when the containing directory denies new entries too. Windows
    already refuses ReplaceFileW on a read-only file, and its directory
    attribute is not enforced, so the directory is left alone there."""
    if sys.platform != "win32" and folder.is_dir():
        os.chmod(folder, 0o555 if on else 0o755)



@pytest.fixture
def env(tmp_path):
    live = tmp_path / "chips" / "live"
    live.mkdir(parents=True)
    (live / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    e = {"app": app, "c": c, "live": live, "tmp": tmp_path}
    yield e
    _set_readonly(live, False)


def _ctx(env):
    return env["app"].config["contexts"][env["app"].config["active_context"]]


def _edit(env, value="0.09"):
    r = env["c"].post("/field/edit", data={"dot_path": "qubits.qA1.z.joint_offset", "value": value})
    assert r.status_code == 200 and r.get_json()["ok"]


def _rows(env):
    return [c.dot_path for c in _ctx(env)["store"].change_log]


def _lab_writes_f01(env, f01):
    _set_readonly(env["live"], False)
    d = json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))
    d["qubits"]["qA1"]["f_01"] = f01
    (env["live"] / "state.json").write_text(json.dumps(d), encoding="utf-8")
    t = time.time() + 5
    os.utime(env["live"] / "state.json", (t, t))


def test_a_refused_apply_keeps_rows_and_pull_and_apply_still_merges(env):
    _edit(env)
    _set_readonly(env["live"], True)
    r = env["c"].post("/state/sync", data={"mode": "apply", "check_collisions": "1"})
    assert r.status_code == 409, r.data[:300]
    d = r.get_json()
    assert d["error_code"] == "live_write_failed"
    assert d["message"].startswith("Apply failed:"), d["message"]
    assert "Errno" not in d["message"] and d.get("detail_exc"), d
    assert _rows(env) == ["qubits.qA1.z.joint_offset"], "the edit is still a row"
    assert not _ctx(env).get("working_dirty"), "not a whole saved version"
    # the folder is fixed and the lab writes another field: Pull & apply merges
    _lab_writes_f01(env, 5.1e9)
    panel = env["c"].get("/state/review").get_data(as_text=True)
    assert 'id="sp-merge"' in panel and "cannot be told apart" not in panel
    r = env["c"].post("/state/sync", data={"mode": "apply"})
    assert r.status_code == 200 and r.get_json()["status"] == "ok", r.data[:300]
    live = json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))["qubits"]["qA1"]
    assert live["f_01"] == 5.1e9 and live["z"]["joint_offset"] == 0.09, live


def test_the_apply_button_door_keeps_rows_and_names_the_details(env):
    _edit(env)
    _set_readonly(env["live"], True)
    r = env["c"].post("/state/apply-to-live", headers=HX)
    assert r.status_code == 409
    html = r.get_data(as_text=True)
    assert "Apply failed:" in html and "Details:" in html, html[-1500:]
    assert _rows(env) == ["qubits.qA1.z.joint_offset"] and not _ctx(env).get("working_dirty")


def test_the_read_only_pill_follows_the_folder(env, monkeypatch):
    _edit(env)
    _set_readonly(env["live"], True)
    assert env["c"].post("/state/apply-to-live", headers=HX).status_code == 409
    assert _ctx(env).get("live_readonly_hint")
    sig_up = env["c"].get("/state/drift").get_json()["sync"]["sig"]
    assert "tray-ro-lock" in env["c"].get("/state/tray", headers=HX).get_data(as_text=True)
    _set_readonly(env["live"], False)                     # fixed outside SM
    monkeypatch.setattr(routes, "_READONLY_REPROBE_S", 0.0)
    sig_down = env["c"].get("/state/drift").get_json()["sync"]["sig"]
    assert not _ctx(env).get("live_readonly_hint"), "the drift poll re-probed and dropped it"
    assert sig_down != sig_up, "the page repaints its tray (the control's signature moved)"
    assert "tray-ro-lock" not in env["c"].get("/state/tray", headers=HX).get_data(as_text=True)


def test_the_notice_on_the_real_response(env):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    _edit(env)
    tray = env["c"].get("/state/tray", headers=HX).get_data(as_text=True)
    _set_readonly(env["live"], True)
    r = env["c"].post("/state/sync", data={"mode": "apply", "check_collisions": "1"})
    assert r.status_code == 409
    fx = env["tmp"] / "fixture.json"
    fx.write_text(json.dumps({"tray_html": tray, "answer": r.get_json()}), encoding="utf-8")
    p = subprocess.run([node, str(ROOT / "tests" / "v151_refused_write_rows_selfcheck.cjs"), str(fx)],
                       capture_output=True, text=True, encoding="utf-8", timeout=180, cwd=str(ROOT))
    if p.returncode == 2:
        pytest.skip("jsdom not installed")
    assert p.returncode == 0 and "DONE" in p.stdout, p.stdout[-3000:] + p.stderr[-3000:]
