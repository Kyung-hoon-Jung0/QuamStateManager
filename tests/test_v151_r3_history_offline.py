"""v1.5.1 R3 M2: a history dir's holder that is only OFFLINE keeps its dir.

``folder_probe`` answered False for an unreachable share or a missing drive
(``exists()`` maps those to "not found"), memoised that for 300 s, and
``_own_dir_for_folder`` read False as "the holder moved": the copy opened
meanwhile took the live chip's history dir. When the share was back the live
chip resolved to an empty ``<dir>__<hash>``. A copy NAMED meanwhile also
recorded its name alias to that dir, even on a read-only resolve.

Now the dir is handed over only on evidence of a move (the holder's drive or
share answers, and the asking folder holds content one of the dir's
snapshots holds, or the holder's parent answers while the holder does not);
a slow "not found" is no answer and never memoised; a read-only resolve
records no name alias. The earlier pin stubbed ``exists()`` to answer True;
these stub it the way an unreachable share really answers.
"""
from __future__ import annotations

import json
import os
import pathlib
import time
from pathlib import Path

import pytest

from quam_state_manager.core import folder_probe
from quam_state_manager.core.history import HistoryManager
from tests.test_v151_r2_history_holders import _owners, _write


@pytest.fixture(autouse=True)
def _fresh_probes():
    folder_probe.forget()
    yield
    folder_probe.forget()


def _live_chip(tmp_path, n=5):
    inst = tmp_path / "inst"
    hm = HistoryManager(inst, max_snapshots=50, cache_size=3)
    live = tmp_path / "share" / "lab" / "quam_state"
    for i in range(n):
        _write(live, 1.0e-5 + i * 1e-7)
        hm.check_and_snapshot(live, "manual", force=True)       # live captures
    hm.claim_history_dir(live)                                  # and the chip was opened
    return inst, live, hm._key_for(live)


def _offline(monkeypatch, root: Path, delay_s: float = 0.0):
    """Every path under *root* answers "not found" -- after *delay_s*, as an
    unreachable share does (ENOENT after the SMB timeout); 0 is a missing
    drive. Everything else is asked for real."""
    prefix = os.path.normcase(str(root))
    real = pathlib.Path.exists

    def exists(self, *a, **k):
        p = os.path.normcase(str(self))
        if p == prefix or p.startswith(prefix + os.sep):
            if delay_s:
                time.sleep(delay_s)
            return False
        return real(self, *a, **k)
    monkeypatch.setattr(pathlib.Path, "exists", exists)


def _back(monkeypatch):
    monkeypatch.undo()
    folder_probe.forget()


def test_a_briefly_offline_holder_keeps_its_dir_when_its_folder_is_renamed_away(tmp_path):
    inst, live, key = _live_chip(tmp_path)
    backup = _write(tmp_path / "backup" / "lab" / "quam_state", 2.0e-5)
    off = tmp_path / "share_offline"
    os.rename(tmp_path / "share", off)                          # the share drops
    folder_probe.forget()
    HistoryManager(inst, max_snapshots=50, cache_size=3).claim_history_dir(backup)
    os.rename(off, tmp_path / "share")                          # the share is back
    folder_probe.forget()
    fresh = HistoryManager(inst, max_snapshots=50, cache_size=3)
    assert fresh._key_for(live) == key and len(fresh.list_snapshots(live)) == 5, (
        f"the live chip lost its dir to the copy opened while it was offline: {_owners(inst)}")
    assert fresh._key_for(backup) != key


def test_an_unreachable_share_answering_not_found_keeps_the_dir(tmp_path, monkeypatch):
    inst, live, key = _live_chip(tmp_path)
    backup = _write(tmp_path / "backup" / "lab" / "quam_state", 2.0e-5)
    _offline(monkeypatch, tmp_path / "share")
    up = HistoryManager(inst, max_snapshots=50, cache_size=3)
    assert up._key_for(backup) != key, "the copy reads the offline chip's history"
    up.claim_history_dir(backup)                                # the user opens the copy
    up.check_and_snapshot(backup, "manual", force=True)         # and it captures
    assert _owners(inst).get(key) and "backup" not in _owners(inst)[key], _owners(inst)
    _back(monkeypatch)
    fresh = HistoryManager(inst, max_snapshots=50, cache_size=3)
    assert fresh._key_for(live) == key and len(fresh.list_snapshots(live)) == 5
    assert len(fresh.list_snapshots(backup)) == 1


