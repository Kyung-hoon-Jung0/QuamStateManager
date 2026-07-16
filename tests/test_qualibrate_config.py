"""Tests for core/qualibrate_config — resolving Qualibrate's live state path
(per active project) and the /workbench watch status."""

from __future__ import annotations

import os
from pathlib import Path

from quam_state_manager.core import qualibrate_config as qc


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_state_path_env_override_wins(tmp_path, monkeypatch):
    target = tmp_path / "override"
    monkeypatch.setenv("QUALIBRATE_STATE_PATH", str(target))
    # even with a config dir present, the env override takes precedence
    monkeypatch.setenv("QUALIBRATE_CONFIG_DIR", str(tmp_path / "cfg"))
    assert qc.resolve_live_state_path() == Path(str(target))


def test_resolves_per_project_state_path(tmp_path, monkeypatch):
    monkeypatch.delenv("QUALIBRATE_STATE_PATH", raising=False)
    cfg = tmp_path / "cfg"
    _write(cfg / "config.toml",
           '[qualibrate]\nproject = "P1"\n\n[quam]\nstate_path = "/global/old"\n')
    _write(cfg / "projects" / "P1" / "config.toml",
           '[quam]\nstate_path = "/proj/live"\n')
    monkeypatch.setenv("QUALIBRATE_CONFIG_DIR", str(cfg))
    # the per-project path must win over the stale global one
    assert qc.resolve_live_state_path() == Path("/proj/live")


def test_falls_back_to_global_state_path(tmp_path, monkeypatch):
    monkeypatch.delenv("QUALIBRATE_STATE_PATH", raising=False)
    cfg = tmp_path / "cfg"
    _write(cfg / "config.toml",
           '[qualibrate]\nproject = "P1"\n\n[quam]\nstate_path = "/global/only"\n')
    # per-project config exists but has no quam.state_path -> fall back to global
    _write(cfg / "projects" / "P1" / "config.toml", "[qualibrate.database_state]\nis_connected = false\n")
    monkeypatch.setenv("QUALIBRATE_CONFIG_DIR", str(cfg))
    assert qc.resolve_live_state_path() == Path("/global/only")


def test_returns_none_when_unresolvable(tmp_path, monkeypatch):
    monkeypatch.delenv("QUALIBRATE_STATE_PATH", raising=False)
    monkeypatch.setenv("QUALIBRATE_CONFIG_DIR", str(tmp_path / "does-not-exist"))
    assert qc.resolve_live_state_path() is None


