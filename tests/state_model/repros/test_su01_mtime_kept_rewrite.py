"""SU-01: a same-size live rewrite that keeps the old mtime is never seen by the status control.

Harness: an OUTSIDE ``samewidth`` op (one digit of a float changed, file size
equal, mtime put back) -> the status control keeps saying "synced" on every
later poll, also after the 30 s live-hash recheck has run (here: at 0 s).
Contract (VPLAN A.3/A.6 "a same-width rewrite is seen by hash within 30 s";
sync-ux 2026-09-25: one honest status): the control says "live".
"""
import os

import pytest

from tests.state_model.repros._rig import Chip


@pytest.mark.xfail(strict=True, reason="SU-01: status facts are keyed on live mtimes only")
def test_a_rewrite_that_keeps_the_mtime_is_announced(tmp_path, monkeypatch):
    from quam_state_manager.web import routes
    monkeypatch.setattr(routes, "_LIVE_HASH_RECHECK_S", 0.0)     # the 30 s recheck is due
    chip = Chip(tmp_path)
    assert chip.status() == "synced"
    p = chip.live / "state.json"
    raw = p.read_bytes()
    old = os.stat(p)
    needle = b'"chi": 1200000.0'
    assert needle in raw
    p.write_bytes(raw.replace(needle, b'"chi": 1200001.0', 1))
    assert os.stat(p).st_size == old.st_size
    os.utime(p, ns=(old.st_atime_ns, old.st_mtime_ns))
    drift = chip.c.get("/state/drift").get_json()
    assert chip.status() == "live", drift


@pytest.mark.xfail(strict=True, reason="SU-01: the pre-apply backup is skipped by mtime, Keep mine refuses")
def test_keep_mine_after_a_rewrite_that_keeps_the_mtime_lands(tmp_path):
    """The second face of SU-01: after Take live (which snapshots the chip) and
    an mtime-kept rewrite, Keep mine is refused "SM could not back up the live
    chip first (its files could not be read ...)" -- the files read fine; the
    snapshot store judges the chip unchanged by its mtimes and takes none."""
    chip = Chip(tmp_path)
    chip.edit("qubits.q1.T1", "3.1e-05")
    assert chip.c.post("/state/sync", data={"mode": "discard", "force": "1"}).status_code == 200
    p = chip.live / "state.json"
    raw = p.read_bytes()
    old = os.stat(p)
    p.write_bytes(raw.replace(b'"chi": 1200000.0', b'"chi": 1200001.0', 1))
    os.utime(p, ns=(old.st_atime_ns, old.st_mtime_ns))
    chip.edit("qubits.q2.T1", "4.1e-05")
    h = chip.c.get("/state/overwrite-live/preflight").get_json()["live_hash"]
    r = chip.c.post("/state/apply-to-live", data={"force": "1", "expect_live_hash": h})
    assert "Applied to the live chip" in r.get_data(as_text=True), r.get_data(as_text=True)[-400:]
