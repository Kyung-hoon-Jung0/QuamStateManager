"""docs/172: the frozen exe exposes the two helpers -- `quam-manager.exe --mcp`
/ `--hook` -- so a Claude Code config on an install without Python can name
them. main() dispatches BEFORE anything desktop-shaped runs."""

from __future__ import annotations

import sys

import pytest

from quam_state_manager import main as main_mod


def test_mcp_flag_runs_the_bridge_and_nothing_else(monkeypatch):
    ran = []
    monkeypatch.setattr("quam_state_manager.mcp.main", lambda: ran.append("mcp"))
    monkeypatch.setattr(main_mod, "webview", None)
    monkeypatch.setattr(sys, "argv", ["quam-manager", "--mcp"])
    main_mod.main()
    assert ran == ["mcp"]


def test_hook_flag_runs_the_hook_and_exits_with_its_code(monkeypatch):
    monkeypatch.setattr("quam_state_manager.hook.main", lambda: 0)
    monkeypatch.setattr(sys, "argv", ["quam-manager", "--hook"])
    with pytest.raises(SystemExit) as e:
        main_mod.main()
    assert e.value.code == 0
