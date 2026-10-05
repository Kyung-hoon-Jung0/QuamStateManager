"""Several processes opening a NEW ledger at the same moment.

Two SM windows (or a window and the CLI) can create one chip's ledger
together. Each used to see an incomplete schema and run ALTER TABLE ->
"duplicate column name", or turn a read into a write inside WAL ->
"database is locked" at once; and each wrote its own ledger_id (the last
one won). Creation is one BEGIN IMMEDIATE transaction now, re-checked inside.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_OPENER = (
    "import sys, time\n"
    "from quam_state_manager.core.hub_store import HubStore\n"
    "t = float(sys.argv[2])\n"
    "while time.time() < t: pass\n"
    "with HubStore(sys.argv[1]) as st:\n"
    "    print(st.meta('ledger_id'))\n"
)


def _env():
    env = dict(os.environ, PYTHONUTF8="1", PYTHONPATH=str(ROOT))
    return env


def test_many_processes_create_one_ledger_together(tmp_path):
    """Four rounds of eight openers each: the race is timing-dependent, so one
    round alone caught the old code only now and then."""
    import time
    for round_ in range(4):
        chip = tmp_path / f"chip{round_}"
        start = time.time() + 2.0                  # every process spins until the same instant
        procs = [subprocess.Popen([sys.executable, "-c", _OPENER, str(chip), str(start)],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=_env())
                 for _ in range(8)]
        outs = [p.communicate(timeout=120) for p in procs]
        errors = [err for (_, err), p in zip(outs, procs) if p.returncode]
        assert not errors, (round_, errors)
        ids = {out.strip() for out, _ in outs}
        assert len(ids) == 1 and len(ids.pop()) == 16, (round_, "one identity, written once")


def test_an_existing_ledger_opens_without_taking_the_write_lock(tmp_path):
    """A complete schema and identity are only READ on open (docs/275: a second
    window used to wait for another window's long transaction just to open)."""
    from quam_state_manager.core.hub_store import HubStore
    with HubStore(tmp_path / "chip"):
        pass
    with HubStore(tmp_path / "chip") as holder:
        holder.conn.execute("BEGIN IMMEDIATE")      # another window mid-write
        t0 = __import__("time").perf_counter()
        with HubStore(tmp_path / "chip") as reader:
            assert reader.meta("ledger_id")
        assert __import__("time").perf_counter() - t0 < 2.0
        holder.conn.rollback()
