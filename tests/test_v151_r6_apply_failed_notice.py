"""v1.5.1 (r6 P2-6): the red "Apply failed" notice goes once a later live
write lands, and stays while none has.

The notice of a refused live write ("Apply failed: the live folder refused
the write ... Your edits are kept.") stayed after the folder was writable
again and a later Apply had landed, and followed every page after that. Now
every landed live write stamps when (``_live_write_landed``), the tray
carries the stamp, and a failure notice carries the stamp as of its failure;
a tray newer than the notice takes it down.

Every answer the browser side replays is a REAL answer of the routes (a
landed Apply, the refused Apply through both doors on a read-only live
folder, a tray re-read with nothing landed, the Apply that lands after the
folder is writable again); the browser side is
``tests/v151_r6_apply_failed_notice_selfcheck.cjs`` (the REAL app.js under
jsdom: its doStateSync, showToast and tray swap).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import sys
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

ROOT = Path(__file__).resolve().parents[1]
HX = {"HX-Request": "true"}
_WIRING = {"network": {"host": "127.0.0.1", "cluster_name": "C1"}}


def _write_chip(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps({
        "qubits": {"qA1": {"id": "qA1", "f_01": 5.0e9, "z": {"joint_offset": 0.08}}},
        "qubit_pairs": {}, "active_qubit_names": ["qA1"]}), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")


def _set_readonly(folder: Path, on: bool) -> None:
    for name in ("state.json", "wiring.json"):
        os.chmod(folder / name, stat.S_IREAD if on else (stat.S_IREAD | stat.S_IWRITE))
    _set_dir_readonly(folder, on)


def _set_dir_readonly(folder: Path, on: bool) -> None:
    """On POSIX a read-only FILE is still replaced by rename; the refusal is
    real only when the containing directory denies new entries too. Windows
    already refuses ReplaceFileW on a read-only file, and its directory
    attribute is not enforced, so the directory is left alone there."""
    if sys.platform != "win32" and folder.is_dir():
        os.chmod(folder, 0o555 if on else 0o755)



def _stamp(tray_html: str) -> int:
    m = re.search(r'id="pending-tray"[^>]*?data-live-landed="(\d+)"', tray_html, re.S)
    assert m, "the tray carries when a live write last landed"
    return int(m.group(1))


def _edit(c, value: str) -> None:
    r = c.post("/field/edit", data={"dot_path": "qubits.qA1.z.joint_offset", "value": value})
    assert r.status_code == 200 and r.get_json()["ok"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node unavailable")
def test_the_apply_failed_notice_goes_when_a_later_write_lands(tmp_path):
    live = tmp_path / "chips" / "live"
    _write_chip(live)
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    try:
        assert _stamp(c.get("/state/tray", headers=HX).get_data(as_text=True)) == 0, "nothing landed yet"
        assert 'data-live-landed="0"' in c.get("/qubits").get_data(as_text=True), "the full-page tray too"
        # a first Apply that lands
        _edit(c, "0.09")
        ok0 = c.post("/state/sync", data={"mode": "apply"}, headers=HX).get_json()
        assert ok0["status"] == "ok", ok0
        landed0 = _stamp(ok0["tray_html"])
        assert landed0 > 0
        # the live folder turns read-only: the next Apply is refused, through both doors
        _edit(c, "0.10")
        _set_readonly(live, True)
        r = c.post("/state/sync", data={"mode": "apply"}, headers=HX)
        assert r.status_code == 409
        fail = r.get_json()
        assert fail["error_code"] == "live_write_failed" and "refused the write" in fail["message"], fail
        assert fail["live_landed"] == landed0 == _stamp(fail["tray_html"])
        tray_nothing = c.get("/state/tray", headers=HX).get_data(as_text=True)
        assert _stamp(tray_nothing) == landed0, "a re-read with nothing landed keeps the stamp"
        r = c.post("/state/apply-to-live", headers=HX)
        assert r.status_code == 409 and r.headers.get("X-SM-Error-Code") == "live_write_failed"
        assert r.headers.get("X-SM-Live-Landed") == str(landed0)
        fail_htmx = r.get_data(as_text=True)
        assert f'data-live-write-failed="{landed0}"' in fail_htmx, "the server-rendered notice carries it"
        # writable again: the next Apply lands
        _set_readonly(live, False)
        ok1 = c.post("/state/sync", data={"mode": "apply"}, headers=HX).get_json()
        assert ok1["status"] == "ok", ok1
        assert _stamp(ok1["tray_html"]) > landed0
    finally:
        _set_readonly(live, False)
    fx = tmp_path / "fx.json"
    fx.write_text(json.dumps({
        "tray0": ok0["tray_html"], "fail": fail, "tray_nothing": tray_nothing,
        "fail_htmx": fail_htmx, "fail_htmx_landed": str(landed0), "ok1": ok1}), encoding="utf-8")
    p = subprocess.run(["node", str(ROOT / "tests" / "v151_r6_apply_failed_notice_selfcheck.cjs"), str(fx)],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=180)
    if p.returncode == 2 and "jsdom not installed" in p.stderr:
        pytest.skip("jsdom not installed")
    assert p.returncode == 0, p.stdout + p.stderr
