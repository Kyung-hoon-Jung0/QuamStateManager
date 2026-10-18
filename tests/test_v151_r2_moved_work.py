"""v1.5.1 R2 M5 + MINOR 7: unapplied work of a folder that cannot be found.

M5. "Gone" was decided by ``Path(old).exists()`` when another chip of the same
design was first opened. A folder only out of reach for a while (a share
reconnecting, a USB disk, a parent renamed for a minute) had its staged rows
handed to that chip and its checkpoint archived: when the original was back
and opened, its tray was empty and nothing said where the rows went. The
probe itself took 5-42 s on an unreachable UNC path, for every stale copy on
every fresh build, under the chip's build lock.

Now the work is COPIED, only on evidence of a move (the new folder holds the
old copy's synced content); the original keeps its checkpoint and is told on
its next open; the offer is recorded under both keys; the probes are
parallel, bounded and memoised per process.

MINOR 7. Checkpoints an older GC left without their working copy (no
meta.json) were never offered.
"""
from __future__ import annotations

import json
import os
import pathlib
import time
from pathlib import Path

import pytest

from quam_state_manager.core import moved_work, pending_tray, working_copy
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

_WIRING = {"network": {"host": "10.1.1.1", "cluster_name": "C"}}


def _state(**t1):
    qs = {f"q{i}": {"id": f"q{i}", "T1": 1.0e-5 * i} for i in range(1, 4)}
    for q, v in t1.items():
        qs[q]["T1"] = v
    return {"qubits": qs, "qubit_pairs": {}, "active_qubit_names": sorted(qs)}


def _chip(folder: Path, state=None):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state or _state()), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")


_APPS: dict = {}


def _open(inst, folder):
    """Open *folder* in the SM of this instance, its open chips forgotten (a
    restart; one app per instance -- see test_v151_sb_moved_chip)."""
    routes._quam_cache.clear()
    app = _APPS.get(str(inst))
    if app is None:
        app = _APPS[str(inst)] = create_app(testing=True, instance_path=str(inst))
    app.config["contexts"].clear()
    app.config["active_context"] = None
    c = app.test_client()
    assert c.post("/load", data={"folder": str(folder)}).status_code in (200, 302)
    return app, c, app.config["contexts"][app.config["active_context"]]


def _listed(ctx):
    return [[r["path"] for r in d["rows"]] for d in pending_tray.unrecovered(ctx["working_copy"])]


def _note(ctx):
    n = ctx.get("_tray_recovery_notice")
    return (n or {}).get("text", "") if isinstance(n, dict) else (n or "")


@pytest.fixture(autouse=True)
def _fresh_probes():
    try:
        from quam_state_manager.core import folder_probe
    except ImportError:                        # before R2: nothing remembered
        yield
        return
    folder_probe.forget()
    yield
    folder_probe.forget()


@pytest.fixture
def staged_x(tmp_path):
    inst = tmp_path / "_inst"
    x = tmp_path / "share" / "chipX" / "quam_state"
    y = tmp_path / "local" / "chipY" / "quam_state"
    _chip(x)
    app, c, ctx = _open(inst, x)
    assert c.post("/field/edit", data={"dot_path": "qubits.q1.T1", "value": "1.5e-05"}).status_code == 200
    pending_tray.flush_all()
    return {"inst": inst, "x": x, "y": y, "tmp": tmp_path, "x_key": ctx["working_copy"].key}


