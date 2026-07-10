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
