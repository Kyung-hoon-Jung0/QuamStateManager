"""QA F5 — an Interactive click on ANOTHER chip's run.

Two defects, one journey:

* The cross-chip confirm named the loaded chip after its PARENT folder
  (``history.chip_name_for`` on a standalone ``<x>/<chip>`` folder says
  ``"chip"`` / ``"quam_states"``), not the name the topbar shows, and neither
  side read the docs/20 v2 ladder's tier-1 ``extras.chip_name``.
* The popup then offered Apply / Apply All on rows the loaded chip does not
  have, and a press showed the raw ``KeyError`` text. Pinned under jsdom by
  ``tests/plot_apply_missing_selfcheck.cjs`` against the REAL app.js.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

_ROOT = Path(__file__).resolve().parent.parent


def _chip(folder: Path, extras: dict | None = None) -> Path:
    folder.mkdir(parents=True)
    state = {"qubits": {"q1": {"id": "q1", "f_01": 6.0e9}},
             "qubit_pairs": {}, "active_qubit_names": ["q1"]}
    if extras is not None:
        state["extras"] = extras
    wiring = {"wiring": {"qubits": {"q1": {"xy": {"opx_output": "MW/1/2"}}}},
              "network": {"host": "10.0.0.1"}}
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")
    return folder


@pytest.fixture
def client(tmp_path):
    return create_app(testing=True,
                      instance_path=str(tmp_path / "_inst")).test_client()


class TestActiveTokenNamesTheChipLikeTheTopbar:
    def test_standalone_folder_is_named_after_itself_not_its_parent(self, client, tmp_path):
        live = _chip(tmp_path / "chip" / "260907_KRS_5Q")
        client.post("/load", data={"folder": str(live)})
        j = client.get("/chip/active-token").get_json()
        assert j["loaded"] is True
        assert j["name"] == "260907_KRS_5Q", j["name"]      # was "chip"

    def test_generic_container_parent_is_never_the_name(self, client, tmp_path):
        live = _chip(tmp_path / "quam_states" / "KRS_5Q")
        client.post("/load", data={"folder": str(live)})
        assert client.get("/chip/active-token").get_json()["name"] == "KRS_5Q"

    def test_declared_chip_name_is_shown_too(self, client, tmp_path):
        live = _chip(tmp_path / "chip" / "260907_KRS_5Q",
                     extras={"chip_name": "KRISS_CZ"})
        client.post("/load", data={"folder": str(live)})
        name = client.get("/chip/active-token").get_json()["name"]
        assert "260907_KRS_5Q" in name and "KRISS_CZ" in name, name


class TestRunSideNameReadsTheLadder:
    def test_a_runs_declared_chip_name_wins_over_the_data_folder_label(self, tmp_path):
        from quam_state_manager.web import routes as R
        qs = _chip(tmp_path / "KRISS_CZ_260906" / "2026-09-07" / "#9_res_spec_014634"
                   / "quam_state", extras={"chip_name": "IQCC_QOP37_1Q"})
        token, name = R._run_chip_identity(qs)
        assert token
        assert name == "IQCC_QOP37_1Q", name              # was "KRISS_CZ_260906"

    def test_undeclared_run_keeps_the_path_label(self, tmp_path):
        from quam_state_manager.web import routes as R
        qs = _chip(tmp_path / "LabZ" / "2026-09-07" / "#3_rabi_010101" / "quam_state")
        assert R._run_chip_identity(qs)[1] == "LabZ"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_popup_never_offers_apply_for_a_missing_field():
    proc = subprocess.run(
        ["node", str(_ROOT / "tests" / "plot_apply_missing_selfcheck.cjs")],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT), timeout=120)
    if proc.returncode == 2 and "jsdom not installed" in (proc.stderr or ""):
        pytest.skip("jsdom not installed")
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
