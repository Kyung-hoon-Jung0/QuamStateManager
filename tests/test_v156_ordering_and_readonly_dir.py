"""Two follow-ups from the Linux CI round.

1. A run's meta.json is written BEFORE its new status becomes visible and the
   waiters wake, so a reader woken by the change finds it on disk too.
2. On POSIX the read-only hint also sees a read-only FOLDER whose files are
   still writable (the apply replaces state.json by rename, which needs write
   on the folder).
"""
from __future__ import annotations

import json
import os
import stat
import sys

import pytest

from quam_state_manager.core import agent_runs
from quam_state_manager.web import routes


def test_meta_is_on_disk_when_the_status_changes(tmp_path, monkeypatch):
    reg = agent_runs.Registry(tmp_path)
    meta = {"key": "k1", "status": "running"}
    reg.runs["k1"] = meta
    seen = []
    real = reg._write_meta

    def spy(m):
        # at write time the in-memory record must not show the new status yet
        seen.append(reg.runs["k1"].get("status"))
        real(m)

    monkeypatch.setattr(reg, "_write_meta", spy)
    reg._set(meta, status="failed")
    assert seen == ["running"]
    assert meta["status"] == "failed"
    on_disk = json.loads((tmp_path / "agent_runs" / "k1" / "meta.json").read_text("utf-8"))
    assert on_disk["status"] == "failed"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX folder permissions")
@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores modes")
def test_a_read_only_folder_with_writable_files_is_reported(tmp_path):
    folder = tmp_path / "chip"
    folder.mkdir()
    (folder / "state.json").write_text("{}", "utf-8")
    (folder / "wiring.json").write_text("{}", "utf-8")
    assert routes._probe_readonly(folder) is False
    folder.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        assert routes._probe_readonly(folder) is True
    finally:
        folder.chmod(stat.S_IRWXU)
