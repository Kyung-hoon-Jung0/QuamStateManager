"""SU-01: a same-size live rewrite that keeps the old mtime is never seen by the status control.

Harness: an OUTSIDE ``samewidth`` op (one digit of a float changed, file size
equal, mtime put back) -> the status control keeps saying "synced" on every
later poll, also after the 30 s live-hash recheck has run (here: at 0 s).
Contract (VPLAN A.3/A.6 "a same-width rewrite is seen by hash within 30 s";
sync-ux 2026-09-25: one honest status): the control says "live".
"""
import os

from tests.state_model.repros._rig import Chip


# v1.3.0: xfail -> pass, periodic status refresh must verify content too.
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


# v1.3.0: xfail -> pass, the live backup must verify content before overwrite.
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


def test_a_backup_failure_names_history_without_claiming_an_unreadable_chip(tmp_path, monkeypatch):
    from quam_state_manager.web import routes
    chip = Chip(tmp_path)
    chip.edit("qubits.q1.T1", "3.1e-05")
    with chip.app.app_context():
        hm = routes._history()
    monkeypatch.setattr(hm, "check_and_snapshot", lambda *a, **kw: None)
    monkeypatch.setattr(hm, "snapshot_ts_for_current_content", lambda *a, **kw: None)
    before = (chip.live / "state.json").read_bytes()
    r = chip.c.post("/state/apply-to-live", data={"force": "1"},
                    headers={"Accept": "application/json"})
    assert r.status_code == 409
    assert r.get_json()["conflict"] == "no_backup"
    assert "history" in r.get_json()["message"]
    assert "files could not be read" not in r.get_json()["message"]
    assert (chip.live / "state.json").read_bytes() == before
    assert chip.log() == [("qubits.q1.T1", 3.1e-05)]
