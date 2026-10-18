"""v1.5.1 SA-F4: the agent's take_live never drops a saved draft unnamed.

A person stages an edit and Saves it (the working copy now differs from the
live chip, the tray is empty), the lab writes the same field, and the agent
calls ``take_live``. The bridge only counted tray rows, the pull dropped the
saved draft, and the answer said ``reverted: []``.

Pinned over the real MCP function and the real routes (a test-client link):
the bridge refuses while a saved version is unapplied, names it, and leaves
the working copy alone; and when a pull does drop something (a draft saved
between its check and the pull), the answer names what and the backup
version that holds it -- never ``reverted: []``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager import mcp
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

_WIRING = {"network": {"host": "127.0.0.1", "cluster_name": "C1"}}


def _state(t1=1.0e-5):
    return {"qubits": {"q1": {"id": "q1", "T1": t1, "z": {"joint_offset": 0.08}}},
            "qubit_pairs": {}, "active_qubit_names": ["q1"]}


def _write_chip(folder: Path, state: dict):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")


class _ClientLink:
    """The bridge's SM link, over the Flask test client (same routes, same
    agent header)."""

    def __init__(self, client):
        self.c = client
        self.h = {"X-SM-Agent": "mcp"}

    def alive(self):
        return True

    @staticmethod
    def _out(r):
        return r.status_code, (r.get_json(silent=True) if r.is_json else r.get_data(as_text=True))

    def get(self, path, params=None, **_k):
        return self._out(self.c.get(path, query_string=params or {}, headers=self.h))

    def post_form(self, path, data):
        return self._out(self.c.post(path, data={k: v for k, v in data.items() if v is not None},
                                     headers=self.h))

    def post_json(self, path, data, **_k):
        return self._out(self.c.post(path, json=data, headers=self.h))


@pytest.fixture
def env(tmp_path, monkeypatch):
    live = tmp_path / "chips" / "live"
    _write_chip(live, _state())
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    # a person's saved draft: staged, Saved -- the tray is empty again
    r = c.post("/field/edit", data={"dot_path": "qubits.q1.T1", "value": "1.2e-05"})
    assert r.status_code == 200 and r.get_json()["ok"]
    assert c.post("/save").status_code == 200
    ctx = app.config["contexts"][app.config["active_context"]]
    assert not ctx["store"].change_log and ctx.get("working_dirty")
    # the lab writes the same field
    _write_chip(live, _state(t1=2.2e-05))
    monkeypatch.setattr(mcp, "_link", _ClientLink(c))
    monkeypatch.setattr(mcp, "_CHIP_PIN", None)
    for k, v in {"_seen": None, "_chip": None, "_seen_key": None, "_seen_token": None,
                 "_seen_sig": None, "_seen_paths": [], "_pin_key": None}.items():
        monkeypatch.setattr(mcp, k, v, raising=False)
    return {"app": app, "client": c, "live": live, "ctx": ctx}


def _working_t1(env):
    wf = env["ctx"]["working_copy"].working_folder
    return json.loads((Path(wf) / "state.json").read_text(encoding="utf-8"))["qubits"]["q1"]["T1"]


def test_take_live_refuses_over_a_saved_draft(env):
    out = mcp.t_take_live({})
    assert out.get("taken") is False, out
    assert out.get("would_drop") == "saved_version", out
    assert _working_t1(env) == 1.2e-05, "the saved draft is still the working copy"
    assert env["ctx"].get("working_dirty")


def test_a_pull_that_drops_a_draft_names_it_and_its_backup(env, monkeypatch):
    # the draft is saved between the bridge's look and its pull: the look saw
    # nothing pending, the pull still drops it -- the answer must say so
    real = mcp._chip_facts
    calls = {"n": 0}

    def facts():
        f = real()
        calls["n"] += 1
        if calls["n"] == 1:
            f = {**f, "working_dirty": False, "pending": 0, "reapply_stash": 0}
        return f

    monkeypatch.setattr(mcp, "_chip_facts", facts)
    out = mcp.t_take_live({})
    assert out.get("taken") is True, out
    assert _working_t1(env) == 2.2e-05, "the pull took live"
    assert out["reverted"], f"content was discarded but reverted is empty: {out}"
    assert out.get("dropped", 0) >= 1 and out.get("backup_version"), out
    hm = env["app"].config["history_manager"]
    held = [m.timestamp for m in hm.list_snapshots(env["ctx"]["path"])
            if hm.load_snapshot(env["ctx"]["path"], m.timestamp).state["qubits"]["q1"]["T1"] == 1.2e-05]
    assert out["backup_version"] in held, (out["backup_version"], held)
