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
    # Prefix completion is the AUTOCOMPLETE contract — it now requires
    # ?complete=1 (the dialog gets ancestor-walk semantics instead; see
    # TestAncestorWalkAndCompletion).
    (tmp_path / "data_2026").mkdir()
    (tmp_path / "data_2025").mkdir()
    (tmp_path / "other").mkdir()
    r = client.get("/browse", query_string={
        "path": str(tmp_path / "data"), "complete": "1"})
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


class TestAncestorWalkAndCompletion:
    """The breadcrumb root-jump fix: the dialog's /browse must ALWAYS list a
    real folder and report `path` as exactly that folder; a dead path lands
    at its nearest existing ancestor with a `missing` marker; the prefix-
    completion behavior survives ONLY behind ?complete=1 (autocomplete)."""

    def test_dead_path_lands_at_nearest_ancestor(self, client, tmp_path):
        keep = tmp_path / "keep"
        keep.mkdir()
        dead = keep / "was" / "deleted" / "deep"
        r = client.get("/browse", query_string={"path": str(dead)})
        body = r.get_json()
        assert body["path"] == str(keep)          # NOT the drive/fs root
        assert body["missing"] == str(dead)
        assert "error" not in body

    def test_file_path_lands_at_parent_with_marker(self, client, tmp_path):
        f = tmp_path / "state.json"
        f.write_text("{}")
        r = client.get("/browse", query_string={"path": str(f)})
        body = r.get_json()
        assert body["path"] == str(tmp_path)
        assert body["missing"] == str(f)

    def test_existing_dir_has_no_missing_marker(self, client, tmp_path):
        r = client.get("/browse", query_string={"path": str(tmp_path)})
        assert "missing" not in r.get_json()

    def test_complete_flag_keeps_prefix_completion(self, client, tmp_path):
        (tmp_path / "data_a").mkdir()
        (tmp_path / "data_b").mkdir()
        (tmp_path / "other").mkdir()
        r = client.get("/browse", query_string={
            "path": str(tmp_path / "data"), "complete": "1"})
        body = r.get_json()
        assert body["path"] == str(tmp_path)
        assert len(body["dirs"]) == 2
        assert all("data_" in d for d in body["dirs"])
        assert "missing" not in body

    def test_dialog_prefix_like_path_walks_not_completes(self, client, tmp_path):
        # WITHOUT complete=1 the same half-name path must NOT return
        # completions — it lists the parent with the missing marker.
        (tmp_path / "data_a").mkdir()
        r = client.get("/browse", query_string={"path": str(tmp_path / "data")})
        body = r.get_json()
        assert body["path"] == str(tmp_path)
        assert body["missing"] == str(tmp_path / "data")
        assert str(tmp_path / "data_a") in body["dirs"]   # full listing
        assert str(tmp_path / "other") not in body["dirs"]  # (doesn't exist)

    def test_relative_bogus_path_never_lists_cwd(self, client):
        # A relative junk path (e.g. "Z:/x" seen by a POSIX server) must NOT
        # ancestor-walk down to "." and expose the process CWD.
        r = client.get("/browse", query_string={"path": "Z:/totally/bogus"})
        body = r.get_json()
        assert body["dirs"] == []
        assert body["missing"] == "Z:/totally/bogus"


class TestDatasetKind:
    """kind=dataset (Dataset Load picker): children carrying node.json /
    data.json are marked so the dialog highlights dataset runs instead of
    quam_state folders."""

    def test_marks_dataset_children(self, client, tmp_path):
        run1 = tmp_path / "#1_res_spec_101010"
        run1.mkdir()
        (run1 / "node.json").write_text("{}")
        run2 = tmp_path / "#2_rabi_111111"
        run2.mkdir()
        (run2 / "data.json").write_text("{}")
        (tmp_path / "plain").mkdir()
        r = client.get("/browse", query_string={"path": str(tmp_path),
                                                "kind": "dataset"})
        body = r.get_json()
        assert sorted(body["dataset_dirs"]) == sorted([str(run1), str(run2)])
        assert str(tmp_path / "plain") not in body["dataset_dirs"]
        assert body["has_dataset"] is False   # the parent itself has no markers

    def test_current_folder_marker(self, client, tmp_path):
        (tmp_path / "node.json").write_text("{}")
        r = client.get("/browse", query_string={"path": str(tmp_path),
                                                "kind": "dataset"})
        assert r.get_json()["has_dataset"] is True

    def test_no_kind_no_dataset_fields(self, client, tmp_path):
        run1 = tmp_path / "run"
        run1.mkdir()
        (run1 / "node.json").write_text("{}")
        body = client.get("/browse", query_string={"path": str(tmp_path)}).get_json()
        assert "dataset_dirs" not in body and "has_dataset" not in body
