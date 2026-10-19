"""v1.5.1 R4 a5: a registry record is never deleted because the clock went back.

R3 M1 judged a record "a dead process's, its pid reused" when the process
now holding the pid started after the record was last written -- two
wall-clock times. A clock set back between a live peer's start and its
record write made its record look exactly like that: dropped AND unlinked,
which un-gated the "older SM is running" rule and the history re-key guard.

A record now names the process that wrote it (``process_start``, the OS's
own start value), and a reader compares that -- no clock involved. A record
without it (an older SM) is still judged by the clock, but one that looks
stale is only skipped, never deleted.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from quam_state_manager.core import instances, safe_io
from tests.test_v151_r2_tray_identity import bystander  # noqa: F401


def _record(reg, pid, updated, **extra):
    reg.mkdir(parents=True, exist_ok=True)
    rec = reg / f"{pid}.json"
    safe_io.atomic_write_json(rec, {
        "pid": pid, "port": 5999, "started_utc": updated, "updated_utc": updated,
        "chip_path": "", "chip_fs_key": "", "chip_name": "", "project": "", "roots": [],
        "tray_protocol": instances.TRAY_PROTOCOL, **extra})
    old = 1_700_000_000
    os.utime(rec, (old, old))                  # an old file, never "just written"
    return rec


def _an_hour_before_start(pid):
    started = instances.process_start_epoch(pid)
    if started is None:
        pytest.skip("the OS cannot say when a process started")
    return datetime.fromtimestamp(started - 3600, timezone.utc).isoformat()


def test_a_live_peer_whose_record_reads_older_than_its_start_is_still_a_peer(tmp_path, bystander):
    """The clock was set back an hour after the peer started: its record's
    time reads before its own start. It names its process: a peer."""
    rec = _record(tmp_path / "instances", bystander.pid, _an_hour_before_start(bystander.pid),
                  process_start=instances.process_start(bystander.pid))
    assert [p.pid for p in instances.peers(tmp_path)] == [bystander.pid]
    assert rec.exists()


def test_an_older_record_that_only_looks_stale_is_skipped_not_deleted(tmp_path, bystander):
    rec = _record(tmp_path / "instances", bystander.pid, _an_hour_before_start(bystander.pid))
    assert instances.peers(tmp_path) == [], "judged by the clock: not taken as a peer"
    assert rec.exists(), "a clock set back looks the same: the record is never deleted on it"


def test_a_record_naming_another_process_is_a_reused_pid(tmp_path, bystander):
    """Its time is current (the clock rule would keep it); the process it
    names is not the one holding the pid now: dead, and cleaned up."""
    start = instances.process_start(bystander.pid)
    if start is None:
        pytest.skip("the OS cannot say when a process started")
    now = datetime.now(timezone.utc).isoformat()
    rec = _record(tmp_path / "instances", bystander.pid, now, process_start=start - 1)
    assert instances.peers(tmp_path) == []
    assert not rec.exists()


def test_this_process_names_itself_in_its_record(tmp_path):
    instances.register(tmp_path, port=5998)
    rec = safe_io.scan_json(tmp_path / "instances" / f"{os.getpid()}.json")
    assert rec["process_start"] == instances.process_start(os.getpid())
    instances.deregister(tmp_path)
    instances.update(tmp_path, chip_path="", chip_name="x")    # a record update() creates
    rec = safe_io.scan_json(tmp_path / "instances" / f"{os.getpid()}.json")
    assert rec["process_start"] == instances.process_start(os.getpid())
    instances.deregister(tmp_path)
