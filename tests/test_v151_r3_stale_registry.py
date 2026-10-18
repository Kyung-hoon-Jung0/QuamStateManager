"""v1.5.1 R3 M1: a stale registry record whose pid was reused is not a peer.

An older SM was killed and left ``instances/<pid>.json`` (no tray_protocol);
Windows has since given that pid to an unrelated process. ``peers()`` judged
liveness by the pid alone, so the record read as "an older SM is running" for
ever -- and a crashed window's plain tray checkpoint was then neither
restored nor listed, with no notice (R2 M2 again, through
``pending_tray._older_sm_running``). A record last written before the
process now holding its pid started is a dead process's: dropped.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from quam_state_manager.core import instances, safe_io
from tests.test_v151_r2_tray_identity import (  # noqa: F401
    _clear_memo, _main, _run, bystander, chip)


def _record(reg, pid, updated, **extra):
    reg.mkdir(parents=True, exist_ok=True)
    rec = reg / f"{pid}.json"
    safe_io.atomic_write_json(rec, {
        "pid": pid, "port": 5999, "started_utc": updated, "updated_utc": updated,
        "chip_path": "D:/somewhere/else/quam_state", "chip_fs_key": "", "chip_name": "other",
        "project": "", "roots": [], **extra})
    old = 1_700_000_000
    os.utime(rec, (old, old))                  # an old file, never "just written"
    return rec


def test_a_record_written_before_its_pids_process_started_is_not_a_peer(tmp_path, bystander):
    reg = tmp_path / "instances"
    rec = _record(reg, bystander.pid, "2026-10-01T09:05:00+00:00")
    assert instances.peers(tmp_path) == [], "a reused pid is not the record's process"
    assert not rec.exists(), "the stale record is cleaned up"


def test_a_record_the_running_process_wrote_is_still_a_peer(tmp_path, bystander):
    reg = tmp_path / "instances"
    now = datetime.now(timezone.utc).isoformat()
    _record(reg, bystander.pid, now)
    assert [p.pid for p in instances.peers(tmp_path)] == [bystander.pid]
    # a record without a usable time is judged by its pid, as before
    _record(reg, bystander.pid, "")
    assert [p.pid for p in instances.peers(tmp_path)] == [bystander.pid]


def test_the_start_time_is_read_in_utc(bystander):
    started = instances.process_start_epoch(bystander.pid)
    if started is None:
        return                                  # the OS cannot say: judged by pid alone
    now = datetime.now(timezone.utc).timestamp()
    assert now - 600 < started <= now + 1, (started, now)
    mine = instances.process_start_epoch(os.getpid())
    assert mine is not None and mine <= started


def test_a_stale_older_sm_record_does_not_hide_a_crashed_windows_checkpoint(chip, bystander):
    a = _run(chip["inst"], chip["live"], "stage", "qubits.q5.T1=9.1e-06")
    assert a["tray"] == ["qubits.q5.T1"]
    main = _main(chip)
    owner = json.loads(main.with_name(main.stem + ".owner.json").read_text(encoding="utf-8"))
    assert owner["pid"] == a["pid"] and owner.get("start"), owner
    reg = chip["inst"] / "instances"
    (reg / f"{a['pid']}.json").unlink(missing_ok=True)
    # an older SM (no tray_protocol) ran days ago and was killed; its pid is
    # the bystander's now
    written = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    _record(reg, bystander.pid, written)
    _clear_memo()
    b = _run(chip["inst"], chip["live"], "open")
    shown = b["tray"] + [x for d in b["unrecovered"] for x in d["rows"]]
    assert shown == ["qubits.q5.T1"], (
        f"the crashed window's staged edit is neither restored nor listed: {b}")
