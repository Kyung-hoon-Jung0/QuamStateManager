"""Op-tagged change-log capture + replay (the sync pull-with-reapply path).

Regression coverage for the pre-existing landmines the Pulses feature
stepped on:

- L3: ``_capture_change_log_as_updates`` used to flatten the log to
  ``{path: new_value}`` and replay via ``set_value`` only — a *created*
  pulse silently vanished on pull-with-reapply (KeyError → "failed"), and
  deletions were unrepresentable (a deleted string alias would have been
  resurrected as the literal string ``"None"``).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.modifier import Modifier
from quam_state_manager.web.app import create_app
from quam_state_manager.web.routes import (
    _capture_change_log_as_updates,
    _replay_updates,
)

QC = "quam.components.pulses."


def _make_state(f_01: float = 6.25e9) -> dict:
    return {
        "qubits": {
            "qA1": {
                "id": "qA1",
                "f_01": f_01,
                "anharmonicity": -220e6,
                "xy": {
                    "RF_frequency": f_01,
                    "operations": {
                        "x180_DragCosine": {"amplitude": 0.115, "length": 40},
                        "x180": "#./x180_DragCosine",
                    },
                },
                "resonator": {
                    "operations": {"readout": {"amplitude": 0.042, "length": 1000}},
                },
            },
        },
        "qubit_pairs": {},
        "active_qubit_names": ["qA1"],
    }


def _make_wiring() -> dict:
    return {
        "wiring": {"qubits": {"qA1": {"xy": {"opx_output": "MW-FEM/1/2"}}}},
        "network": {"host": "10.1.1.18"},
    }


OPS = "qubits.qA1.xy.operations"


# ---------------------------------------------------------------------------
# Unit level: capture composition + replay dispatch over plain stores
# ---------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path: Path) -> QuamStore:
    (tmp_path / "state.json").write_text(json.dumps(_make_state()), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps(_make_wiring()), encoding="utf-8")
    return QuamStore(tmp_path)


@pytest.fixture
def fresh_store(tmp_path: Path) -> QuamStore:
    folder = tmp_path / "fresh"
    folder.mkdir()
    (folder / "state.json").write_text(json.dumps(_make_state(f_01=7.0e9)),
                                       encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_make_wiring()), encoding="utf-8")
    return QuamStore(folder)


class TestCapture:
    def test_set_tagged(self, store):
        mod = Modifier(store)
        mod.set_value("qubits.qA1.f_01", 6.3e9)
        assert _capture_change_log_as_updates(store) == {
            "qubits.qA1.f_01": ("set", 6.3e9)}

    def test_create_tagged(self, store):
        mod = Modifier(store)
        mod.create_subtree(f"{OPS}.probe", {"amplitude": 0.1, "length": 16})
        updates = _capture_change_log_as_updates(store)
        assert updates[f"{OPS}.probe"][0] == "create"

    def test_delete_tagged(self, store):
        mod = Modifier(store)
        mod.delete_subtree(f"{OPS}.x180")
        assert _capture_change_log_as_updates(store) == {
            f"{OPS}.x180": ("delete", None)}

    def test_create_then_delete_cancels_out(self, store):
        mod = Modifier(store)
        mod.create_subtree(f"{OPS}.probe", {"amplitude": 0.1, "length": 16})
        mod.delete_subtree(f"{OPS}.probe")
        assert _capture_change_log_as_updates(store) == {}

    def test_delete_then_recreate_becomes_replace(self, store):
        # "replace" (not "set"): the pulled live value's type may differ
        # (string alias deleted, dict re-created) and a coercing set would
        # stringify the dict — see TestReplaceOpRegression.
        mod = Modifier(store)
        mod.delete_subtree(f"{OPS}.x180_DragCosine")
        mod.create_subtree(f"{OPS}.x180_DragCosine", {"amplitude": 0.2, "length": 32})
        updates = _capture_change_log_as_updates(store)
        op, value = updates[f"{OPS}.x180_DragCosine"]
        assert op == "replace" and value["amplitude"] == 0.2

    def test_create_then_edit_stays_create_with_latest(self, store):
        mod = Modifier(store)
        mod.create_subtree(f"{OPS}.probe", {"amplitude": 0.1, "length": 16})
        mod.set_value(f"{OPS}.probe.amplitude", 0.7)
        updates = _capture_change_log_as_updates(store)
        op, value = updates[f"{OPS}.probe"]
        assert op == "create"
        # the logged subtree is aliased to the live tree → carries the edit
        assert value["amplitude"] == 0.7
        # the inner set entry replays fine after the create
        assert updates[f"{OPS}.probe.amplitude"] == ("set", 0.7)

    def test_delete_subsumes_inner_edits(self, store):
        mod = Modifier(store)
        mod.set_value(f"{OPS}.x180_DragCosine.amplitude", 0.9)
        mod.delete_subtree(f"{OPS}.x180_DragCosine")
        updates = _capture_change_log_as_updates(store)
        assert f"{OPS}.x180_DragCosine.amplitude" not in updates
        assert updates[f"{OPS}.x180_DragCosine"] == ("delete", None)


class TestReplay:
    def test_create_survives_replay(self, store, fresh_store):
        """The L3 regression: a created pulse must survive pull-with-reapply."""
        mod = Modifier(store)
        mod.create_subtree(f"{OPS}.probe", {
            "amplitude": 0.1, "length": 16, "__class__": QC + "SquarePulse"})
        pending = _capture_change_log_as_updates(store)

        replay = _replay_updates(Modifier(fresh_store), pending)
        assert replay["failed"] == []
        assert replay["applied"] == 1
        ops = fresh_store.merged["qubits"]["qA1"]["xy"]["operations"]
        assert ops["probe"]["amplitude"] == 0.1

    def test_delete_survives_replay(self, store, fresh_store):
        mod = Modifier(store)
        mod.delete_subtree(f"{OPS}.x180_DragCosine")
        pending = _capture_change_log_as_updates(store)

        replay = _replay_updates(Modifier(fresh_store), pending)
        assert replay["failed"] == []
        ops = fresh_store.merged["qubits"]["qA1"]["xy"]["operations"]
        assert "x180_DragCosine" not in ops

    def test_delete_of_alias_does_not_write_none_string(self, store, fresh_store):
        """A deleted string-alias op must NOT be resurrected as 'None'."""
        mod = Modifier(store)
        mod.delete_subtree(f"{OPS}.x180")
        pending = _capture_change_log_as_updates(store)

        replay = _replay_updates(Modifier(fresh_store), pending)
        assert replay["failed"] == []
        ops = fresh_store.merged["qubits"]["qA1"]["xy"]["operations"]
        assert "x180" not in ops
        assert "None" not in json.dumps(ops)

    def test_delete_of_already_missing_path_is_noop_success(self, store, fresh_store):
        mod = Modifier(store)
        mod.create_subtree(f"{OPS}.temp", {"amplitude": 0.1, "length": 4})
        mod2 = Modifier(store)
        mod2.delete_subtree(f"{OPS}.temp")
        # hand-build a delete of something the fresh state never had
        replay = _replay_updates(Modifier(fresh_store),
                                 {f"{OPS}.temp": ("delete", None)})
        assert replay["failed"] == [] and replay["applied"] == 1

    def test_create_onto_existing_path_keeps_live_on_conflict(self, fresh_store):
        # audit #3: a create whose key already exists on the PULLED state with a
        # DIFFERENT value must NOT clobber it — the live version (e.g. a
        # qualibrate-created twin) is kept and the conflict reported, never
        # silently overwritten + counted as applied.
        ops = fresh_store.merged["qubits"]["qA1"]["xy"]["operations"]
        before = dict(ops["x180_DragCosine"])
        replay = _replay_updates(Modifier(fresh_store), {
            f"{OPS}.x180_DragCosine": ("create", {"amplitude": 0.9, "length": 8}),
        })
        assert replay["applied"] == 0
        assert len(replay["failed"]) == 1
        assert "kept the live version" in replay["failed"][0]["error"]
        assert ops["x180_DragCosine"] == before  # unchanged — not clobbered

    def test_create_onto_existing_path_equal_value_is_noop_applied(self, fresh_store):
        # A byte-equal create-collision is a true no-op success (nothing to do).
        ops = fresh_store.merged["qubits"]["qA1"]["xy"]["operations"]
        replay = _replay_updates(Modifier(fresh_store), {
            f"{OPS}.x180_DragCosine": ("create", dict(ops["x180_DragCosine"])),
        })
        assert replay["failed"] == [] and replay["applied"] == 1

    def test_legacy_plain_value_treated_as_set(self, fresh_store):
        replay = _replay_updates(Modifier(fresh_store),
                                 {"qubits.qA1.f_01": 6.5e9})
        assert replay["failed"] == []
        assert fresh_store.merged["qubits"]["qA1"]["f_01"] == 6.5e9

    def test_set_on_missing_path_still_reports_failed(self, fresh_store):
        replay = _replay_updates(Modifier(fresh_store),
                                 {"qubits.qA1.gone": ("set", 1)})
        assert len(replay["failed"]) == 1

    def test_mixed_batch_order_preserved(self, store, fresh_store):
        mod = Modifier(store)
        mod.set_value("qubits.qA1.f_01", 6.31e9)
        mod.create_subtree(f"{OPS}.probe", {"amplitude": 0.1, "length": 16})
        mod.delete_subtree(f"{OPS}.x180")
        pending = _capture_change_log_as_updates(store)

        replay = _replay_updates(Modifier(fresh_store), pending)
        assert replay["failed"] == []
        assert replay["applied"] == 3
        merged = fresh_store.merged
        assert merged["qubits"]["qA1"]["f_01"] == 6.31e9
        ops = merged["qubits"]["qA1"]["xy"]["operations"]
        assert "probe" in ops and "x180" not in ops


# ---------------------------------------------------------------------------
# Client level: the full conflict → pull-with-reapply flow with a CREATED
# pulse (uses the existing add-pulse endpoint) — end-to-end L3 regression.
# ---------------------------------------------------------------------------

def _write_live_state(folder: Path, state: dict) -> None:
    p = folder / "state.json"
    p.write_text(json.dumps(state, indent=2), encoding="utf-8")
    future = time.time() + 100
    os.utime(p, (future, future))


@pytest.fixture
def synth_folder(tmp_path: Path) -> Path:
    (tmp_path / "state.json").write_text(json.dumps(_make_state(), indent=2),
                                         encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps(_make_wiring(), indent=2),
                                          encoding="utf-8")
    return tmp_path


@pytest.fixture
def loaded_client(tmp_path, synth_folder):
    app = create_app(testing=True, instance_path=str(tmp_path / "_app_instance"))
    client = app.test_client()
    client.post("/load", data={"folder": str(synth_folder)})
    return client


class TestCreatedPulseSurvivesSync:
    def test_created_pulse_survives_pull_reapply(self, loaded_client, synth_folder):
        # 1. create a pulse through the existing add-pulse endpoint
        resp = loaded_client.post("/api/pulse/create", data={
            "target_kind": "qubit", "qubit": "qA1", "channel": "xy",
            "op_name": "my_probe", "pulse_type": "SquarePulse",
            "amplitude": "0.22", "length": "64",
        })
        assert resp.status_code == 200

        # 2. an experiment overwrites the live state out from under us
        _write_live_state(synth_folder, _make_state(f_01=7.0e9))

        # 3. pull with reapply — the created pulse must survive
        data = loaded_client.post("/state/sync", data={"mode": "reapply"}).get_json()
        assert data["status"] == "ok"
        assert data["replay"]["failed"] == []

        review = loaded_client.get("/state/review").data.decode()
        assert "my_probe" in review

    def test_created_pulse_survives_pull_apply_to_live(self, loaded_client, synth_folder):
        resp = loaded_client.post("/api/pulse/create", data={
            "target_kind": "qubit", "qubit": "qA1", "channel": "xy",
            "op_name": "my_probe", "pulse_type": "SquarePulse",
            "amplitude": "0.22", "length": "64",
        })
        assert resp.status_code == 200
        _write_live_state(synth_folder, _make_state(f_01=7.0e9))

        data = loaded_client.post("/state/sync", data={"mode": "apply"}).get_json()
        assert data["status"] == "ok"
        assert data["replay"]["failed"] == []
        live = json.loads((synth_folder / "state.json").read_text(encoding="utf-8"))
        ops = live["qubits"]["qA1"]["xy"]["operations"]
        assert ops["my_probe"]["amplitude"] == 0.22
        # the pulled live value is kept where we didn't edit
        assert live["qubits"]["qA1"]["f_01"] == 7.0e9


class TestReplaceOpRegression:
    """Adversarial-review finding: delete+recreate over a STRING ALIAS used
    to compose to ("set", dict), and the coercing replay stringified the
    dict into its Python repr — silently, reported as success."""

    def test_delete_alias_recreate_dict_composes_to_replace(self, store):
        mod = Modifier(store)
        mod.delete_subtree(f"{OPS}.x180")
        mod.create_subtree(f"{OPS}.x180", {"amplitude": 0.2, "length": 32})
        updates = _capture_change_log_as_updates(store)
        op, value = updates[f"{OPS}.x180"]
        assert op == "replace" and value["amplitude"] == 0.2

    def test_replay_replace_over_string_alias_keeps_dict(self, store, fresh_store):
        mod = Modifier(store)
        mod.delete_subtree(f"{OPS}.x180")
        mod.create_subtree(f"{OPS}.x180", {"amplitude": 0.2, "length": 32})
        pending = _capture_change_log_as_updates(store)

        replay = _replay_updates(Modifier(fresh_store), pending)
        assert replay["failed"] == []
        replayed = fresh_store.merged["qubits"]["qA1"]["xy"]["operations"]["x180"]
        assert isinstance(replayed, dict), (
            f"replayed as {type(replayed).__name__}: {replayed!r}")
        assert replayed == {"amplitude": 0.2, "length": 32}

    def test_replace_then_edit_keeps_replace(self, store):
        mod = Modifier(store)
        mod.delete_subtree(f"{OPS}.x180_DragCosine")
        mod.create_subtree(f"{OPS}.x180_DragCosine", {"amplitude": 0.2, "length": 32})
        mod.set_value(f"{OPS}.x180_DragCosine.amplitude", 0.3)
        updates = _capture_change_log_as_updates(store)
        op, value = updates[f"{OPS}.x180_DragCosine"]
        assert op == "replace" and value["amplitude"] == 0.3

    def test_delete_recreate_delete_nets_to_delete(self, store):
        mod = Modifier(store)
        mod.delete_subtree(f"{OPS}.x180")
        mod.create_subtree(f"{OPS}.x180", {"amplitude": 0.2, "length": 32})
        mod.delete_subtree(f"{OPS}.x180")
        updates = _capture_change_log_as_updates(store)
        assert updates[f"{OPS}.x180"] == ("delete", None)

    def test_replay_replace_when_live_also_deleted(self, fresh_store):
        # live deleted the path too → replace falls back to create
        del fresh_store.merged["qubits"]["qA1"]["xy"]["operations"]["x180"]
        replay = _replay_updates(Modifier(fresh_store), {
            f"{OPS}.x180": ("replace", {"amplitude": 0.9, "length": 8})})
        assert replay["failed"] == []
        ops = fresh_store.merged["qubits"]["qA1"]["xy"]["operations"]
        assert ops["x180"] == {"amplitude": 0.9, "length": 8}
