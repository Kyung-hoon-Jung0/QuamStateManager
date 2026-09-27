"""w7/livewrite: the live-write fast paths write what the slow paths wrote.

Apply over a live chip that has not moved since the last sync skips the pull
(sync_from_live + store rebuild + replay) -- that is only allowed because the
pull would reproduce the content the store already holds. Pinned by running
the same press both ways on two byte-identical chips and comparing every file
the press wrote, byte for byte, plus the response the page acts on.
"""
import json
import shutil
from pathlib import Path

import pytest

from quam_state_manager.core import working_copy
from quam_state_manager.web.app import create_app


def _state() -> dict:
    return {
        "qubits": {
            "qA1": {"T1": 1e-5, "T2": 2e-5, "f_01": 4.8e9, "name": "qA1",
                    "xy": {"operations": {"x180": {"amplitude": 0.1, "length": 40},
                                          "x90": "#../x180"}}},
            "qA2": {"T1": 3e-5, "f_01": 5.1e9, "tags": [1, 2.5, None]},
        },
        "active_qubit_names": ["qA1", "qA2"],
    }


def _chip(root: Path) -> Path:
    folder = root / "chip"
    folder.mkdir(parents=True)
    # 2-space indent, no trailing newline: SM must keep the file's own format
    (folder / "state.json").write_text(json.dumps(_state(), indent=2), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps({"wiring": {"qA1": {"xy": "#/ports/1"}}}, indent=2),
                                        encoding="utf-8")
    return folder


def _client(root: Path):
    folder = _chip(root)
    app = create_app(testing=True, instance_path=str(root / "_inst"))
    c = app.test_client()
    r = c.post("/load", data={"folder": str(folder)})
    assert r.status_code in (200, 302), r.data[:300]
    return c, folder, root / "_inst" / "working_state"


def _files(folder: Path, ws: Path) -> dict:
    wdir = next(d for d in ws.iterdir() if d.is_dir() and (d / "state.json").exists())
    return {"live_state": (folder / "state.json").read_bytes(),
            "live_wiring": (folder / "wiring.json").read_bytes(),
            "wc_state": (wdir / "state.json").read_bytes(),
            "wc_wiring": (wdir / "wiring.json").read_bytes()}


def _press(c, *, slow=False):
    for path, val in (("qubits.qA1.f_01", "4.85e9"), ("qubits.qA2.T1", "3.5e-5")):
        r = c.post("/field/edit", data={"dot_path": path, "value": val})
        assert r.status_code == 200, r.data[:200]
    data = {"mode": "apply", "check_collisions": "1"}
    if slow:
        # a per-field pick (on a path nobody edited, so it reshapes nothing)
        # routes the press down the pull path, exactly as the old code ran it
        data["picks"] = json.dumps({"zz.not_a_field": "mine"})
    return c.post("/state/sync", data=data).get_json()


@pytest.mark.parametrize("presses", [1, 2])
def test_apply_over_an_unmoved_live_skips_the_pull_and_writes_the_same_bytes(tmp_path, monkeypatch, presses):
    fast_c, fast_live, fast_ws = _client(tmp_path / "fast")
    slow_c, slow_live, slow_ws = _client(tmp_path / "slow")
    pulls = {"n": 0}
    real_sync = working_copy.sync_from_live

    def counting_sync(wc):
        pulls["n"] += 1
        return real_sync(wc)
    monkeypatch.setattr(working_copy, "sync_from_live", counting_sync)

    for _ in range(presses):
        pulls["n"] = 0
        d_fast = _press(fast_c)
        assert pulls["n"] == 0, "the unmoved-live apply still pulled"
        d_slow = _press(slow_c, slow=True)
        assert pulls["n"] == 1, "the forced slow path did not pull"
        for k in ("status", "mode", "replay", "pulled_other_changes", "changes", "structural"):
            assert d_fast.get(k) == d_slow.get(k), (k, d_fast.get(k), d_slow.get(k))
        assert _files(fast_live, fast_ws) == _files(slow_live, slow_ws)
    assert json.loads(_files(fast_live, fast_ws)["live_state"])["qubits"]["qA1"]["f_01"] == 4.85e9


def test_a_moved_live_still_pulls(tmp_path, monkeypatch):
    c, folder, _ws = _client(tmp_path)
    st = json.loads((folder / "state.json").read_text(encoding="utf-8"))
    st["qubits"]["qA2"]["f_01"] = 5.3e9                  # an experiment wrote
    (folder / "state.json").write_text(json.dumps(st), encoding="utf-8")
    pulls = {"n": 0}
    real_sync = working_copy.sync_from_live

    def counting_sync(wc):
        pulls["n"] += 1
        return real_sync(wc)
    monkeypatch.setattr(working_copy, "sync_from_live", counting_sync)
    d = _press(c)
    assert d["status"] == "ok" and pulls["n"] == 1 and d["pulled_other_changes"] is True
    live = json.loads((folder / "state.json").read_text(encoding="utf-8"))
    assert live["qubits"]["qA2"]["f_01"] == 5.3e9 and live["qubits"]["qA1"]["f_01"] == 4.85e9


def _resave(folder: Path, how: str) -> None:
    """A content-identical rewrite of live from outside SM: the content hash
    stays the sync point's, the mtimes move (a node's machine.save(), a touch)."""
    import os
    import time
    for name in ("state.json", "wiring.json"):
        p = folder / name
        if how == "reformat":
            p.write_text(json.dumps(json.loads(p.read_text(encoding="utf-8")), indent=4),
                         encoding="utf-8")
        t = time.time() + 5
        os.utime(p, (t, t))


@pytest.mark.parametrize("how", ["touch", "reformat"])
def test_a_content_identical_resave_of_live_then_edit_then_apply_writes(tmp_path, monkeypatch, how):
    """Verifier D1: the fast path was gated on the content hash alone, so after
    a same-content re-save it skipped the pull that re-anchors the sync point's
    mtimes and apply_to_live refused (status 'conflict', live unwritten). The
    press must write, byte-identical to the pull path (integ/w7's only path)."""
    fast_c, fast_live, fast_ws = _client(tmp_path / "fast")
    slow_c, slow_live, slow_ws = _client(tmp_path / "slow")
    # one applied round first, so the sync point is SM's own write
    assert _press(fast_c)["status"] == "ok" and _press(slow_c, slow=True)["status"] == "ok"
    _resave(fast_live, how)
    _resave(slow_live, how)
    for c, path in ((fast_c, fast_live), (slow_c, slow_live)):
        r = c.post("/field/edit", data={"dot_path": "qubits.qA1.T1", "value": "1.5e-5"})
        assert r.status_code == 200, r.data[:200]
    d_fast = fast_c.post("/state/sync", data={"mode": "apply", "check_collisions": "1"}).get_json()
    d_slow = slow_c.post("/state/sync", data={"mode": "apply", "check_collisions": "1",
                                              "picks": json.dumps({"zz.not_a_field": "mine"})}).get_json()
    assert d_fast["status"] == "ok", d_fast
    for k in ("status", "mode", "replay", "pulled_other_changes"):
        assert d_fast.get(k) == d_slow.get(k), (k, d_fast.get(k), d_slow.get(k))
    assert _files(fast_live, fast_ws) == _files(slow_live, slow_ws)
    assert json.loads((fast_live / "state.json").read_text(encoding="utf-8"))["qubits"]["qA1"]["T1"] == 1.5e-5
