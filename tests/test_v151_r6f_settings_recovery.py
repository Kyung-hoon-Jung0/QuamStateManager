"""A missing configured settings file requires explicit recovery."""
import json
import subprocess
from pathlib import Path
import pytest
from quam_state_manager.core import safe_io, scheduler
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app


def missing(scope, monkeypatch):
    scheduler.save_settings(scope, {"global_simulate": False})
    scheduler.settings_path(scope).unlink()
    monkeypatch.setattr(safe_io, "_READ_BACKOFF_S", 0)
    monkeypatch.setattr(scheduler, "_SETTINGS_SEEN", set())


def test_save_recovers_own_missing_file(tmp_path, monkeypatch):
    missing(tmp_path, monkeypatch)
    with pytest.raises(scheduler.SettingsUnreadable):
        scheduler.load_settings(tmp_path, strict=True)
    try:
        saved = scheduler.save_settings(tmp_path, {"global_simulate": False})
    except scheduler.SettingsUnreadable as exc:
        saved = exc
    assert isinstance(saved, dict), str(saved)
    assert saved["global_simulate"] is False
    assert scheduler.load_settings(tmp_path, strict=True)["global_simulate"] is False


def test_reset_recovers_missing_scope_and_persists_it(tmp_path, monkeypatch):
    missing(tmp_path, monkeypatch)
    scheduler.reset_damaged_settings(tmp_path)
    shared = json.loads(scheduler.shared_settings_path(tmp_path).read_text())
    assert tmp_path.name not in shared[scheduler._CONFIGURED_SCOPES]
    assert scheduler._settings_key(scheduler.settings_path(tmp_path)) in scheduler._SETTINGS_RESET
    assert scheduler.load_settings(tmp_path, strict=True) == scheduler._DEFAULTS
    monkeypatch.setattr(scheduler, "_SETTINGS_RESET", set())
    assert scheduler.load_settings(tmp_path, strict=True) == scheduler._DEFAULTS


def test_missing_message_offers_reset_or_save(tmp_path, monkeypatch):
    missing(tmp_path, monkeypatch)
    with pytest.raises(scheduler.SettingsUnreadable) as caught:
        scheduler.load_settings(tmp_path, strict=True)
    body = routes._settings_unreadable_body("Nothing ran", caught.value)
    assert body.get("error_kind") == "missing"
    assert body.get("reset_url") == "/scheduler/settings/reset"
    assert "Reset" in body["error"] and "Save settings" in body["error"]
    assert "could not be read" not in body["error"] and "Retry" not in body["error"]


def test_missing_reset_route_and_client(tmp_path, monkeypatch):
    app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
    with app.test_request_context():
        scope = Path(routes._sched_inst())
    missing(scope, monkeypatch)
    with pytest.raises(scheduler.SettingsUnreadable) as caught:
        scheduler.load_settings(scope, strict=True)
    body = routes._settings_unreadable_body("Nothing started", caught.value)
    probe = """
const {JSDOM} = require('jsdom'), fs = require('fs');
const w = new JSDOM('<body></body>', {runScripts: 'outside-only'}).window;
let posted = '', prompt = '';
w.confirm = m => { prompt = m; return true; };
w.fetch = u => { posted = u; return Promise.resolve({json: () => Promise.resolve({ok:true})}); };
w.eval(fs.readFileSync(process.argv[1], 'utf8'));
const took = w._schedulerSettingsDamaged(JSON.parse(process.argv[2]));
setTimeout(() => process.exit(took && /Reset/.test(prompt) && posted === '/scheduler/settings/reset' ? 0 : 1), 20);
"""
    js = Path(__file__).resolve().parents[1] / "quam_state_manager/web/static/scheduler.js"
    result = subprocess.run(["node", "-e", probe, str(js), json.dumps(body)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    answer = app.test_client().post("/scheduler/settings/reset").get_json()
    assert answer["ok"] and "Missing settings reset" in answer["message"]
    assert answer["set_aside"] == []
    assert scheduler.load_settings(scope, strict=True) == scheduler._DEFAULTS
