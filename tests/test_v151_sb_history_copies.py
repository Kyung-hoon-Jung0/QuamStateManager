"""v1.5.1 SB-F7: two unnamed copies of one chip whose parent folders share a
name shared ONE history dir (the path candidate is the parent's name, and a
copy has the same fingerprint) -- copy 2's value history listed copy 1's
saves.

Now the first live folder that resolves to a dir holds it; another folder
that still exists gets ``<dir>__<path hash>``. A holder whose folder is gone
(the chip moved) hands the dir on, so a moved chip keeps its history. An
instance upgraded with one chip's history keeps it in place (the first
folder to ask holds the dir). Run folders and SM's own temporary capture
folders file as before; Take live's backup files as its live chip.
"""
from __future__ import annotations

import itertools
import json
import os
import shutil
import time
from pathlib import Path

import pytest

from quam_state_manager.core import safe_io
from quam_state_manager.core.history import HistoryManager
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

_TICK = itertools.count(1)


def _write(folder: Path, t1: float) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    st = {"qubits": {"q1": {"id": "q1", "T1": t1}}, "qubit_pairs": {}}
    (folder / "state.json").write_text(json.dumps(st), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(
        {"wiring": {}, "network": {"host": "10.0.0.1", "cluster_name": "C"}}), encoding="utf-8")
    t = time.time() + 2 * next(_TICK)
    for f in ("state.json", "wiring.json"):
        os.utime(folder / f, (t, t))
    return folder


def _values(hm, path):
    out = []
    for m in hm.list_snapshots(path):
        snap = hm._history_dir(Path(path)) / m.timestamp / "state.json"
        out.append(json.loads(snap.read_text(encoding="utf-8"))["qubits"]["q1"]["T1"])
    return sorted(out)


@pytest.fixture
def hm(tmp_path):
    return HistoryManager(tmp_path / "inst", max_snapshots=50, cache_size=3)


class TestCopiesKeepTheirOwnHistory:
    def test_two_copies_under_same_named_parents_get_two_dirs(self, hm, tmp_path):
        a = _write(tmp_path / "site1" / "chipq" / "quam_state", 1.0e-5)
        b = _write(tmp_path / "site2" / "chipq" / "quam_state", 1.0e-5)
        assert hm.check_and_snapshot(a, "manual", force=True)
        assert hm.check_and_snapshot(b, "manual", force=True)
        _write(a, 1.11e-5)
        assert hm.check_and_snapshot(a, "manual", force=True)
        assert hm._key_for(a) != hm._key_for(b)
        assert _values(hm, b) == [1.0e-5], "copy 2's history lists copy 1's save"
        assert _values(hm, a) == [1.0e-5, 1.11e-5]

    def test_an_upgraded_single_chip_keeps_its_dir_and_history(self, hm, tmp_path):
        a = _write(tmp_path / "site1" / "chipq" / "quam_state", 1.0e-5)
        hm.check_and_snapshot(a, "manual", force=True)
        _write(a, 1.2e-5)
        hm.check_and_snapshot(a, "manual", force=True)
        key = hm._key_for(a)
        # an instance written before this release has no "owners" record
        aliases = hm._alias_path()
        doc = json.loads(aliases.read_text(encoding="utf-8"))
        doc.pop("owners", None)
        safe_io.atomic_write_json(aliases, doc)
        fresh = HistoryManager(tmp_path / "inst", max_snapshots=50, cache_size=3)
        assert fresh._key_for(a) == key
        assert _values(fresh, a) == [1.0e-5, 1.2e-5]
        _write(a, 1.3e-5)
        fresh.check_and_snapshot(a, "manual", force=True)
        assert _values(fresh, a) == [1.0e-5, 1.2e-5, 1.3e-5]
        assert sorted(p.name for p in (tmp_path / "inst" / "history").iterdir()
                      if p.is_dir()) == [key], "the upgrade split one chip's history"

    def test_a_moved_chip_keeps_its_history(self, hm, tmp_path):
        a = _write(tmp_path / "site1" / "chipq" / "quam_state", 1.0e-5)
        hm.check_and_snapshot(a, "manual", force=True)
        key = hm._key_for(a)
        shutil.move(str(tmp_path / "site1"), str(tmp_path / "site9"))
        moved = tmp_path / "site9" / "chipq" / "quam_state"
        assert hm._key_for(moved) == key
        assert _values(hm, moved) == [1.0e-5]

    def test_a_copy_never_takes_over_when_the_first_one_goes(self, hm, tmp_path):
        a = _write(tmp_path / "site1" / "chipq" / "quam_state", 1.0e-5)
        b = _write(tmp_path / "site2" / "chipq" / "quam_state", 2.0e-5)
        hm.check_and_snapshot(a, "manual", force=True)
        hm.check_and_snapshot(b, "manual", force=True)
        kb = hm._key_for(b)
        shutil.rmtree(tmp_path / "site1")
        assert hm._key_for(b) == kb and _values(hm, b) == [2.0e-5]

    def test_run_folders_file_under_their_chip_as_before(self, hm, tmp_path):
        a = _write(tmp_path / "ws" / "chipq" / "quam_state", 1.0e-5)
        hm.check_and_snapshot(a, "manual", force=True)
        run = _write(tmp_path / "ws" / "chipq" / "2026-10-01" / "#12_ramsey_101500" / "quam_state", 1.0e-5)
        assert hm._key_for(run) == hm._key_for(a)


def test_take_live_backup_files_as_its_own_copy(tmp_path):
    a = _write(tmp_path / "site1" / "chipq" / "quam_state", 1.0e-5)
    b = _write(tmp_path / "site2" / "chipq" / "quam_state", 1.0e-5)
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    c.post("/load", data={"folder": str(a)})
    hm = app.config["history_manager"]
    hm.check_and_snapshot(a, "manual", force=True)
    c.post("/load", data={"folder": str(b)})
    c.post("/field/edit", data={"dot_path": "qubits.q1.T1", "value": "3.3e-05"})
    r = c.post("/state/sync", data={"mode": "discard", "force": "1"})
    assert r.status_code == 200, r.data
    backups_b = [m for m in hm.list_snapshots(b) if getattr(m, "kind", None) == "backup"]
    backups_a = [m for m in hm.list_snapshots(a) if getattr(m, "kind", None) == "backup"]
    assert backups_b and not backups_a, (backups_a, backups_b)
