"""v1.5.1 R3 C1: the merge door reads the stash of the chip it was pressed on.

The merge door (Pull & apply, and the Auto-Sync merge that presses it)
captures its ctx, pulls, and then captures the edits to replay AGAIN under
the store lock. That second capture read the re-apply stash through the
GLOBAL active context: a chip opened in another window while the pull read
and wrote made this chip's merge replay the other chip's stash -- and with
``mode=apply``, write it to this chip's live files.

The race is made deterministic the way ``test_v151_sb_pull_race`` does it:
another window's real ``POST /load`` lands between the pull's live read and
its working-copy write.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from quam_state_manager.core import working_copy as WC
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

_WIRING = {"network": {"host": "127.0.0.1", "cluster_name": "C1"}}
LAB, Y_EDIT = 2.2e-05, 7.77e-06


def _state(q2=2.1e-05):
    qs = {f"q{i}": {"id": f"q{i}", "T1": 1.0e-5 * i} for i in (1, 2, 3)}
    qs["q2"]["T1"] = q2
    return {"qubits": qs, "qubit_pairs": {}, "active_qubit_names": sorted(qs)}


def _write(folder: Path, state: dict):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")


def _live(folder, q):
    return json.loads((folder / "state.json").read_text(encoding="utf-8"))["qubits"][q]["T1"]


def _ctx_for(app, folder):
    for _name, ctx in app.config["contexts"].items():
        if Path(str(ctx.get("path"))) == folder:
            return ctx
    raise AssertionError(folder)


@pytest.mark.parametrize("mode", ["reapply", "apply"])
def test_a_merge_never_replays_the_stash_of_a_chip_opened_meanwhile(tmp_path, monkeypatch, mode):
    x = tmp_path / "siteX" / "alphachip" / "quam_state"
    y = tmp_path / "siteY" / "betachip" / "quam_state"
    _write(x, _state())
    _write(y, _state())
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()

    # chip Y: an edit stashed for re-apply (what a refused Apply after its save leaves)
    assert c.post("/load", data={"folder": str(y)}).status_code in (200, 302)
    yctx = _ctx_for(app, y)
    with app.test_request_context():
        routes._stash_reapply({"qubits.q1.T1": Y_EDIT}, yctx)
    assert yctx.get("pending_reapply")

    # chip X: open, the lab writes, the merge door is pressed
    assert c.post("/load", data={"folder": str(x)}).status_code in (200, 302)
    xctx = _ctx_for(app, x)
    assert not xctx.get("pending_reapply")
    _write(x, _state(q2=LAB))

    real_dc = WC.doc_cache
    fired: list = []

    class _Hooked:
        def __getattr__(self, name):
            return getattr(real_dc, name)

        def read_pair(self, folder, *a, **k):
            pair = real_dc.read_pair(folder, *a, **k)
            if not fired and k.get("mode") == "fresh" and Path(str(folder)) == x:
                fired.append(1)
                out = {}
                t = threading.Thread(target=lambda: out.update(
                    r=c.post("/load", data={"folder": str(y)})))   # another window opens Y
                t.start()
                t.join(30)
                assert out["r"].status_code in (200, 302)
            return pair

    monkeypatch.setattr(WC, "doc_cache", _Hooked())
    r = c.post("/state/sync", data={"mode": mode, "ack_unseen": "1", "ack_collision": "1"})
    monkeypatch.undo()
    assert fired, "the hook never ran"
    body = r.get_json() or {}
    rows = [e.dot_path for e in xctx["store"].change_log]
    assert xctx["store"].state["qubits"]["q1"]["T1"] != Y_EDIT and "qubits.q1.T1" not in rows, (
        "the other chip's stashed edit was replayed onto this chip")
    assert _live(x, "q1") != Y_EDIT, "the other chip's stashed edit was WRITTEN to this chip's live files"
    assert yctx.get("pending_reapply"), "the other chip's stash is its own, untouched"
    # the response renders the chip the press was made on, not the one opened meanwhile
    tray = body.get("tray_html") or ""
    if tray:
        assert "betachip" not in tray, tray[:600]
