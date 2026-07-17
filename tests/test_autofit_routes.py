"""Autofit web surface (docs/56 §5): the one-button flow end-to-end through
the Flask client (sim backend — zero hardware), the status poll + badge
payload, the mutual-exclusion + edit-lock guards, and the ledger report."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(testing=True, instance_path=str(tmp_path / "inst"))
    c = app.test_client()
    c._app = app
    return c


def _wait_done(client, timeout=60.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        d = client.get("/autofit/status").get_json()
        st = (d.get("state") or {})
        if not d.get("active") and st.get("status") in ("done", "failed",
                                                        "aborted"):
            return d
        time.sleep(0.2)
    raise AssertionError("plan did not finish in time")


class TestPage:
    def test_page_renders_with_readiness_and_presets(self, client):
        r = client.get("/autofit")
        assert r.status_code == 200
        body = r.get_data(as_text=True)
        assert "Run plan" in body
        assert "1Q bringup" in body and "CZ tuneup" in body
        assert "autofit-readiness" in body

    def test_sidebar_carries_the_autofit_entry(self, client):
        body = client.get("/").get_data(as_text=True)
        assert 'href="/autofit"' in body
        assert "autofit-nav-badge" in body


class TestOneButtonSimFlow:
    def test_full_1q_plan_from_the_button_to_the_report(self, client):
        r = client.post("/autofit/start", json={
            "preset": "1q_bringup", "backend": "sim", "autonomy": "full"})
        assert r.status_code == 200, r.get_json()
        run_id = r.get_json()["plan_run_id"]

        d = _wait_done(client)
        st = d["state"]
        assert st["status"] == "done"
        assert st["plan_run_id"] == run_id
        # every generator-backed step passed for every sim qubit
        for sid in ("qubit_spec", "power_rabi", "ramsey", "t1", "echo",
                    "res_spec", "readout_freq", "iq_blobs"):
            for t in ("qA1", "qA2", "qA3"):
                assert st["board"][sid][t]["state"] == "pass", (sid, t)
        assert st["review_queue"] == []

        # the sim world's LIVE files really converged (write-path parity)
        sim_live = Path(client._app.instance_path) / "autofit" / "sim" / "live_chip"
        state = json.loads((sim_live / "state.json").read_text())
        assert set(state["qubits"]) == {"qA1", "qA2", "qA3"}

        # ledger-backed report
        lr = client.get("/autofit/ledger").get_json()
        assert lr["ok"] and lr["run"] == run_id
        events = {e["event"] for e in lr["events"]}
        assert {"plan_started", "verdict", "decision", "plan_done"} <= events

        # badge payload rides the scheduler poll
        sched = client.get("/scheduler/status").get_json()
        assert sched["autofit"]["status"] == "done"
        assert sched["autofit"]["running"] is False

    def test_cz_preset_sim_skips_the_lab_only_step(self, client):
        r = client.post("/autofit/start", json={
            "preset": "cz_tuneup", "backend": "sim", "autonomy": "full"})
        assert r.status_code == 200, r.get_json()
        d = _wait_done(client)
        st = d["state"]
        assert st["status"] == "done"
        assert st["board"]["chevron"]["qA2-qA1"]["state"] == "pass"
        assert st["board"]["cond_phase"]["qA2-qA1"]["state"] == "pass"
        # phase compensation has no sim generator → benign skip, no defer
        assert st["board"]["phase_comp"]["qA2-qA1"]["state"] == "skipped"
        assert st["review_queue"] == []

    def test_double_start_is_refused(self, client):
        r = client.post("/autofit/start", json={
            "preset": "1q_bringup", "backend": "sim"})
        assert r.status_code == 200
        r2 = client.post("/autofit/start", json={
            "preset": "1q_bringup", "backend": "sim"})
        # either still running (409) or already finished (200) — never a 500;
        # with 3 qubits × 8 steps the run outlives this immediate re-POST
        assert r2.status_code == 409
        _wait_done(client)

    def test_abort_endpoint(self, client):
        client.post("/autofit/start", json={"preset": "1q_bringup",
                                            "backend": "sim"})
        r = client.post("/autofit/abort")
        assert r.status_code == 200
        d = _wait_done(client)
        assert d["state"]["status"] in ("aborted", "done")


class TestGuards:
    def test_mutators_locked_while_autofit_active(self, client, monkeypatch):
        from quam_state_manager.core.autofit import engine as af_engine
        monkeypatch.setattr(af_engine, "is_active", lambda inst: True)
        r = client.post("/save")
        assert r.status_code == 409
        assert r.get_json()["error"] == "autofit_running"
        # scheduler control is locked too (two-masters closure §7b-B3)
        r = client.post("/scheduler/start", json={})
        assert r.status_code == 409
        r = client.post("/scheduler/queue/add", json={})
        assert r.status_code == 409
        # reads stay live
        assert client.get("/scheduler/status").status_code == 200
        assert client.get("/autofit/status").status_code == 200

    def test_autofit_start_refused_while_scheduler_runs(self, client,
                                                        monkeypatch):
        from quam_state_manager.core import scheduler as sched
        monkeypatch.setattr(sched, "is_active", lambda inst: True)
        r = client.post("/autofit/start", json={"preset": "1q_bringup",
                                                "backend": "sim"})
        assert r.status_code == 409
        assert "Scheduler" in r.get_json()["error"]

    def test_resolve_requires_calibrations_folder(self, client):
        r = client.post("/autofit/resolve", json={"preset": "1q_bringup"})
        assert r.status_code == 400
        assert "calibrations folder" in r.get_json()["error"]

    def test_bad_preset_is_400(self, client):
        r = client.post("/autofit/start", json={"preset": "nope",
                                                "backend": "sim"})
        assert r.status_code == 400


class TestReviewAutonomyViaRoutes:
    def test_review_run_restores_the_sim_chip(self, client):
        r = client.post("/autofit/start", json={
            "preset": "1q_bringup", "backend": "sim", "autonomy": "review"})
        assert r.status_code == 200
        d = _wait_done(client)
        assert d["state"]["status"] == "done"
        lr = client.get("/autofit/ledger").get_json()
        assert any(e["event"] == "plan_restored" and e.get("ok")
                   for e in lr["events"])
