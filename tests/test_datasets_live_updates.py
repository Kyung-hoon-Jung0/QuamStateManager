"""Datasets live updates: a run whose saved state lands last is still listed,
the run watcher ticks when it does, and the sidebar wakes on a landed run and
keeps the marker on the run being shown (sidebar_live_run_selfcheck.cjs)."""
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from quam_state_manager.core import run_watch
from quam_state_manager.core.scanner import Workspace

ROOT = Path(__file__).resolve().parent.parent


def _bump(p: Path, ahead: float) -> None:
    now = time.time() + ahead
    os.utime(p, (now, now))


def _run(day: Path, i: int) -> Path:
    return day / f"#{i}_ramsey_0900{i:02d}"


def _write(run: Path, step: str, i: int) -> None:
    if step == "dir":
        run.mkdir(exist_ok=True)
    elif step == "node":
        body = {"metadata": {"name": "ramsey", "status": "finished"}, "id": i}
        (run / "node.json").write_text(json.dumps(body), encoding="utf-8")
    elif step == "qsdir":
        (run / "quam_state").mkdir(exist_ok=True)
    else:
        (run / "quam_state" / f"{step}.json").write_text("{}", encoding="utf-8")


@pytest.mark.parametrize("order", [
    ("dir", "node", "qsdir", "state", "wiring"),
    ("dir", "qsdir", "state", "wiring", "node"),
])
def test_a_run_whose_saved_state_lands_last_is_listed(tmp_path, order):
    root = tmp_path / "data"
    day = root / "2026-11-20"
    day.mkdir(parents=True)
    first = _run(day, 1)
    for step in ("dir", "node", "qsdir", "state", "wiring"):
        _write(first, step, 1)
    ws = Workspace()
    ws.add_root(root)
    assert len(ws.all_entries) == 1
    second = _run(day, 2)
    for k, step in enumerate(order):
        _write(second, step, 2)
        # every write moves the dir it lands in; make the move visible to a
        # coarse clock, then let the workspace look between the writes
        target = second if step in ("dir", "node", "qsdir") else second / "quam_state"
        _bump(target, 2.0 * (k + 1))
        if step == "dir":
            _bump(day, 2.0 * (k + 1))
        ws.rescan_if_stale()
        ws.rescan_if_stale()
    for _ in range(3):
        ws.rescan_if_stale()
    assert len(ws.all_entries) == 2, "the run whose state.json / wiring.json came last is listed"


def test_the_watcher_ticks_when_the_newest_run_gets_its_saved_state(tmp_path):
    root = tmp_path / "data"
    day = root / "2026-11-20"
    run = _run(day, 3)
    (run / "quam_state").mkdir(parents=True)
    s0 = run_watch.signature(str(root))
    (run / "quam_state" / "state.json").write_text("{}", encoding="utf-8")
    _bump(run / "quam_state", 3.0)
    s1 = run_watch.signature(str(root))
    assert s1 != s0, "state.json landing in the newest run's quam_state moves the signature"
    assert run_watch.signature(str(root)) == s1


def test_sidebar_wakes_on_a_landed_run_and_keeps_the_shown_marker():
    node = shutil.which("node")
    if node is None or subprocess.run([node, "-e", "require('jsdom')"], capture_output=True,
                                      cwd=str(ROOT), timeout=300).returncode != 0:
        pytest.skip("node + jsdom not available")
    r = subprocess.run([node, str(ROOT / "tests" / "sidebar_live_run_selfcheck.cjs")],
                       capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT), timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "sidebar_live_run_selfcheck ok" in r.stdout
