"""v1.5.1 R3 MINOR 9: a damaged Runner settings file is named and can be reset.

SB-P2-7 refused to save over (or run on) a settings file that could not be
read -- right for a moment's sharing violation, a dead end for a file whose
content is damaged: every save and Start said "Retry in a moment", the queue
paused with "press Start to retry", and the message named only the bare
file name. A damaged file is now named in full and the refusal offers a
reset that sets it aside (renamed beside it, never deleted); a file that is
only held for a moment still says retry and is never moved.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.core import safe_io, scheduler
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

ROOT = Path(__file__).resolve().parent.parent
_WIRING = {"network": {"host": "127.0.0.1", "cluster_name": "C1"}}
_TORN = '{"dry_run": false, '


def _scope(tmp_path) -> Path:
    scope = tmp_path / "inst" / "scheduler" / "chip-abc"
    scope.mkdir(parents=True)
    return scope


@pytest.mark.parametrize("shared", [False, True])
@pytest.mark.parametrize("content", [_TORN, "[]"])
def test_a_damaged_file_is_named_and_set_aside_by_the_reset(tmp_path, shared, content):
    scope = _scope(tmp_path)
    f = scheduler.shared_settings_path(scope) if shared else scheduler.settings_path(scope)
    f.write_text(content, encoding="utf-8")
    with pytest.raises(scheduler.SettingsUnreadable) as ei:
        scheduler.save_settings(scope, {"default_timeout_s": 600})
    assert getattr(ei.value, "damaged", False) is True and ei.value.path == str(f)
    assert str(f) in str(ei.value) and "damaged" in str(ei.value)
    moved = scheduler.reset_damaged_settings(scope)
    assert len(moved) == 1 and not f.exists()
    aside = Path(moved[0])
    assert aside.parent == f.parent and ".damaged-" in aside.name
    assert aside.read_text(encoding="utf-8") == content, "the damaged file is kept as it was"
    assert scheduler.save_settings(scope, {"default_timeout_s": 600})["default_timeout_s"] == 600


def test_a_file_only_held_for_a_moment_says_retry_and_is_never_moved(tmp_path, monkeypatch):
    scope = _scope(tmp_path)
    f = scope / scheduler._SETTINGS_FILENAME
    f.write_text(json.dumps({"default_timeout_s": 300}), encoding="utf-8")
    real = safe_io.read_json

    def held(path, *a, **k):
        if Path(path) == f:
            raise safe_io.LiveFileError(f"Could not read {path} after 3 attempts: sharing violation")
        return real(path, *a, **k)
    monkeypatch.setattr(safe_io, "read_json", held)
    with pytest.raises(scheduler.SettingsUnreadable) as ei:
        scheduler.save_settings(scope, {"default_timeout_s": 600})
    assert getattr(ei.value, "damaged", None) is False
    assert scheduler.reset_damaged_settings(scope) == [] and f.exists()


def test_the_routes_offer_the_reset_and_the_runner_js_takes_it(tmp_path):
    live = tmp_path / "chip" / "quam_state"
    live.mkdir(parents=True)
    (live / "state.json").write_text(json.dumps(
        {"qubits": {"q1": {"id": "q1", "T1": 1e-5}}, "qubit_pairs": {},
         "active_qubit_names": ["q1"]}), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    with app.test_request_context():
        scope = Path(routes._sched_inst())
    scope.mkdir(parents=True, exist_ok=True)
    f = scope / scheduler._SETTINGS_FILENAME
    f.write_text(_TORN, encoding="utf-8")
    r = c.post("/scheduler/settings", data={"default_timeout_s": "600"})
    body = r.get_json()
    assert r.status_code == 409 and body.get("damaged") is True, body
    assert body["file"] == str(f) and str(f) in body["error"], body
    assert "Retry in a moment" not in body["error"] and "Reset" in body["error"], body["error"]
    r = c.post(body["reset_url"])
    assert r.status_code == 200 and r.get_json()["ok"] and len(r.get_json()["set_aside"]) == 1
    assert c.post("/scheduler/settings", data={"default_timeout_s": "600"}).get_json()["ok"]

    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    probe = (
        "'use strict';"
        "let JSDOM; try { ({ JSDOM } = require('jsdom')); } catch (e) { console.log('SKIP'); process.exit(2); }"
        "const fs = require('fs');"
        "const body = JSON.parse(process.argv[1]);"
        "const w = new JSDOM('<!doctype html><body></body>', { runScripts: 'outside-only' }).window;"
        "const posts = []; let asked = '';"
        "w.fetch = (url, o) => { posts.push(String(url));"
        "  return Promise.resolve({ json: () => Promise.resolve({ ok: true, message: 'Set aside' }) }); };"
        "w.confirm = (m) => { asked = m; return true; };"
        "w.showToast = () => {};"
        "w.eval(fs.readFileSync(process.argv[2], 'utf8'));"
        "const took = w._schedulerSettingsDamaged(body);"
        "setTimeout(() => {"
        "  const ok = took && /Reset/.test(asked) && posts[0] === body.reset_url;"
        "  console.log(ok ? 'DONE' : ('FAIL ' + JSON.stringify({ took, asked, posts })));"
        "  process.exit(ok ? 0 : 1); }, 50);")
    pr = subprocess.run([node, "-e", probe, json.dumps(body),
                         str(ROOT / "quam_state_manager" / "web" / "static" / "scheduler.js")],
                        capture_output=True, text=True, encoding="utf-8", timeout=120, cwd=str(ROOT))
    if pr.returncode == 2:
        pytest.skip("jsdom not installed")
    assert pr.returncode == 0 and "DONE" in pr.stdout, pr.stdout + pr.stderr
