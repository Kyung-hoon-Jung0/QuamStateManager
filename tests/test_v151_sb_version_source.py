"""v1.5.1 SB-F8: a saved version of ANOTHER folder's design staged silently.

After Re-generate into a new folder, both folders carry the same declared
chip name, so they share one history (the ladder's tier 1). The newest
version -- saved from the rebuilt folder (q0..q4, with an edit made there) --
staged onto the old folder (q1..q5) with "Snapshot ... loaded as the working
state ... press Apply": one Apply away from writing the rebuilt design into
the old chip.

Staging now asks first, naming the source folder, when a version was saved
from another chip folder that is still there, or holds another design; its
own token (``ack_source``) loads it anyway. A version of this folder (or of
a folder that has since gone) loads as before.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

_WIRING = {"network": {"host": "127.0.0.1", "cluster_name": "C1"}}


def _state(first: int, t1: float = 1.0e-5):
    qs = {f"q{i}": {"id": f"q{i}", "T1": t1} for i in range(first, first + 5)}
    return {"qubits": qs, "qubit_pairs": {}, "active_qubit_names": sorted(qs),
            "extras": {"chip_name": "gchip"}}


def _write(folder: Path, state: dict):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")


@pytest.fixture
def env(tmp_path):
    old = tmp_path / "quam_states" / "chip_g"
    new = tmp_path / "quam_states" / "chip_g_regen"
    _write(old, _state(1))
    _write(new, _state(0, t1=2.0e-5))
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    hm = app.config["history_manager"]
    assert c.post("/load", data={"folder": str(old)}).status_code in (200, 302)
    own = hm.check_and_snapshot(old, "manual", force=True)
    regen = hm.check_and_snapshot(new, "manual", force=True)
    assert own and regen
    assert hm._key_for(old) == hm._key_for(new), "the fixture must share one history"
    return {"app": app, "c": c, "old": old, "new": new, "own": own.timestamp,
            "regen": regen.timestamp, "tmp": tmp_path}


def _qubits(env):
    ctx = env["app"].config["contexts"][env["app"].config["active_context"]]
    return sorted(ctx["store"].state["qubits"])


class TestAVersionOfAnotherFolderAsks:
    def test_the_rebuilt_design_is_named_and_not_staged(self, env):
        r = env["c"].post(f"/state-history/{env['regen']}/stage")
        assert r.status_code == 409, r.get_data(as_text=True)[:400]
        body = r.get_data(as_text=True)
        assert str(env["new"]) in body and str(env["old"]) in body, body
        assert "ack_source=1" in body and "q0" in body
        assert _qubits(env) == ["q1", "q2", "q3", "q4", "q5"], "staged without asking"

    def test_the_yes_loads_it(self, env):
        r = env["c"].post(f"/state-history/{env['regen']}/stage?ack_source=1")
        assert r.status_code == 200, r.get_data(as_text=True)[:400]
        assert _qubits(env) == ["q0", "q1", "q2", "q3", "q4"]

    def test_force_alone_never_answers_it(self, env):
        r = env["c"].post(f"/state-history/{env['regen']}/stage?force=1")
        assert r.status_code == 409 and _qubits(env) == ["q1", "q2", "q3", "q4", "q5"]


class TestThisChipsOwnVersionsLoadAsBefore:
    def test_a_version_of_this_folder(self, env):
        r = env["c"].post(f"/state-history/{env['own']}/stage")
        assert r.status_code == 200, r.get_data(as_text=True)[:400]

    def test_a_version_whose_folder_is_gone_but_same_design(self, env, tmp_path):
        """A chip that moved: its old folder no longer exists."""
        import shutil
        hm = env["app"].config["history_manager"]
        gone = tmp_path / "quam_states" / "chip_g_old"
        _write(gone, _state(1, t1=3.0e-5))
        ts = hm.check_and_snapshot(gone, "manual", force=True).timestamp
        shutil.rmtree(gone)
        r = env["c"].post(f"/state-history/{ts}/stage")
        assert r.status_code == 200, r.get_data(as_text=True)[:400]


class TestRestoreLiveAsksToo:
    """R2 MINOR 5: restore-live (Mode 2) of a SAME-design version saved from
    another existing folder wrote it to the live chip unasked; only stage
    asked (a design mismatch is still the align gate's)."""

    @pytest.fixture
    def same(self, tmp_path):
        old = tmp_path / "quam_states" / "chip_h"
        copy = tmp_path / "quam_states" / "chip_h_copy"
        _write(old, _state(1))
        _write(copy, _state(1, t1=7.0e-5))                 # same design, another folder
        routes._quam_cache.clear()
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        c = app.test_client()
        hm = app.config["history_manager"]
        assert c.post("/load", data={"folder": str(old)}).status_code in (200, 302)
        hm.check_and_snapshot(old, "manual", force=True)
        ts = hm.check_and_snapshot(copy, "manual", force=True).timestamp
        assert hm._key_for(old) == hm._key_for(copy)
        return {"c": c, "old": old, "copy": copy, "ts": ts}

    @staticmethod
    def _live_t1(folder):
        return json.loads((folder / "state.json").read_text(encoding="utf-8"))["qubits"]["q1"]["T1"]

    def test_the_source_folder_is_named_and_nothing_written(self, same):
        r = same["c"].post(f"/state-history/{same['ts']}/restore-live")
        body = r.get_data(as_text=True)
        assert r.status_code == 409 and str(same["copy"]) in body and "ack_source=1" in body, body[:600]
        assert self._live_t1(same["old"]) == 1.0e-5, "restored to live without asking"

    def test_the_yes_restores_it(self, same):
        r = same["c"].post(f"/state-history/{same['ts']}/restore-live?ack_source=1")
        assert r.status_code == 200, r.get_data(as_text=True)[:400]
        assert self._live_t1(same["old"]) == 7.0e-5

    def test_the_unsaved_edits_token_never_answers_it(self, same):
        r = same["c"].post(f"/state-history/{same['ts']}/restore-live?force_pending=1&force_align=1")
        assert r.status_code == 409 and self._live_t1(same["old"]) == 1.0e-5
