"""/browse hardening — Linux compatibility + honest failure reporting.

Backs the customer feedback "folder creation should work on Linux and be
much more stable": an empty path on POSIX now lists $HOME (not /), and an
unreadable directory returns an ``error`` field at HTTP 200 (the dialog
renders it with a Retry) instead of a silent empty listing.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
    return app.test_client()


posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only behavior")


@posix_only
def test_empty_path_lists_home(client, monkeypatch, tmp_path):
    home = tmp_path / "home"
    (home / "projects").mkdir(parents=True)
    (home / ".hidden").mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    r = client.get("/browse")
    assert r.status_code == 200
    body = r.get_json()
    assert body["path"] == str(home)
    assert str(home / "projects") in body["dirs"]
    assert all(".hidden" not in d for d in body["dirs"])
    # parent navigation still walks upward (never the pre-fix "" dead end).
    assert body["parent"] == str(home.parent)


def test_normal_listing(client, tmp_path):
    work = tmp_path / "work"          # keep the app's instance dir out of view
    work.mkdir()
    (work / "b_dir").mkdir()
    (work / "a_dir").mkdir()
    (work / ".hid").mkdir()
    (work / "file.txt").write_text("x")
    r = client.get("/browse", query_string={"path": str(work)})
    body = r.get_json()
    assert body["dirs"] == [str(work / "a_dir"), str(work / "b_dir")]
    assert "error" not in body


def test_cap_at_50(client, tmp_path):
    for i in range(60):
        (tmp_path / f"d{i:03d}").mkdir()
    r = client.get("/browse", query_string={"path": str(tmp_path)})
    assert len(r.get_json()["dirs"]) == 50


@posix_only
@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores permission bits")
def test_permission_denied_reports_error(client, tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o000)
    try:
        r = client.get("/browse", query_string={"path": str(locked)})
        assert r.status_code == 200          # never a 500
        body = r.get_json()
        assert body["error"] == "Permission denied"
        assert body["dirs"] == []
    finally:
        locked.chmod(0o755)


def test_nonexistent_path_prefix_completes(client, tmp_path):
    (tmp_path / "data_2026").mkdir()
    (tmp_path / "data_2025").mkdir()
    (tmp_path / "other").mkdir()
    r = client.get("/browse", query_string={"path": str(tmp_path / "data")})
    body = r.get_json()
    assert body["path"] == str(tmp_path)
    assert len(body["dirs"]) == 2
    assert all("data_" in d for d in body["dirs"])


@posix_only
def test_root_has_empty_parent(client):
    r = client.get("/browse", query_string={"path": "/"})
    body = r.get_json()
    assert body["path"] == "/"
    assert body["parent"] == ""


def test_quam_state_detection(client, tmp_path):
    (tmp_path / "state.json").write_text("{}")
    (tmp_path / "wiring.json").write_text("{}")
    r = client.get("/browse", query_string={"path": str(tmp_path)})
    assert r.get_json()["has_quam_state"] is True
