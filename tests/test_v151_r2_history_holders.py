"""v1.5.1 R2 M3 + M4: which chip folder a history dir belongs to.

M3. Two unnamed copies under same-named parents got two dirs (SB-F7) -- but
naming copy 2 (the identity banner's own flow) made tier 1 adopt the
parent-name dir, which holds copy 1's history: copy 2's own history was no
longer shown and copy 1's saves were listed as copy 2's again.

M4. After the upgrade an instance records no holder, and "the first folder to
ask holds the dir" -- even a read-only lookup. A backup copy under a
same-named parent resolved first took the live chip's dir, and the live chip
got an empty one: its Param History, Trends and value history lost its past.

Now a dir with no recorded holder belongs to the folder its snapshots were
captured from (the majority; the newest capture breaks a tie), ownership is
recorded only by a live capture or an explicit open, and tier 1 starts from
the folder's own dir and never adopts a dir another existing folder holds.
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
#: an instance shaped like a real one: a mixed dir of an unnamed chip (two
#: copies under same-named parents, before v1.5.1), its alias registry
RIG = Path(r"D:\work\sm_qa_rigs\v141\upr\S\SB\rig\instance\history")


def _write(folder: Path, t1: float, name: str | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    st = {"qubits": {"q1": {"id": "q1", "T1": t1}}, "qubit_pairs": {}}
    if name:
        st["extras"] = {"chip_name": name}
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


def _forget_holders(hm):
    """An instance written before v1.5.1: no holder recorded."""
    doc = json.loads(hm._alias_path().read_text(encoding="utf-8"))
    doc.pop("owners", None)
    safe_io.atomic_write_json(hm._alias_path(), doc)


def _owners(inst: Path) -> dict:
    p = inst / "history" / "_chip_aliases.json"
    return (json.loads(p.read_text(encoding="utf-8")).get("owners") or {}) if p.exists() else {}


class TestNamingACopyKeepsItsOwnHistory:
    def test_naming_copy_two_does_not_merge_it_into_copy_one(self, tmp_path):
        hm = HistoryManager(tmp_path / "inst", max_snapshots=50, cache_size=3)
        a = _write(tmp_path / "site1" / "chipq" / "quam_state", 1.0e-5)
        b = _write(tmp_path / "site2" / "chipq" / "quam_state", 2.0e-5)
        hm.check_and_snapshot(a, "manual", force=True)
        hm.check_and_snapshot(b, "manual", force=True)
        _write(a, 1.11e-5)
        hm.check_and_snapshot(a, "manual", force=True)
        own_b = hm._key_for(b)
        assert own_b != hm._key_for(a)
        _write(b, 2.22e-5, name="copy-two")            # the identity banner's flow
        hm2 = HistoryManager(tmp_path / "inst", max_snapshots=50, cache_size=3)
        hm2.check_and_snapshot(b, "manual", force=True)
        assert hm2._key_for(b) == own_b, "the named copy left its own history"
        assert _values(hm2, b) == [2.0e-5, 2.22e-5], _values(hm2, b)
        assert _values(hm2, a) == [1.0e-5, 1.11e-5], "copy 1's history lists copy 2's save"

    def test_a_copy_named_before_its_first_capture_never_adopts_the_other(self, tmp_path):
        hm = HistoryManager(tmp_path / "inst", max_snapshots=50, cache_size=3)
        a = _write(tmp_path / "site1" / "chipq" / "quam_state", 1.0e-5)
        hm.check_and_snapshot(a, "manual", force=True)
        b = _write(tmp_path / "site2" / "chipq" / "quam_state", 2.0e-5, name="copy-two")
        hm.check_and_snapshot(b, "manual", force=True)
        assert hm._key_for(b) != hm._key_for(a)
        assert _values(hm, b) == [2.0e-5] and _values(hm, a) == [1.0e-5]


class TestAnUpgradedInstanceKeepsTheLiveChipsHistory:
    def _upgraded(self, tmp_path):
        inst = tmp_path / "inst"
        hm = HistoryManager(inst, max_snapshots=50, cache_size=3)
        live = tmp_path / "data" / "lab" / "quam_state"
        for i in range(5):
            _write(live, 1.0e-5 + i * 1e-7)
            hm.check_and_snapshot(live, "manual", force=True)
        backup = _write(tmp_path / "backup" / "lab" / "quam_state", 2.0e-5)
        key = hm._key_for(live)
        _forget_holders(hm)
        return inst, live, backup, key

    def test_a_backup_resolved_first_does_not_take_it(self, tmp_path):
        inst, live, backup, key = self._upgraded(tmp_path)
        up = HistoryManager(inst, max_snapshots=50, cache_size=3)
        assert up._key_for(backup) != key
        assert up._key_for(live) == key and len(up.list_snapshots(live)) == 5

    def test_a_backup_opened_or_captured_first_does_not_take_it(self, tmp_path):
        inst, live, backup, key = self._upgraded(tmp_path)
        up = HistoryManager(inst, max_snapshots=50, cache_size=3)
        up.claim_history_dir(backup)                      # opened first
        up.check_and_snapshot(backup, "manual", force=True)
        up2 = HistoryManager(inst, max_snapshots=50, cache_size=3)
        assert up2._key_for(live) == key and len(up2.list_snapshots(live)) == 5
        assert up2._key_for(backup) != key and len(up2.list_snapshots(backup)) == 1

    def test_a_read_only_resolve_records_no_holder(self, tmp_path):
        inst, live, backup, key = self._upgraded(tmp_path)
        up = HistoryManager(inst, max_snapshots=50, cache_size=3)
        up._key_for(backup)
        up.list_snapshots(backup)
        up._key_for(live)
        up.list_snapshots(live)
        assert _owners(inst) == {}, _owners(inst)
        up.claim_history_dir(live)
        assert list(_owners(inst).values()) and key in _owners(inst)

    def test_opening_the_chip_records_its_holder(self, tmp_path):
        inst, live, backup, key = self._upgraded(tmp_path)
        routes._quam_cache.clear()
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst_app"))
        hm = HistoryManager(inst, max_snapshots=50, cache_size=3)
        app.config["history_manager"] = hm
        c = app.test_client()
        assert c.post("/load", data={"folder": str(backup)}).status_code in (200, 302)
        assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
        owners = _owners(inst)
        assert owners.get(key) and "backup" not in owners[key], owners
        assert hm._key_for(live) == key and len(hm.list_snapshots(live)) == 5


@pytest.mark.skipif(not (RIG / "lab").is_dir(), reason="the real-shaped rig instance is absent")
def test_a_real_shaped_instance_keeps_the_live_chips_mixed_dir(tmp_path):
    """Pieces of a real instance (copied, never used in place): a dir of an
    unnamed chip holding captures of two copies under same-named parents
    (two from the live copy, one from the backup), and its alias registry
    with no holder recorded. The backup is resolved -- and opened -- first."""
    inst = tmp_path / "inst"
    hist = inst / "history"
    hist.mkdir(parents=True)
    shutil.copy2(RIG / "_chip_aliases.json", hist / "_chip_aliases.json")
    doc = json.loads((hist / "_chip_aliases.json").read_text(encoding="utf-8"))
    doc.pop("owners", None)
    doc["paths"] = {}
    safe_io.atomic_write_json(hist / "_chip_aliases.json", doc)
    live = tmp_path / "data" / "one" / "lab" / "chip"
    backup = tmp_path / "data" / "two" / "lab" / "chip"
    src_dir = RIG / "lab"
    snaps = sorted(d for d in src_dir.iterdir() if d.is_dir() and (d / "meta.json").exists())
    assert len(snaps) >= 3
    by_folder = {}
    for d in snaps:
        shutil.copytree(d, hist / "lab" / d.name)
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        who = live if "\\one\\" in meta["source_path"] or "/one/" in meta["source_path"] else backup
        meta["source_path"] = str(who)
        safe_io.atomic_write_json(hist / "lab" / d.name / "meta.json", meta)
        by_folder.setdefault(str(who), []).append(d.name)
    assert len(by_folder[str(live)]) > len(by_folder[str(backup)]), by_folder
    newest = snaps[-1]
    for folder in (live, backup):
        folder.mkdir(parents=True)
        for f in ("state.json", "wiring.json"):
            shutil.copy2(newest / f, folder / f)
    hm = HistoryManager(inst, max_snapshots=50, cache_size=3)
    assert hm._key_for(backup) != "lab"
    hm.claim_history_dir(backup)
    fresh = HistoryManager(inst, max_snapshots=50, cache_size=3)
    assert fresh._key_for(live) == "lab"
    assert len(fresh.list_snapshots(live)) == len(snaps), "the live chip lost its history"
    assert fresh._key_for(backup) != "lab"
