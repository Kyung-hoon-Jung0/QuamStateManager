"""v1.5.1 R3 M5: the "nothing pending" refusal names a staged version.

Window A shows its edit; window B stages an older version (State History
Load, with its own discard confirm). A's Apply is refused because the rows A
saw are gone. The refusal said "your edits were saved into the working state
-- press again", which was false: the STAGE set working_dirty, and pressing
again wrote B's staged version without A's edit. It now names the staged
version and says Apply would write that, not A's edits.
"""
from __future__ import annotations

import json
import os
import re
import time

from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

WIRING = {"network": {"host": "127.0.0.1", "cluster_name": "C1"}}
HX = {"HX-Request": "true"}


def _st(t1_q1, t1_q2):
    return {"qubits": {"q1": {"id": "q1", "T1": t1_q1}, "q2": {"id": "q2", "T1": t1_q2}},
            "qubit_pairs": {}, "active_qubit_names": ["q1", "q2"]}


def _seen(c):
    tray = c.get("/state/tray", headers=HX).get_data(as_text=True)
    n = re.search(r'data-change-count="(\d+)"', tray).group(1)
    s = (re.search(r'data-change-sig="([0-9a-f]*)"', tray) or [None, ""])[1]
    return {"seen_changes": n, "seen_sig": s}


def _chip(tmp_path):
    live = tmp_path / "chip" / "quam_state"
    live.mkdir(parents=True)
    (live / "state.json").write_text(json.dumps(_st(1.0e-5, 2.0e-5)), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps(WIRING), encoding="utf-8")
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    return app, c, live


def test_a_stage_from_another_window_is_named_not_called_your_save(tmp_path):
    app, c, live = _chip(tmp_path)
    with app.test_request_context():
        hm = routes._history()
        hm.check_and_snapshot(str(live), "manual", force=True, kind="manual")
        ts_v1 = hm.list_snapshots(str(live))[0].timestamp
    (live / "state.json").write_text(json.dumps(_st(1.5e-5, 2.5e-5)), encoding="utf-8")
    t = time.time() + 3
    os.utime(live / "state.json", (t, t))
    assert c.post("/state/sync", data={"mode": "discard", "force": "1"}).status_code == 200

    # window A stages an edit; its tray shows it
    assert c.post("/field/edit", data={"dot_path": "qubits.q2.T1", "value": "9e-05"}).get_json()["ok"]
    seen = _seen(c)
    # window B loads the older version into the working state (its own confirm)
    assert c.post(f"/state-history/{ts_v1}/stage", data={"force": "1"}, headers=HX).status_code == 200
    # window A presses Apply with what its screen showed
    r = c.post("/state/apply-to-live", data=seen, headers={"Accept": "application/json", **HX})
    body = r.get_json(silent=True) or {}
    msg = body.get("message") or ""
    assert body.get("status") == "unseen_changes" and body.get("nothing_pending"), body
    assert "saved into the working state" not in msg and "press it again" not in msg, (
        f"the refusal blames the user's own Save and tells them to press again: {msg!r}")
    assert body.get("staged") is True and body.get("saved") is False, body
    assert "State History" in msg and "not your edits" in msg, msg
    q = json.loads((live / "state.json").read_text(encoding="utf-8"))["qubits"]
    assert q["q2"]["T1"] == 2.5e-5, "nothing was written"


def test_the_users_own_save_still_says_press_again(tmp_path):
    """The SB-P2-3 case is unchanged: the user's own Save folded the rows."""
    app, c, live = _chip(tmp_path)
    assert c.post("/field/edit", data={"dot_path": "qubits.q2.T1", "value": "9e-05"}).get_json()["ok"]
    seen = _seen(c)
    assert c.post("/save").status_code in (200, 204)
    r = c.post("/state/apply-to-live", data=seen, headers={"Accept": "application/json", **HX})
    body = r.get_json(silent=True) or {}
    assert body.get("nothing_pending") and body.get("saved") is True, body
    assert "press it again" in body.get("message", "")