class TestAnOriginalOnlyOutOfReach:
    def test_keeps_its_rows_and_is_told_where_the_copy_went(self, staged_x):
        t = staged_x
        _chip(t["y"])                                   # a same-design copy, same content
        os.replace(t["tmp"] / "share", t["tmp"] / "share_offline")
        _app, _c, cy = _open(t["inst"], t["y"])
        assert _listed(cy) == [["qubits.q1.T1"]], _listed(cy)
        assert "could not be found" in _note(cy) and "not there any more" not in _note(cy)
        os.replace(t["tmp"] / "share_offline", t["tmp"] / "share")
        _app, _c, cx = _open(t["inst"], t["x"])
        assert [e.dot_path for e in cx["store"].change_log] == ["qubits.q1.T1"], \
            "the original's staged edit left it"
        assert str(t["y"]) in _note(cx), _note(cx)

    def test_the_offer_is_recorded_under_both_keys_and_made_once(self, staged_x):
        t = staged_x
        _chip(t["y"])
        os.replace(t["tmp"] / "share", t["tmp"] / "share_offline")
        _app, _c, cy = _open(t["inst"], t["y"])
        root = working_copy.working_state_root(t["inst"])
        rec = json.loads((root / f"{t['x_key']}.pending_tray.moved-offer.json").read_text(encoding="utf-8"))
        assert cy["working_copy"].key in rec["to"], rec
        listed = pending_tray.unrecovered(cy["working_copy"])
        doc = json.loads(Path(listed[0]["file"]).read_text(encoding="utf-8"))
        assert doc["moved_from"]["key"] == t["x_key"], doc.get("moved_from")
        assert (root / f"{t['x_key']}.pending_tray.json").exists(), "the original's checkpoint moved"
        _app, _c, cy = _open(t["inst"], t["y"])
        assert len(pending_tray.unrecovered(cy["working_copy"])) == 1


class TestNoEvidenceNoMove:
    def test_a_copy_with_other_content_gets_nothing_and_the_original_keeps_all(self, staged_x):
        t = staged_x
        _chip(t["y"], _state(q2=9.9e-06))               # not the content X was synced with
        os.replace(t["tmp"] / "share", t["tmp"] / "share_offline")
        _app, _c, cy = _open(t["inst"], t["y"])
        assert _listed(cy) == [], _listed(cy)
        assert "content differs" in _note(cy), _note(cy)
        root = working_copy.working_state_root(t["inst"])
        assert (root / f"{t['x_key']}.pending_tray.json").exists()
        assert not list(root.glob(f"{t['x_key']}.pending_tray.moved-*.json")) or \
            all(p.name.endswith("moved-offer.json") for p in root.glob(f"{t['x_key']}.pending_tray.moved-*"))
        os.replace(t["tmp"] / "share_offline", t["tmp"] / "share")
        _app, _c, cx = _open(t["inst"], t["x"])
        assert [e.dot_path for e in cx["store"].change_log] == ["qubits.q1.T1"]


class TestTheProbeIsBounded:
    def test_a_slow_folder_never_holds_an_open_for_its_full_timeout(self, staged_x, monkeypatch):
        t = staged_x
        _chip(t["y"])
        slow = {os.path.normcase(str(t["x"])), os.path.normcase(str(t["x"].resolve()))}
        real = pathlib.Path.exists

        def exists(self, *a, **k):
            if os.path.normcase(str(self)) in slow:
                time.sleep(4.0)                         # an unreachable share
                return False
            return real(self, *a, **k)
        state = json.loads((t["y"] / "state.json").read_text(encoding="utf-8"))
        wiring = json.loads((t["y"] / "wiring.json").read_text(encoding="utf-8"))
        monkeypatch.setattr(pathlib.Path, "exists", exists)
        t0 = time.monotonic()
        found = moved_work.find(t["inst"], t["y"], state, wiring)
        took = time.monotonic() - t0
        assert took < 2.5, f"the probe held the open for {took:.1f} s"
        assert found == [], "an unanswered probe counted as gone"
        t0 = time.monotonic()
        moved_work.find(t["inst"], t["y"], state, wiring)       # the same open again
        assert time.monotonic() - t0 < 2.5


class TestLegacyOrphanedCheckpoints:
    def test_a_checkpoint_left_without_its_copy_is_offered(self, staged_x):
        t = staged_x
        root = working_copy.working_state_root(t["inst"])
        # what the older GC did: the copy and its meta gone, the checkpoint left
        import shutil
        shutil.rmtree(root / t["x_key"])
        (root / f"{t['x_key']}.meta.json").unlink()
        assert (root / f"{t['x_key']}.pending_tray.json").exists()
        _chip(t["y"])
        os.replace(t["tmp"] / "share", t["tmp"] / "share_gone")
        _app, _c, cy = _open(t["inst"], t["y"])
        assert _listed(cy) == [["qubits.q1.T1"]], _listed(cy)
        assert str(t["x"]) in _note(cy), _note(cy)
