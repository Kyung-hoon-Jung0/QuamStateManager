"""v1.5.1 SB-P2-7: a Runner settings read that hits a sharing violation.

A second SM process's settings read failed with PermissionError (the other
process held scheduler.json) and read as the defaults -- "Dry run" on -- so
its queued item ran as a dry run, silently. Pinned:
  - a transient sharing violation is retried (share-delete read with the
    retry ladder) and the stored settings are what the run reads;
  - a file that stays unreadable pauses the queue with a named message and
    runs nothing -- never a run on defaults;
  - a save over an unreadable file refuses instead of writing defaults over
    what is stored, and the settings route says so (409, no 500).
"""
from __future__ import annotations

import pathlib
import time

import pytest

from quam_state_manager.core import safe_io, scheduler


def _info(name):
    return {"name": name, "description": "", "parameters": {}, "path": f"/n/{name}.py"}


def _wait(cond, timeout=5.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def _held(monkeypatch, times: int):
    """scheduler.json held open by another process for *times* reads (both the
    plain read and the share-delete read refuse, as Windows does)."""
    left = {"n": times}
    real_open, real_read_text = safe_io.open_shared, pathlib.Path.read_text

    def violated(p):
        if pathlib.Path(p).name in ("scheduler.json", "_shared.json") and left["n"] > 0:
            left["n"] -= 1
            raise PermissionError(13, "The process cannot access the file because it is "
                                      "being used by another process", str(p))

    def open_shared(p, *a, **k):
        violated(p)
        return real_open(p, *a, **k)

    def read_text(self, *a, **k):
        violated(self)
        return real_read_text(self, *a, **k)

    monkeypatch.setattr(safe_io, "open_shared", open_shared)
    monkeypatch.setattr(pathlib.Path, "read_text", read_text)
    return left


def test_a_transient_sharing_violation_reads_the_stored_settings(tmp_path, monkeypatch):
    scheduler.save_settings(tmp_path, {"env_python": "/py", "global_simulate": False})
    _held(monkeypatch, 1)
    got = scheduler.load_settings(tmp_path)
    assert got["global_simulate"] is False and got["env_python"] == "/py", got


def test_a_queued_item_never_runs_on_default_settings(tmp_path, monkeypatch):
    ran = []
    monkeypatch.setattr(scheduler, "_run_item", lambda inst, item, settings, runner: (
        ran.append(settings.get("global_simulate")) or
        {"status": "done", "error": None, "returncode": 0, "log_file": "x"}))
    scheduler.save_settings(tmp_path, {"env_python": "/py", "global_simulate": False})
    scheduler.add_item(tmp_path, _info("a"))
    left = _held(monkeypatch, 10 ** 6)          # held for as long as the worker looks
    scheduler.start(tmp_path)
    assert _wait(lambda: not scheduler.is_running(tmp_path))
    st = scheduler.load_queue(tmp_path)
    assert ran == [], f"an item ran on settings it could not read: simulate={ran}"
    assert st["queue"][0]["status"] == "queued"
    assert st["run"]["status"] == "paused" and "could not be read" in st["run"]["message"], st["run"]
    left["n"] = 0
    scheduler.start(tmp_path)                   # readable again: Start runs it, for real
    assert _wait(lambda: not scheduler.is_running(tmp_path))
    assert ran == [False]


def test_a_save_over_an_unreadable_file_refuses(tmp_path, monkeypatch):
    scheduler.save_settings(tmp_path, {"env_python": "/py", "global_simulate": False})
    _held(monkeypatch, 10 ** 6)
    with pytest.raises(scheduler.SettingsUnreadable):
        scheduler.save_settings(tmp_path, {"failure_policy": "continue"})
    monkeypatch.undo()
    got = scheduler.load_settings(tmp_path)
    assert got["global_simulate"] is False and got["env_python"] == "/py", got


def test_the_settings_route_answers_409_not_500(tmp_path, monkeypatch):
    from quam_state_manager.web import routes
    from quam_state_manager.web.app import create_app
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    with app.test_request_context():
        inst = routes._sched_inst()
    scheduler.save_settings(inst, {"env_python": "/py", "global_simulate": False})
    _held(monkeypatch, 10 ** 6)
    r = c.post("/scheduler/settings", data={"failure_policy": "continue"})
    assert r.status_code == 409, (r.status_code, r.data[:300])
    assert "could not be read" in r.get_json()["error"]
