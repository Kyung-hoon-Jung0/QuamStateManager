"""r11: the dataset detail's "Load State" button.

With a project chip open, the user's intent is "pull this experiment's state
into MY chip" — the run's frozen quam_state must be STAGED into the active
context's WORKING COPY (State History Mode-1 semantics: live untouched until
an explicit Sync / Apply), never activated as a separate archive context that
hijacks the project scope and the load-path box. The archive open survives as
``mode=archive`` and as the fallback when nothing editable is loaded."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.web import routes as routes_mod
from quam_state_manager.web.app import create_app

_STATE = {"qubits": {"qA1": {"id": "qA1", "f_01": 6.25e9}},
          "qubit_pairs": {}, "active_qubit_names": ["qA1"]}
_WIRING = {"network": {"host": "1.1.1.1", "cluster_name": "C1"}}


def _chip(folder: Path, state=None, wiring=None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state or _STATE),
                                       encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(wiring or _WIRING),
                                        encoding="utf-8")
    return folder


def _seed_run(root: Path, run_id: int, *, state=None, wiring=None,
              date="2026-07-29", hhmmss="010000", name="31_chevron") -> Path:
    run = root / date / f"#{run_id}_{name}_{hhmmss}"
    run.mkdir(parents=True)
    (run / "node.json").write_text(json.dumps({
        "metadata": {"name": name, "status": "successful",
                     "run_start": f"{date}T01:00:00",
                     "run_end": f"{date}T01:00:01"},
        "data": {"parameters": {"model": {"qubits": ["qA1"]}}, "outcomes": {}},
        "id": run_id, "parents": [], "created_at": f"{date}T01:00:00",
    }), encoding="utf-8")
    (run / "data.json").write_text("{}", encoding="utf-8")
    _chip(run / "quam_state", state=state, wiring=wiring)
    return run


@pytest.fixture
def env(tmp_path):
    chip = _chip(tmp_path / "chips" / "live_chip")
    data_root = tmp_path / "data"
    # run 1: snapshot of the SAME chip with a changed value
    changed = json.loads(json.dumps(_STATE))
    changed["qubits"]["qA1"]["f_01"] = 6.31e9
    _seed_run(data_root, 1, state=changed)
    # run 2: a DIFFERENT chip entirely
    _seed_run(data_root, 2, state={"qubits": {"qZ9": {"id": "qZ9"}},
                                   "qubit_pairs": {},
                                   "active_qubit_names": ["qZ9"]},
              wiring={"network": {"host": "9.9.9.9", "cluster_name": "OTHER"}})
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    c.post("/workspace/add", data={"folder": str(data_root)})
    key = routes_mod._folder_key(data_root)
    return {"app": app, "client": c, "chip": chip, "data_root": data_root,
            "uid_same": f"{key}:1", "uid_other": f"{key}:2"}


def _active(app):
    name = app.config.get("active_context")
    return (app.config.get("contexts") or {}).get(name) if name else None


def _live_bytes(chip: Path) -> tuple[bytes, bytes]:
    return ((chip / "state.json").read_bytes(),
            (chip / "wiring.json").read_bytes())


class TestStageIntoOpenChip:
    def test_stages_into_working_copy_not_a_new_context(self, env):
        c = env["client"]
        c.post("/load", data={"folder": str(env["chip"])})
        before_name = env["app"].config["active_context"]
        live_before = _live_bytes(env["chip"])

        r = c.post(f"/dataset/{env['uid_same']}/load-state")
        assert r.status_code == 200
        body = r.get_data(as_text=True)
        assert "WORKING state" in body
        assert "HX-Redirect" not in r.headers          # stays on the page
        assert "stateRestored" in r.headers.get("HX-Trigger", "")

        ctx = _active(env["app"])
        assert env["app"].config["active_context"] == before_name  # no hijack
        assert (ctx.get("origin") or "live") == "live"
        assert ctx.get("working_dirty") is True
        # the working copy now holds the run's snapshot…
        wc_state = json.loads(
            (ctx["working_copy"].working_folder / "state.json")
            .read_text(encoding="utf-8"))
        assert wc_state["qubits"]["qA1"]["f_01"] == 6.31e9
        # …and the LIVE chip is byte-identical (Sync/Apply is the only door)
        assert _live_bytes(env["chip"]) == live_before

    def test_store_reflects_staged_values(self, env):
        c = env["client"]
        c.post("/load", data={"folder": str(env["chip"])})
        c.post(f"/dataset/{env['uid_same']}/load-state")
        ctx = _active(env["app"])
        assert ctx["store"].merged["qubits"]["qA1"]["f_01"] == 6.31e9

    def test_pending_edits_gate_with_force(self, env):
        c = env["client"]
        c.post("/load", data={"folder": str(env["chip"])})
        c.post("/field/edit", data={"dot_path": "qubits.qA1.f_01",
                                    "value": "6.26e9"})
        r = c.post(f"/dataset/{env['uid_same']}/load-state")
        assert r.status_code == 409
        assert "unsaved edits" in r.get_data(as_text=True)
        assert "force=1" in r.get_data(as_text=True)
        r = c.post(f"/dataset/{env['uid_same']}/load-state?force=1")
        assert r.status_code == 200

    def test_chip_mismatch_gate_with_force_chip(self, env):
        c = env["client"]
        c.post("/load", data={"folder": str(env["chip"])})
        r = c.post(f"/dataset/{env['uid_other']}/load-state")
        assert r.status_code == 409
        assert "DIFFERENT chip" in r.get_data(as_text=True)
        assert "force_chip=1" in r.get_data(as_text=True)
        r = c.post(f"/dataset/{env['uid_other']}/load-state?force_chip=1")
        assert r.status_code == 200
        ctx = _active(env["app"])
        wc_state = json.loads(
            (ctx["working_copy"].working_folder / "state.json")
            .read_text(encoding="utf-8"))
        assert "qZ9" in wc_state["qubits"]


class TestArchiveFallback:
    def test_no_chip_loaded_opens_archive(self, env):
        r = env["client"].post(f"/dataset/{env['uid_same']}/load-state")
        assert r.headers.get("HX-Redirect") == "/qubits"
        ctx = _active(env["app"])
        assert ctx is not None and ctx.get("origin") == "dataset_archive"

    def test_mode_archive_is_the_explicit_escape(self, env):
        c = env["client"]
        c.post("/load", data={"folder": str(env["chip"])})
        r = c.post(f"/dataset/{env['uid_same']}/load-state?mode=archive")
        assert r.headers.get("HX-Redirect") == "/qubits"
        ctx = _active(env["app"])
        assert ctx.get("origin") == "dataset_archive"

    def test_active_archive_context_falls_back_too(self, env):
        # staging INTO a read-only archive is refused by doctrine — the
        # button then behaves like the no-chip case.
        c = env["client"]
        c.post(f"/dataset/{env['uid_same']}/load-state")      # archive open
        r = c.post(f"/dataset/{env['uid_other']}/load-state")
        assert r.headers.get("HX-Redirect") == "/qubits"
        assert _active(env["app"]).get("origin") == "dataset_archive"


class TestUiWiring:
    def test_detail_carries_result_slot_and_archive_button(self, env):
        c = env["client"]
        body = c.get(f"/dataset/{env['uid_same']}",
                     headers={"HX-Request": "true"}).get_data(as_text=True)
        assert 'id="ds-load-state-result"' in body
        assert "mode=archive" in body
        assert "Open read-only" in body

    def test_confirm_fragment_targets_dataset_slot(self, env):
        c = env["client"]
        c.post("/load", data={"folder": str(env["chip"])})
        body = c.post(
            f"/dataset/{env['uid_other']}/load-state").get_data(as_text=True)
        assert 'hx-target="#ds-load-state-result"' in body
