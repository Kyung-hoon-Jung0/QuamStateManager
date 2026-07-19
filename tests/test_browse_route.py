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


def test_dialog_cap_truncates_with_flag(client, tmp_path):
    # The dialog cap is 400 (the old silent 50 hid most of big lab archives);
    # over it the payload says so, so the client can render an honest note.
    big = tmp_path / "big"
    big.mkdir()
    for i in range(410):
        (big / f"d{i:03d}").mkdir()
    r = client.get("/browse", query_string={"path": str(big)})
    body = r.get_json()
    assert len(body["dirs"]) == 400
    assert body["truncated"] is True
    assert body["total"] == 410


def test_under_cap_has_no_truncated_flag(client, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (work / "one").mkdir()
    body = client.get("/browse", query_string={"path": str(work)}).get_json()
    assert "truncated" not in body and "total" not in body


def test_listing_sort_is_case_insensitive(client, tmp_path):
    # Display ORDER only — the paths themselves are untouched.
    work = tmp_path / "work"
    work.mkdir()
    for n in ("Zeta", "alpha", "Beta"):
        (work / n).mkdir()
    body = client.get("/browse", query_string={"path": str(work)}).get_json()
    assert [Path(d).name for d in body["dirs"]] == ["alpha", "Beta", "Zeta"]


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


class TestStatEpermNever500:
    """A path under a non-traversable directory makes Path.is_dir() RAISE
    PermissionError (only ENOENT-class errnos are swallowed) — audit-proven
    500 on mode-000 dirs. Both /browse branches must classify it as missing.
    (Monkeypatched rather than chmod 000 — chmod is flaky across runners.)"""

    @pytest.fixture
    def _eperm_under_locked(self, monkeypatch):
        real_is_dir = Path.is_dir

        def fake_is_dir(self, *a, **k):
            if "locked" in self.parts:
                raise PermissionError(13, "Permission denied")
            return real_is_dir(self, *a, **k)

        monkeypatch.setattr(Path, "is_dir", fake_is_dir)

    def test_dialog_branch_walks_to_ancestor(self, client, tmp_path,
                                             _eperm_under_locked):
        keep = tmp_path / "keep"
        keep.mkdir()
        target = keep / "locked" / "inner"
        r = client.get("/browse", query_string={"path": str(target)})
        assert r.status_code == 200          # never a 500
        body = r.get_json()
        assert body["path"] == str(keep)     # ancestor walk landed above the EPERM
        assert body["missing"] == str(target)

    def test_complete_branch_returns_empty(self, client, tmp_path,
                                           _eperm_under_locked):
        target = tmp_path / "locked" / "inn"
        r = client.get("/browse", query_string={"path": str(target),
                                                "complete": "1"})
        assert r.status_code == 200
        assert r.get_json()["dirs"] == []


def test_complete_relative_input_never_lists_cwd(client):
    # The dialog branch had this guard; the autocomplete branch didn't —
    # a relative input listed the APP CWD and returned bare relative
    # suggestions (audit).
    r = client.get("/browse", query_string={"path": "data", "complete": "1"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["dirs"] == []
    assert body["path"] == "data"


class TestDotDirCompletion:
    """Typing a dot-prefixed segment must surface dot-dirs (audit: ~/.qual
    completed to nothing — the app's own ~/.qualibrate was unreachable);
    non-dot prefixes keep hiding them."""

    def test_dot_prefix_reveals_dot_dirs(self, client, tmp_path):
        (tmp_path / ".qualibrate").mkdir()
        (tmp_path / ".qsm").mkdir()
        (tmp_path / "visible").mkdir()
        r = client.get("/browse", query_string={
            "path": str(tmp_path / ".qual"), "complete": "1"})
        assert r.get_json()["dirs"] == [str(tmp_path / ".qualibrate")]

    def test_plain_prefix_still_hides_dot_dirs(self, client, tmp_path):
        (tmp_path / ".vault").mkdir()
        (tmp_path / "vault").mkdir()
        r = client.get("/browse", query_string={
            "path": str(tmp_path / "v"), "complete": "1"})
        assert r.get_json()["dirs"] == [str(tmp_path / "vault")]

    def test_direct_navigation_into_dot_dir_lists_children(self, client, tmp_path):
        # A TYPED dot-path that exists is a normal dialog navigation — its
        # children list (the dialog hides dot-CHILDREN, not dot-parents).
        q = tmp_path / ".qualibrate"
        (q / "projects").mkdir(parents=True)
        r = client.get("/browse", query_string={"path": str(q)})
        body = r.get_json()
        assert body["path"] == str(q)
        assert str(q / "projects") in body["dirs"]
        assert "missing" not in body


@posix_only
def test_browse_expands_tilde(client, monkeypatch, tmp_path):
    home = tmp_path / "home"
    (home / "chips").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    r = client.get("/browse", query_string={"path": "~/chips"})
    body = r.get_json()
    assert body["path"] == str(home / "chips")
    assert "missing" not in body


@posix_only
class TestSystemPathBoundary:
    """The old str.startswith block also caught siblings (C:\\Windows_backup);
    the parts-prefix comparison must not. POSIX gains its own block list."""

    def test_posix_blocklist_and_parts_boundary(self):
        from quam_state_manager.web.routes import _is_system_path
        assert _is_system_path(Path("/proc")) is True
        assert _is_system_path(Path("/proc/1")) is True
        assert _is_system_path(Path("/sys")) is True
        assert _is_system_path(Path("/dev")) is True
        assert _is_system_path(Path("/etc/systemd")) is True
        # Sibling whose NAME merely starts with a blocked prefix — allowed.
        assert _is_system_path(Path("/etcetera")) is False

    def test_root_blocked_for_mkdir_only(self):
        from quam_state_manager.web.routes import _is_system_path
        assert _is_system_path(Path("/")) is False
        assert _is_system_path(Path("/"), for_mkdir=True) is True

    def test_browse_system_path_returns_empty_200(self, client):
        r = client.get("/browse", query_string={"path": "/proc"})
        assert r.status_code == 200
        assert r.get_json()["dirs"] == []

    def test_mkdir_into_etc_403(self, client):
        r = client.post("/mkdir", data={"path": "/etc", "name": "x"})
        assert r.status_code == 403

    def test_mkdir_at_root_403(self, client):
        r = client.post("/mkdir", data={"path": "/", "name": "x"})
        assert r.status_code == 403


class TestMkdirHardening:
    """Path-ingestion + portable-name policy for the 'New folder' action."""

    def test_relative_parent_rejected(self, client):
        # Audit: a relative parent created folders under the APP's CWD.
        r = client.post("/mkdir", data={"path": "relative/dir", "name": "x"})
        assert r.status_code == 400
        assert "absolute" in r.get_json()["error"].lower()

    @posix_only
    def test_tilde_parent_expands(self, client, monkeypatch, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        r = client.post("/mkdir", data={"path": "~", "name": "newdir"})
        assert r.get_json()["ok"] is True
        assert (home / "newdir").is_dir()

    @pytest.mark.parametrize(
        "bad", ["a<b", "a>b", "a:b", 'a"b', "a|b", "a?b", "a*b"])
    def test_windows_illegal_chars_rejected_everywhere(self, client, tmp_path, bad):
        # Portable-folder policy: rejected on ALL OSes, with the reason.
        r = client.post("/mkdir", data={"path": str(tmp_path), "name": bad})
        assert r.status_code == 400
        assert "portable" in r.get_json()["error"].lower()
        assert not (tmp_path / bad).exists()

    def test_trailing_dot_rejected(self, client, tmp_path):
        r = client.post("/mkdir", data={"path": str(tmp_path), "name": "trail."})
        assert r.status_code == 400
        assert "portable" in r.get_json()["error"].lower()

    def test_trailing_space_trimmed_never_created(self, client, tmp_path):
        # The route strips surrounding whitespace on ingestion, so a name
        # ending in ' ' (unrepresentable on Windows) can never be created —
        # it lands as the trimmed name instead.
        r = client.post("/mkdir", data={"path": str(tmp_path), "name": "trail "})
        assert r.get_json()["ok"] is True
        assert (tmp_path / "trail").is_dir()
        assert not (tmp_path / "trail ").exists()

    @pytest.mark.parametrize(
        "bad", ["CON", "con", "Com7", "lpt3", "NUL.backup", "aux.d"])
    def test_windows_reserved_stems_rejected(self, client, tmp_path, bad):
        r = client.post("/mkdir", data={"path": str(tmp_path), "name": bad})
        assert r.status_code == 400
        assert "reserved" in r.get_json()["error"].lower()
        assert not (tmp_path / bad).exists()

    @pytest.mark.parametrize("good", ["runs_2026.bak", ".hidden", "COMET", "CONF"])
    def test_portable_names_still_created(self, client, tmp_path, good):
        # Reserved-stem matching is exact — COMET/CONF are fine names.
        r = client.post("/mkdir", data={"path": str(tmp_path), "name": good})
        assert r.get_json()["ok"] is True, r.data
        assert (tmp_path / good).is_dir()
