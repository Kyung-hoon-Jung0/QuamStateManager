"""v1.3.0 B1 P2: Take live while another program is saving state.json.

A real outside writer (QUAlibrate's in-place save) leaves state.json
truncated for a moment. ``POST /state/sync`` answered 500 "state.json is not
valid JSON" -- the read had already retried, nothing was pulled, and the live
chip was not touched: it is a moment to wait out, not a server failure. It now
answers 409 with ``transient: true`` and a message that says to retry; the
same press after the writer finishes pulls normally."""
import json
from pathlib import Path

import pytest

from quam_state_manager.core import safe_io
from quam_state_manager.web.app import create_app


def _state(f01=5.0e9):
    return {"qubits": {"q1": {"id": "q1", "f_01": f01}}, "qubit_pairs": {},
            "active_qubit_names": ["q1"]}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(safe_io, "_READ_BACKOFF_S", 0.0)      # the retry ladder, without its waits
    live = tmp_path / "live"
    live.mkdir()
    (live / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps({"wiring": {}, "network": {}}), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "inst"))
    app.config["PROPAGATE_EXCEPTIONS"] = False
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    return c, live


@pytest.mark.parametrize("mode", ["discard", "reapply"])
def test_a_torn_live_file_is_a_transient_409_and_nothing_changes(env, mode):
    c, live = env
    good = json.dumps(_state(5.2e9))
    torn = good[: len(good) // 3]
    (live / "state.json").write_text(torn, encoding="utf-8")
    r = c.post("/state/sync", data={"mode": mode})
    assert r.status_code == 409, r.data[:300]
    body = r.get_json()
    assert body["status"] == "error" and body["transient"] is True
    assert "retry in a moment" in body["message"] and "not valid JSON" in body["message"]
    assert (live / "state.json").read_text(encoding="utf-8") == torn      # live untouched
    peek = c.get("/field/peek", query_string={"dot_path": "qubits.q1.f_01"}).get_json()
    assert "5000000000" in json.dumps(peek).replace(".0", "")             # nothing pulled

    # the writer finishes: the same press pulls
    (live / "state.json").write_text(good, encoding="utf-8")
    r2 = c.post("/state/sync", data={"mode": mode})
    assert r2.status_code == 200 and r2.get_json()["status"] == "ok", r2.data[:300]
