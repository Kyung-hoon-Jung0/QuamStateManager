"""R3B MINOR 7: under Auto-Sync the plot-apply popup reports what the live
write did, not merely that a session is armed.

The row said "applied -- written to the chip" the moment the value was STAGED,
before the auto flush had written anything -- and also when the flush was then
refused (the live chip moved, the working folder was read-only). Now the row
says it is staged and being written, then the real outcome when the flush
answers: written, refused with the reason, or not written yet while the
session merges a moved chip.

Every answer the browser side replays is a REAL answer of the routes below
(field edit, the tray, apply-to-live: landed, stale with the session stopped,
stale with the session merging, a failed save), so the reasons it reads are
the server's own bodies and headers, not invented shapes. The browser side is
``tests/v151_plot_outcome_selfcheck.cjs`` (the REAL app.js + auto-apply.js
under jsdom).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

import quam_state_manager.web.routes as routes_mod
from quam_state_manager.web.app import create_app

ROOT = Path(__file__).resolve().parents[1]
HX = {"HX-Request": "true"}
_WIRING = {"network": {"host": "1.1.1.1", "cluster_name": "C1"}}


def _chip(folder: Path, f01: float = 5.0e9) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps({
        "qubits": {q: {"id": q, "f_01": f01} for q in ("q1", "q2")},
        "qubit_pairs": {}, "active_qubit_names": ["q1", "q2"]}), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")


def _answer(r) -> dict:
    return {"status": r.status_code, "body": r.get_data(as_text=True),
            "trigger": r.headers.get("HX-Trigger", ""),
            "code": r.headers.get("X-SM-Error-Code", "")}


class _Session:
    """One chip, Auto-Sync armed through the real door."""

    def __init__(self, tmp_path: Path, name: str, *, pull: bool = False):
        self.live = tmp_path / name
        _chip(self.live)
        app = create_app(testing=True, instance_path=str(tmp_path / ("_i_" + name)))
        self.c = app.test_client()
        assert self.c.post("/load", data={"folder": str(self.live)}).status_code in (200, 302)
        assert self.c.post("/auto-sync/set", data={
            "pull": "1" if pull else "0", "push": "1"}).status_code == 200

    def tray(self) -> str:
        return self.c.get("/state/tray", headers=HX).get_data(as_text=True)

    def stage(self, q: str, value: str) -> str:
        r = self.c.post("/field/edit", data={"dot_path": f"qubits.{q}.f_01", "value": value},
                        headers=HX)
        body = r.get_json()
        assert r.status_code == 200 and body["ok"], body
        return body["tray_html"]

    def flush(self) -> dict:
        return _answer(self.c.post("/state/apply-to-live", headers=HX))

    def live_f01(self, q: str) -> float:
        return json.loads((self.live / "state.json").read_text(encoding="utf-8"))["qubits"][q]["f_01"]


def _fixture(tmp_path: Path, monkeypatch) -> dict:
    fx: dict = {}
    # Landed: two edits, each written by its own flush.
    s = _Session(tmp_path, "landed")
    fx["tray0"] = s.tray()
    fx["stage1"] = s.stage("q1", "5100000000.0")
    fx["flush1"] = s.flush()
    assert "autoApplyApplied" in fx["flush1"]["trigger"] and s.live_f01("q1") == 5.1e9
    fx["stage2"] = s.stage("q2", "5200000000.0")
    # The first flush's tray as the server renders it once the second edit is
    # in: what a flush answer looks like when an edit landed during it.
    fx["flush1_late"] = dict(fx["flush1"], body=s.tray())
    fx["flush2"] = s.flush()
    assert "autoApplyApplied" in fx["flush2"]["trigger"] and s.live_f01("q2") == 5.2e9
    # One flush that carries both edits (it acquired the state after both).
    s = _Session(tmp_path, "both")
    fx["both_stage1"] = s.stage("q1", "5100000000.0")
    fx["both_stage2"] = s.stage("q2", "5200000000.0")
    fx["both_flush"] = s.flush()
    assert s.live_f01("q1") == 5.1e9 and s.live_f01("q2") == 5.2e9
    # Stale, push only: the session stops.
    s = _Session(tmp_path, "stale_off")
    fx["off_stage"] = s.stage("q1", "5100000000.0")
    _chip(s.live, f01=7.7e9)
    fx["off_flush"] = s.flush()
    assert "autoApplyDisarm" in fx["off_flush"]["trigger"] and s.live_f01("q1") == 7.7e9
    # Stale, pull armed too: the session merges.
    s = _Session(tmp_path, "stale_merge", pull=True)
    fx["merge_stage"] = s.stage("q1", "5100000000.0")
    _chip(s.live, f01=7.7e9)
    fx["merge_flush"] = s.flush()
    assert "autoSyncMerge" in fx["merge_flush"]["trigger"] and s.live_f01("q1") == 7.7e9
    # A save that fails (a read-only working folder).
    s = _Session(tmp_path, "failed")
    fx["fail_stage"] = s.stage("q1", "5100000000.0")

    def _denied(ctx, press):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr(routes_mod, "_press_save", _denied)
    fx["fail_flush"] = s.flush()
    assert fx["fail_flush"]["status"] == 409 and fx["fail_flush"]["code"], fx["fail_flush"]
    assert s.live_f01("q1") == 5.0e9
    return fx


@pytest.mark.skipif(shutil.which("node") is None, reason="node unavailable")
def test_plot_apply_waits_for_its_live_write_result(tmp_path, monkeypatch):
    fx = tmp_path / "fx.json"
    fx.write_text(json.dumps(_fixture(tmp_path, monkeypatch)), encoding="utf-8")
    p = subprocess.run(["node", str(ROOT / "tests" / "v151_plot_outcome_selfcheck.cjs"), str(fx)],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=180)
    if p.returncode == 2 and "jsdom not installed" in p.stderr:
        pytest.skip("jsdom not installed")
    assert p.returncode == 0, p.stdout + p.stderr