def test_live_state_status_ok_and_detects_change(tmp_path, monkeypatch):
    sd = tmp_path / "quam_state"
    sd.mkdir()
    (sd / "state.json").write_text("{}", encoding="utf-8")
    (sd / "wiring.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("QUALIBRATE_STATE_PATH", str(sd))

    s1 = qc.live_state_status()
    assert s1["ok"] is True
    assert s1["files"] == 2
    assert s1["mtime"] is not None
    assert Path(s1["path"]) == sd

    # simulate Qualibrate writing the live file -> newer mtime detected
    newer = s1["mtime"] + 10
    os.utime(sd / "state.json", (newer, newer))
    s2 = qc.live_state_status()
    assert s2["mtime"] > s1["mtime"]


def test_live_state_status_not_ok_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("QUALIBRATE_STATE_PATH", str(tmp_path / "ghost"))
    s = qc.live_state_status()
    assert s["ok"] is False
    assert "reason" in s


# =====================================================================
# Projects browser (docs/55) — list/effective/lint/tray over a synthetic
# ~/.qualibrate whose anomaly set replicates the studied real folder:
# a dangling ACTIVE state_path (with an existing sibling), shared dataset
# storage across inheritors, a 0-byte overlay, an explicit state_path = "".
# =====================================================================

import pytest


def _projects_tree(tmp_path: Path) -> dict[str, Path]:
    """Synthetic .qualibrate + chip folders. Returns the interesting paths."""
    cfg = tmp_path / ".qualibrate"
    chips = tmp_path / "chips"
    good = chips / "good_chip"
    good.mkdir(parents=True)
    (good / "state.json").write_text("{}", encoding="utf-8")
    (good / "wiring.json").write_text("{}", encoding="utf-8")
    # the sibling the doctor should suggest for the dangling path
    (chips / "quam_state_sibling").mkdir()
    shared = tmp_path / "datasets" / "shared"
    shared.mkdir(parents=True)
    own_storage = tmp_path / "datasets" / "beta_only"
    own_storage.mkdir()

    _write(cfg / "config.toml", f'''
[qualibrate]
project = "alpha"
version = 5

[qualibrate.storage]
location = "{shared}"

[quam]
state_path = "{good}"
version = 3
''')
    # alpha: ACTIVE + dangling own state_path (the real folder's worst anomaly)
    _write(cfg / "projects" / "alpha" / "config.toml",
           f'[quam]\nstate_path = "{chips / "missing_chip"}"\n')
    # beta: healthy — own existing state_path + own storage
    _write(cfg / "projects" / "beta" / "config.toml",
           f'[quam]\nstate_path = "{good}"\n\n'
           f'[qualibrate.storage]\nlocation = "{own_storage}"\n')
    # gamma: 0-byte overlay — pure inheritor
    (cfg / "projects" / "gamma").mkdir(parents=True)
    (cfg / "projects" / "gamma" / "config.toml").touch()
    # delta: explicit EMPTY state_path (an override, not an omission)
    _write(cfg / "projects" / "delta" / "config.toml",
           '[quam]\nstate_path = ""\n')
    # epsilon: lazy project template in its storage location
    _write(cfg / "projects" / "epsilon" / "config.toml",
           '[qualibrate.storage]\nlocation = "'
           + str(tmp_path / "datasets") + '/${#/qualibrate/project}"\n')
    return {"cfg": cfg, "good": good, "shared": shared,
            "own_storage": own_storage, "chips": chips}


class TestNativePath:
    @pytest.mark.skipif(os.name == "nt", reason="dialect mapping is for POSIX hosts")
    def test_windows_drive_maps_to_mnt(self):
        assert qc.native_path(r"D:\work\chips") == Path("/mnt/d/work/chips")
        # lowercase drive + forward slashes (both appear in the real folder)
        assert qc.native_path("d:/work/x") == Path("/mnt/d/work/x")
        assert qc.native_path(r"C:\Users\u\.qualibrate") == Path(
            "/mnt/c/Users/u/.qualibrate")

    def test_passthrough_and_empties(self):
        assert qc.native_path("/mnt/d/already/native") == Path("/mnt/d/already/native")
        assert qc.native_path("") is None          # explicit-override empty
        assert qc.native_path("   ") is None
        assert qc.native_path(None) is None
        assert qc.native_path(5) is None           # non-string config garbage


class TestDeepMerge:
    def test_recursive_dict_scalar_and_new_keys(self):
        base = {"a": {"x": 1, "y": 2}, "s": "old", "l": [1, 2]}
        over = {"a": {"y": 20, "z": 30}, "s": "new", "l": [9], "n": True}
        out = qc._deep_merge(base, over)
        assert out == {"a": {"x": 1, "y": 20, "z": 30}, "s": "new",
                       "l": [9], "n": True}
        # inputs untouched (a NEW dict is returned)
        assert base["a"] == {"x": 1, "y": 2} and base["l"] == [1, 2]

    def test_dict_replaces_scalar_and_vice_versa(self):
        assert qc._deep_merge({"k": 1}, {"k": {"a": 2}}) == {"k": {"a": 2}}
        assert qc._deep_merge({"k": {"a": 2}}, {"k": 1}) == {"k": 1}


class TestConfigDirEnv:
    def test_official_file_form(self, tmp_path, monkeypatch):
        cfg = tmp_path / "qcfg"
        _write(cfg / "config.toml", "[qualibrate]\nproject = \"p\"\n")
        monkeypatch.setenv("QUALIBRATE_CONFIG_FILE", str(cfg / "config.toml"))
        monkeypatch.delenv("QUALIBRATE_CONFIG_DIR", raising=False)
        assert qc._config_dir() == cfg

    def test_official_dir_form(self, tmp_path, monkeypatch):
        cfg = tmp_path / "qcfg"
        cfg.mkdir()
        monkeypatch.setenv("QUALIBRATE_CONFIG_FILE", str(cfg))
        assert qc._config_dir() == cfg

    def test_official_wins_over_legacy_alias(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QUALIBRATE_CONFIG_FILE", str(tmp_path / "official"))
        monkeypatch.setenv("QUALIBRATE_CONFIG_DIR", str(tmp_path / "legacy"))
        assert qc._config_dir() == tmp_path / "official"

    def test_quam_state_path_env_wins(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QUAM_STATE_PATH", str(tmp_path / "quam_env"))
        monkeypatch.setenv("QUALIBRATE_STATE_PATH", str(tmp_path / "legacy_env"))
        assert qc.resolve_live_state_path() == tmp_path / "quam_env"


class TestEffectiveConfig:
    def test_overlay_cannot_rename_active_project(self, tmp_path):
        cfg = tmp_path / ".qualibrate"
        _write(cfg / "config.toml", '[qualibrate]\nproject = "alpha"\n')
        _write(cfg / "projects" / "alpha" / "config.toml",
               '[qualibrate]\nproject = "evil"\n[quam]\nstate_path = "/x"\n')
        eff = qc.effective_config("alpha", cfg_dir=cfg)
        # qualibrate force-sets the project name before merging
        assert eff["qualibrate"]["project"] == "alpha"
        assert eff["quam"]["state_path"] == "/x"

    def test_zero_byte_overlay_inherits_everything(self, tmp_path):
        paths = _projects_tree(tmp_path)
        eff = qc.effective_config("gamma", cfg_dir=paths["cfg"])
        assert eff["quam"]["state_path"] == str(paths["good"])
        assert eff["qualibrate"]["project"] == "gamma"


class TestListProjects:
    def test_listing_shape_and_sources(self, tmp_path):
        paths = _projects_tree(tmp_path)
        listing = qc.list_projects(paths["cfg"])
        assert listing["ok"] and listing["config_exists"]
        assert listing["active"] == "alpha"
        assert listing["versions"] == {"qualibrate": 5, "quam": 3,
                                       "supported": True}
        by = {p["name"]: p for p in listing["projects"]}
        assert set(by) == {"alpha", "beta", "gamma", "delta", "epsilon"}

        assert by["alpha"]["active"] is True
        assert by["alpha"]["state_path"]["source"] == "own"
        assert by["alpha"]["state_path"]["exists"] is False

        assert by["beta"]["state_path"]["exists"] is True
        assert by["beta"]["storage"]["source"] == "own"

        assert by["gamma"]["overlay_empty"] is True
        assert by["gamma"]["state_path"]["source"] == "inherited"
        assert by["gamma"]["state_path"]["exists"] is True

        # explicit empty override is NOT "inherited from root"
        assert by["delta"]["state_path"]["source"] == "empty"
        assert by["delta"]["state_path"]["native"] is None
        assert by["delta"]["state_path"]["exists"] is False

        # lazy ${#/qualibrate/project} template substituted per project
        assert by["epsilon"]["storage"]["raw"].endswith("/epsilon")

    def test_missing_config_dir(self, tmp_path):
        listing = qc.list_projects(tmp_path / "ghost")
        assert listing["config_exists"] is False
        assert listing["projects"] == []


class TestLint:
    def test_real_folder_anomaly_set(self, tmp_path):
        paths = _projects_tree(tmp_path)
        listing = qc.list_projects(paths["cfg"])
        findings = qc.lint(listing)
        codes = {(f["code"], f.get("project")) for f in findings}

        # active project's dangling state_path is an ERROR with the sibling hint
        dangle = next(f for f in findings if f["code"] == "state_path_dangling"
                      and f["project"] == "alpha")
        assert dangle["severity"] == "error"
        assert "quam_state_sibling" in dangle.get("suggestion", "")

        assert ("state_path_empty", "delta") in codes
        # alpha + gamma + delta inherit the root storage → shared-root info
        shared = next(f for f in findings if f["code"] == "storage_shared")
        assert shared["severity"] == "info"
        for name in ("alpha", "gamma", "delta"):
            assert name in shared["message"]
        assert "beta" not in shared["message"]
        # healthy config: no version drift, no parse errors
        assert not any(f["code"] in ("version_drift", "no_config",
                                     "unparseable_config") for f in findings)

    def test_no_config_and_unparseable(self, tmp_path):
        ghost = qc.list_projects(tmp_path / "ghost")
        assert [f["code"] for f in qc.lint(ghost)] == ["no_config"]

        bad = tmp_path / "bad"
        _write(bad / "config.toml", "not [valid toml ===")
        listing = qc.list_projects(bad)
        assert [f["code"] for f in qc.lint(listing)] == ["unparseable_config"]

    def test_version_drift_flags_read_only(self, tmp_path):
        cfg = tmp_path / ".qualibrate"
        _write(cfg / "config.toml",
               '[qualibrate]\nproject = "p"\nversion = 4\n[quam]\nversion = 3\n')
        (cfg / "projects" / "p").mkdir(parents=True)
        (cfg / "projects" / "p" / "config.toml").touch()
        listing = qc.list_projects(cfg)
        assert listing["versions"]["supported"] is False
        assert any(f["code"] == "version_drift" for f in qc.lint(listing))


class TestTrayStatus:
    def test_active_and_dangling(self, tmp_path):
        paths = _projects_tree(tmp_path)
        st = qc.tray_status(paths["cfg"])
        assert st["config_exists"] is True
        assert st["active"] == "alpha"
        assert st["state_exists"] is False   # alpha's state_path is dangling

    def test_cache_invalidates_on_root_edit(self, tmp_path):
        paths = _projects_tree(tmp_path)
        cfg = paths["cfg"]
        assert qc.tray_status(cfg)["active"] == "alpha"
        root = cfg / "config.toml"
        root.write_text(root.read_text(encoding="utf-8").replace(
            'project = "alpha"', 'project = "beta"'), encoding="utf-8")
        # force a distinct mtime_ns even on coarse filesystems
        stat = root.stat()
        os.utime(root, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
        st = qc.tray_status(cfg)
        assert st["active"] == "beta"
        assert st["state_exists"] is True    # beta's state_path exists

    def test_missing_config(self, tmp_path):
        st = qc.tray_status(tmp_path / "ghost")
        assert st == {"config_exists": False, "active": None, "state_raw": None,
                      "state_native": None, "state_exists": False}
