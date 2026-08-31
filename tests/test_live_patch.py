"""Sync pull → in-place patch (customer 2026-08-27, critical): pressing Sync
used to re-fetch the whole page (an 8.8 MB /bulk on the 20Q chip: a freeze,
then the grid back at its first-click view). The pull now reports every leaf
it changed so the client rewrites just those; a shape change still refreshes.
Route pins here; the client is pinned by live_patch_selfcheck.cjs."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

_ROOT = Path(__file__).resolve().parent.parent


def _state() -> dict:
    return {
        "qubits": {
            "qA1": {"T1": 1e-5, "T2": 2e-5, "f_01": 4.8e9,
                    "xy": {"operations": {"x180": {"amplitude": 0.1, "length": 40}}}},
            "qA2": {"T1": 3e-5, "f_01": 5.1e9},
        },
        "active_qubit_names": ["qA1", "qA2"],
    }


@pytest.fixture
def live(tmp_path):
    (tmp_path / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (tmp_path / "wiring.json").write_text("{}", encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_app_instance"))
    c = app.test_client()
    r = c.post("/load", data={"folder": str(tmp_path)})
    assert r.status_code in (200, 302), r.data[:300]
    return c, tmp_path


def _rewrite_live(folder: Path, mutate):
    st = json.loads((folder / "state.json").read_text(encoding="utf-8"))
    mutate(st)
    (folder / "state.json").write_text(json.dumps(st), encoding="utf-8")


def test_pull_names_every_changed_leaf_and_nothing_else(live):
    c, folder = live

    def mutate(st):
        st["qubits"]["qA1"]["T1"] = 1.5e-5
        st["qubits"]["qA1"]["xy"]["operations"]["x180"]["amplitude"] = 0.2
    _rewrite_live(folder, mutate)
    d = c.post("/state/sync", data={"mode": "discard"}).get_json()
    assert d["status"] == "ok", d
    assert d["structural"] is False
    by = {e["dot_path"]: e for e in d["changes"]}
    assert set(by) == {"qubits.qA1.T1", "qubits.qA1.xy.operations.x180.amplitude"}
    e = by["qubits.qA1.T1"]
    # the undo-repaint payload shape, verbatim, plus the raw value for the tree
    assert e["old_kind"] == "num" and e["value"] == 1.5e-5
    assert e["old_value_disp"] and e["old_value_str"]


def test_added_or_removed_key_is_structural(live):
    c, folder = live
    _rewrite_live(folder, lambda st: st["qubits"]["qA2"].__setitem__("T2echo", 4e-5))
    d = c.post("/state/sync", data={"mode": "discard"}).get_json()
    assert d["status"] == "ok" and d["structural"] is True
    _rewrite_live(folder, lambda st: st["qubits"]["qA2"].pop("T1"))
    d = c.post("/state/sync", data={"mode": "discard"}).get_json()
    assert d["structural"] is True


def test_unchanged_live_yields_an_empty_patch(live):
    c, _ = live
    d = c.post("/state/sync", data={"mode": "discard"}).get_json()
    assert d["status"] == "ok" and d["changes"] == [] and d["structural"] is False


def test_apply_mode_carries_the_patch_too(live):
    """Apply from the grid is doStateSync('apply'): the pull absorbs what an
    experiment wrote meanwhile, and THOSE leaves must patch in place too."""
    c, folder = live
    r = c.post("/field/edit", data={"dot_path": "qubits.qA2.f_01", "value": "5.2e9"})
    assert r.status_code == 200, r.data[:200]
    _rewrite_live(folder, lambda st: st["qubits"]["qA1"].__setitem__("T1", 1.7e-5))
    d = c.post("/state/sync", data={"mode": "apply"}).get_json()
    assert d["status"] == "ok" and d["mode"] == "apply", d
    assert d["structural"] is False
    paths = {e["dot_path"] for e in d["changes"]}
    assert "qubits.qA1.T1" in paths


def _snapshot_now(folder):
    """A real on-disk snapshot of the CURRENT live content, via the app's own
    storage layout (a separate HistoryManager instance over the same
    instance dir -- routes read from disk, so this is what they see)."""
    from quam_state_manager.core.history import HistoryManager
    hm = HistoryManager(folder / "_app_instance")
    hm.check_and_snapshot(folder, "manual", force=True)
    return hm.list_snapshots(folder)[0].timestamp


def test_state_restored_trigger_names_what_a_stage_changes(live):
    """docs/144: the wholesale-replace routes (State History stage here) ship
    their leaf patch in the stateRestored HX-Trigger detail, so the client
    patches the visible pane instead of resetting it."""
    import json as _json
    c, folder = live
    ts = _snapshot_now(folder)
    r = c.post("/field/edit", data={"dot_path": "qubits.qA1.T1", "value": "9e-5"})
    assert r.status_code == 200, r.data[:200]
    r = c.post(f"/state-history/{ts}/stage?force=1")
    assert r.status_code == 200, r.data[:300]
    trig = _json.loads(r.headers["HX-Trigger"])
    sr = trig["stateRestored"]
    assert sr["structural"] is False
    changed = {e["dot_path"]: e["value"] for e in sr["changes"]}
    assert changed.get("qubits.qA1.T1") == 1e-5      # back to the snapshot
    assert "pulses-changed" in trig and "diagnostics-changed" in trig


def test_state_restored_header_cap_falls_back_to_structural(live, monkeypatch):
    """A patch too big for a response header degrades to the wholesale path
    (structural), never a megabyte HX-Trigger."""
    import json as _json
    from quam_state_manager.web import routes as routes_mod
    c, folder = live
    ts = _snapshot_now(folder)
    r = c.post("/field/edit", data={"dot_path": "qubits.qA1.T1", "value": "9e-5"})
    assert r.status_code == 200
    monkeypatch.setattr(routes_mod, "_HEADER_PATCH_CAP", 0)
    r = c.post(f"/state-history/{ts}/stage?force=1")
    assert r.status_code == 200
    sr = _json.loads(r.headers["HX-Trigger"])["stateRestored"]
    assert sr["structural"] is True and sr["changes"] == []


def test_dataset_apply_to_chip_and_auto_sync_share_the_trigger(live):
    """The other bracketed emitters go through the same helper -- pin the
    helper's shape directly (extra keys merge, bare routes unaffected)."""
    import json as _json
    from quam_state_manager.web import routes as routes_mod
    c, folder = live
    with c.application.app_context(), c.application.test_request_context():
        ctx = c.application.config["contexts"][
            c.application.config["active_context"]]
        pre = routes_mod._leaf_snapshot(ctx)
        trig = _json.loads(routes_mod._state_restored_trigger(
            ctx, pre, extra={"autoSyncPulled": {"replaced": True}}))
    assert trig["stateRestored"] == {"structural": False, "changes": []}
    assert trig["autoSyncPulled"] == {"replaced": True}


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_live_patch_selfcheck():
    node = shutil.which("node")
    if subprocess.run([node, "-e", "require('jsdom')"], capture_output=True, cwd=str(_ROOT)).returncode != 0:
        pytest.skip("jsdom not installed for node")
    res = subprocess.run([node, str(_ROOT / "tests" / "live_patch_selfcheck.cjs")],
                         capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT))
    assert res.returncode == 0, res.stdout + "\n" + res.stderr
    assert "ok - non-structural sync patches in place" in res.stdout
