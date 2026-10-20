"""Strict settings reads bridge absence; polls keep their immediate answer."""
import threading
import time

import pytest

from quam_state_manager.core import safe_io, scheduler


def test_worker_bridges_a_real_rename_gap(tmp_path, monkeypatch):
    scope = tmp_path / "scope"
    scheduler.save_settings(scope, {"env_python": "/py", "global_simulate": False})
    scheduler.add_item(scope, {"name": "node", "description": "", "parameters": {},
                               "path": "/n/node.py"})
    target = scheduler.settings_path(scope)
    aside = target.with_suffix(".moving")
    ran = []
    monkeypatch.setattr(scheduler, "_run_item", lambda inst, item, settings, runner: (
        ran.append(settings["global_simulate"]) or
        {"status": "done", "error": None, "returncode": 0, "log_file": "stub"}))
    target.rename(aside)
    timer = threading.Timer(.25, lambda: aside.rename(target))
    timer.start()
    try:
        scheduler.start(scope)
        end = time.monotonic() + 10
        while scheduler.is_running(scope) and time.monotonic() < end:
            time.sleep(.01)
    finally:
        timer.join()
    assert not scheduler.is_running(scope)
    assert ran == [False], "worker substituted operational defaults during the rename gap"


def test_configured_absence_refuses_reads_and_saves(tmp_path, monkeypatch):
    scope = tmp_path / "scope"
    scheduler.save_settings(scope, {"global_simulate": False})
    target = scheduler.settings_path(scope)
    target.unlink()
    monkeypatch.setattr(safe_io, "_READ_BACKOFF_S", 0)
    for operation in (lambda: scheduler.load_settings(scope, strict=True),
                      lambda: scheduler.save_settings(scope, {"failure_policy": "continue"})):
        with pytest.raises(scheduler.SettingsUnreadable) as caught:
            operation()
        assert str(target) in str(caught.value)
    assert not target.exists()


def test_shared_record_detects_missing_scope_after_process_restart(tmp_path, monkeypatch):
    scope = tmp_path / "scope"
    scheduler.save_settings(scope, {"global_simulate": False})
    target = scheduler.settings_path(scope)
    target.unlink()
    if hasattr(scheduler, "_SETTINGS_SEEN"):
        monkeypatch.setattr(scheduler, "_SETTINGS_SEEN", set())
    monkeypatch.setattr(safe_io, "_READ_BACKOFF_S", 0)
    error = None
    try:
        scheduler.load_settings(scope, strict=True)
    except scheduler.SettingsUnreadable as exc:
        error = exc
    assert error is not None, "shared record must protect a configured scope after restart"


def test_strict_never_configured_scope_uses_defaults_after_protected_read(tmp_path, monkeypatch):
    calls = []
    real = safe_io.read_json
    monkeypatch.setattr(safe_io, "_READ_BACKOFF_S", 0)
    monkeypatch.setattr(safe_io, "read_json", lambda p, **kw: (calls.append(p), real(p, **kw))[1])
    assert scheduler.load_settings(tmp_path, strict=True)["global_simulate"] is True
    assert scheduler.settings_path(tmp_path) in calls


def test_worker_pauses_when_configured_settings_stay_missing(tmp_path, monkeypatch):
    scheduler.save_settings(tmp_path, {"env_python": "/py", "global_simulate": False})
    scheduler.add_item(tmp_path, {"name": "node", "parameters": {}, "path": "/n/node.py"})
    scheduler.settings_path(tmp_path).unlink()
    monkeypatch.setattr(safe_io, "_READ_BACKOFF_S", 0)
    ran = []
    monkeypatch.setattr(scheduler, "_run_item", lambda *a: (ran.append(a) or {"status": "done", "error": None, "returncode": 0, "log_file": "stub"}))
    scheduler.start(tmp_path)
    end = time.monotonic() + 5
    while scheduler.is_running(tmp_path) and time.monotonic() < end:
        time.sleep(.01)
    run = scheduler.load_queue(tmp_path)["run"]
    assert ran == []
    assert run["status"] == "paused" and "scheduler.json" in run["message"]