def test_a_slow_not_found_is_no_answer_and_never_remembered(tmp_path, monkeypatch):
    inst, live, key = _live_chip(tmp_path, n=3)
    backup = _write(tmp_path / "backup" / "lab" / "quam_state", 2.0e-5)
    _offline(monkeypatch, tmp_path / "share", delay_s=folder_probe.WAIT_S + 0.4)
    up = HistoryManager(inst, max_snapshots=50, cache_size=3)
    up.claim_history_dir(backup)
    assert up._key_for(backup) != key
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and any(t.is_alive() for t in list(folder_probe._INFLIGHT.values())):
        time.sleep(0.05)
    remembered = {p: v for p, (_t, v) in folder_probe._PROBES.items()
                  if os.path.normcase(p).startswith(os.path.normcase(str(tmp_path / "share")))}
    assert False not in remembered.values(), f"a timeout-length 'not found' was memoised: {remembered}"
    _back(monkeypatch)
    fresh = HistoryManager(inst, max_snapshots=50, cache_size=3)
    assert fresh._key_for(live) == key and len(fresh.list_snapshots(live)) == 3


def test_an_identical_copy_does_not_take_the_dir_of_a_holder_whose_share_is_down(tmp_path, monkeypatch):
    """Content evidence alone cannot tell a move from an identical copy: the
    holder's share must answer before anything is handed over."""
    inst, live, key = _live_chip(tmp_path, n=2)
    unc = r"\\nonexistent-host-r3\share\lab\quam_state"
    hm = HistoryManager(inst, max_snapshots=50, cache_size=3)
    hm._record_owners({key: unc})                               # recorded on the share
    copy = tmp_path / "copy" / "lab" / "quam_state"
    copy.mkdir(parents=True)
    for f in ("state.json", "wiring.json"):
        (copy / f).write_bytes((live / f).read_bytes())         # the same content
    real = pathlib.Path.exists

    def exists(self, *a, **k):
        if str(self).lower().startswith(r"\\nonexistent-host-r3"):
            return False
        return real(self, *a, **k)
    monkeypatch.setattr(pathlib.Path, "exists", exists)
    up = HistoryManager(inst, max_snapshots=50, cache_size=3)
    up.claim_history_dir(copy)
    assert _owners(inst)[key] == unc, _owners(inst)
    assert up._key_for(copy) != key


def test_a_moved_chip_still_keeps_its_history(tmp_path):
    """The other half: a real move (the drive answers, the content is a
    captured one) still hands the dir on."""
    inst, live, key = _live_chip(tmp_path, n=2)
    os.rename(tmp_path / "share", tmp_path / "share_moved")
    folder_probe.forget()
    moved = tmp_path / "share_moved" / "lab" / "quam_state"
    hm = HistoryManager(inst, max_snapshots=50, cache_size=3)
    hm.claim_history_dir(moved)
    assert hm._key_for(moved) == key and len(hm.list_snapshots(moved)) == 2


def test_a_copy_named_while_the_holder_is_offline_records_no_alias_on_a_read(tmp_path, monkeypatch):
    inst, live, key = _live_chip(tmp_path, n=2)
    named = _write(tmp_path / "backup" / "lab" / "quam_state", 2.0e-5, name="copy-r3")
    _offline(monkeypatch, tmp_path / "share")
    up = HistoryManager(inst, max_snapshots=50, cache_size=3)
    up._key_for(named)                                          # read-only resolves
    up.list_snapshots(named)
    doc = json.loads((inst / "history" / "_chip_aliases.json").read_text(encoding="utf-8"))
    assert "copy-r3" not in (doc.get("names") or {}), doc.get("names")
    _back(monkeypatch)
    fresh = HistoryManager(inst, max_snapshots=50, cache_size=3)
    assert fresh._key_for(live) == key and len(fresh.list_snapshots(live)) == 2
