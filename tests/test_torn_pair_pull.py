"""A pull never adopts a pair caught between QUAlibrate's two file writes
(QA correctness-r2-08).

QUAlibrate's ``machine.save()`` writes state.json and THEN wiring.json, each
in place. A "Take live" landing in between adopted a chip that never existed
-- state.json had already moved q5's flux line from port con1/5/5 to 5/6,
wiring.json still wired it to 5/5 -- and recorded it as a MANUAL version with a
one-click "Pull to Live". Both files were complete and their mtimes settled,
so the pair read's mtime bracket could not see it.

Pins:
  - the pure helper finds port references that dangle in the live pair but
    did not in the pair SM holds, and ignores ones that already dangled;
  - every manual pull door answers ``torn_live`` (nothing pulled, no version)
    until the user acks it with its own token, then pulls;
  - a chip whose reference ALREADY dangled pulls without a question;
  - an armed Auto-Sync pull waits a torn pair out for a bounded number of
    polls, then pulls.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from quam_state_manager.core import working_copy
from quam_state_manager.web import routes as routes_mod
from quam_state_manager.web.app import create_app


def _state(port: str = "5", f01: float = 5.0e9) -> dict:
    return {
        "qubits": {"q5": {"id": "q5", "f_01": f01,
                          "z": {"opx_output": "#/wiring/qubits/q5/z/opx_output"}}},
        "qubit_pairs": {}, "active_qubit_names": ["q5"],
        "ports": {"analog_outputs": {"con1": {"5": {port: {"port_id": int(port)}}}}},
    }


def _wiring(port: str = "5") -> dict:
    return {"wiring": {"qubits": {"q5": {"z": {
                "opx_output": f"#/ports/analog_outputs/con1/5/{port}"}}}},
            "network": {"host": "1.1.1.1", "cluster_name": "C1"}}


def _write(folder: Path, state: dict | None = None, wiring: dict | None = None) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    if state is not None:
        (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    if wiring is not None:
        (folder / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")


@pytest.fixture
def env(tmp_path):
    live = tmp_path / "chips" / "live"
    _write(live, _state(), _wiring())
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    return {"app": app, "client": c, "live": live, "tmp": tmp_path}


def _ctx(env):
    return next(iter(env["app"].config["contexts"].values()))


def _versions(env) -> set[str]:
    root = env["tmp"] / "_inst" / "history"
    return {f"{d.parent.name}/{d.name}" for d in root.glob("*/*")
            if d.is_dir() and re.match(r"^\d{8}_\d{6}", d.name)}


def _torn(env) -> None:
    """state.json written (port 5 -> 6), wiring.json not yet."""
    _write(env["live"], state=_state(port="6", f01=5.1e9))


class TestTheHelper:
    def test_a_moved_port_dangles_only_in_the_torn_pair(self):
        assert working_copy.dangling_port_refs(_state(), _wiring()) == {}
        torn = working_copy.dangling_port_refs(_state("6"), _wiring("5"))
        assert torn == {"wiring.qubits.q5.z.opx_output": "#/ports/analog_outputs/con1/5/5"}
        assert working_copy.dangling_port_refs(_state("6"), _wiring("6")) == {}

    def test_a_reference_that_already_dangled_is_not_new(self):
        held = (_state("6"), _wiring("5"))            # SM already holds it dangling
        assert working_copy.new_dangling_port_refs(*held, *held) == {}
        assert working_copy.new_dangling_port_refs(_state(), _wiring(), *held) != {}


class TestManualPull:
    @pytest.mark.parametrize("mode", ["discard", "reapply", "apply"])
    def test_every_pull_door_asks_and_pulls_nothing(self, env, mode):
        _torn(env)
        before = _versions(env)
        r = env["client"].post("/state/sync", data={"mode": mode})
        d = r.get_json()
        assert d["status"] == "torn_live", d
        assert "con1/5/5" in d["message"] and "mid-save" in d["message"]
        held = _ctx(env)["store"]
        assert "5" in held.state["ports"]["analog_outputs"]["con1"]["5"], "the torn pair was adopted"
        assert _versions(env) == before, "a torn pair was recorded as a version"

    def test_the_users_own_token_takes_it_as_it_is(self, env):
        _torn(env)
        d = env["client"].post("/state/sync", data={"mode": "discard", "ack_torn": "1"}).get_json()
        assert d["status"] == "ok", d
        assert "6" in _ctx(env)["store"].state["ports"]["analog_outputs"]["con1"]["5"]

    def test_the_finished_save_pulls_without_a_question(self, env):
        _write(env["live"], _state("6", f01=5.1e9), _wiring("6"))
        d = env["client"].post("/state/sync", data={"mode": "discard"}).get_json()
        assert d["status"] == "ok", d

    def test_force_is_not_the_answer_to_this_question(self, env):
        """docs/41: one token never collapses two gates -- force=1 answers the
        staged-content question, not this one."""
        _torn(env)
        d = env["client"].post("/state/sync", data={"mode": "discard", "force": "1"}).get_json()
        assert d["status"] == "torn_live"


class TestAChipThatAlreadyDangled:
    def test_pulls_without_asking(self, tmp_path):
        live = tmp_path / "chips" / "live"
        _write(live, _state("6"), _wiring("5"))       # dangling from the start
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        c = app.test_client()
        assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
        _write(live, state=_state("6", f01=5.3e9))    # an ordinary outside edit
        d = c.post("/state/sync", data={"mode": "discard"}).get_json()
        assert d["status"] == "ok", d


class TestAutoSyncWaitsItOut:
    def _due(self, env):
        with env["app"].test_request_context():
            ctx = routes_mod._active_ctx()
            ctx["_live_hash_checked_at"] = None
            routes_mod._refresh_live_diverged(ctx)

    def test_bounded_wait_then_pull(self, env):
        c = env["client"]
        assert c.post("/auto-sync/set", data={"pull": "1", "pull_replace": "1"}).status_code == 200
        _torn(env)
        self._due(env)
        for _ in range(routes_mod._AUTO_PULL_TORN_POLLS):
            r = c.post("/auto-sync/pull", data={"dom_dirty": "0"})
            assert r.status_code == 204
            assert "5" in _ctx(env)["store"].state["ports"]["analog_outputs"]["con1"]["5"]
        r = c.post("/auto-sync/pull", data={"dom_dirty": "0"})
        assert r.status_code == 200
        assert "6" in _ctx(env)["store"].state["ports"]["analog_outputs"]["con1"]["5"]

    def test_the_finished_save_is_pulled_at_once(self, env):
        c = env["client"]
        assert c.post("/auto-sync/set", data={"pull": "1", "pull_replace": "1"}).status_code == 200
        _torn(env)
        self._due(env)
        assert c.post("/auto-sync/pull", data={"dom_dirty": "0"}).status_code == 204
        _write(env["live"], wiring=_wiring("6"))       # QUAlibrate's second write lands
        self._due(env)
        assert c.post("/auto-sync/pull", data={"dom_dirty": "0"}).status_code == 200
        held = _ctx(env)["store"]
        assert "6" in held.state["ports"]["analog_outputs"]["con1"]["5"]
        assert held.wiring["wiring"]["qubits"]["q5"]["z"]["opx_output"].endswith("/5/6")
