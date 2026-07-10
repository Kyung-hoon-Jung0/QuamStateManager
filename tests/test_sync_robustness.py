"""Sync-robustness regression tests (feedback #5).

Locks in the server half of "ALWAYS stay synced": /state/live-diff can never
emit a non-JSON 500 or a fatal error on a transient burst; the drift settle-gate
can't pin the count at a stale 0 forever; the diff genuinely catches pulse edits;
and a clean auto-pull is surfaced (no longer silent).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.core import safe_io
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app


def _state() -> dict:
    return {"qubits": {"qA1": {
        "id": "qA1", "f_01": 6.25e9,
        "xy": {"operations": {"x180_DragCosine": {"length": 48, "amplitude": 0.11}}},
        "resonator": {"operations": {"readout": {"length": 1000, "amplitude": 0.04}}},
    }}, "qubit_pairs": {}, "active_qubit_names": ["qA1"]}


def _wiring() -> dict:
    return {"network": {"host": "1.1.1.1"}}


@pytest.fixture
def client(tmp_path: Path):
    (tmp_path / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps(_wiring()), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    c = app.test_client()
    c.post("/load", data={"folder": str(tmp_path)})
    c._app = app          # type: ignore[attr-defined]
    c._folder = tmp_path  # type: ignore[attr-defined]
    return c


def _active_ctx(app):
    return app.config["contexts"][app.config["active_context"]]


class TestLiveDiffRobustness:
    def test_livefileerror_is_503_transient_json(self, client, monkeypatch):
        # a QUAlibrate burst → safe_io can't settle the pair → LiveFileError. The
        # client must see a retryable 503 JSON, never a fatal/HTML error.
        def boom(wc, **k):
            raise safe_io.LiveFileError("writer mid-save")
        monkeypatch.setattr(routes.working_copy, "read_live", boom)
        r = client.get("/state/live-diff?with_live=1")
        assert r.status_code == 503
        assert r.is_json
        d = r.get_json()
        assert d["ok"] is False and d["transient"] is True

    def test_differ_error_is_json_500_never_html(self, client, monkeypatch):
        # the dominant "Live diff failed (network error)" cause: an error OUTSIDE the
        # old try (Differ/serialize) escaped as a Werkzeug HTML 500 the client's
        # r.json() mis-parsed. Now the whole body is guarded → always JSON.
        def boom(self, *a, **k):
            raise RuntimeError("odd pointer data")
        monkeypatch.setattr(routes.Differ, "diff", boom)
        r = client.get("/state/live-diff")
        assert r.status_code == 500
        assert r.is_json, "a Differ error must be JSON, never an HTML 500 page"
        assert r.get_json()["ok"] is False

    def test_missing_live_is_404_json(self, client, monkeypatch):
        def boom(wc, **k):
            raise FileNotFoundError()
        monkeypatch.setattr(routes.working_copy, "read_live", boom)
        r = client.get("/state/live-diff")
        assert r.status_code == 404 and r.is_json
        assert r.get_json()["ok"] is False

    def test_reads_harder_on_the_click_path(self, client, monkeypatch):
        # the explicit user-click read passes a larger attempts budget than the
        # background poll (the "increase the period to compensate" realization).
        seen = {}
        real = safe_io.read_state_wiring

        def spy(folder, *, attempts=None):
            seen["attempts"] = attempts
            return real(folder, attempts=attempts)
        monkeypatch.setattr(routes.working_copy.safe_io, "read_state_wiring", spy)
        client.get("/state/live-diff")
        assert seen.get("attempts") == 8


class TestPulseDriftDetected:
    def test_pulse_length_amp_edits_surface(self, client):
        app = client._app
        folder = client._folder
        with app.app_context():
            ctx = _active_ctx(app)
            routes._drift_baseline(ctx)               # baseline = the ORIGINAL live
            st = json.loads((folder / "state.json").read_text())
            st["qubits"]["qA1"]["xy"]["operations"]["x180_DragCosine"]["length"] = 56
            st["qubits"]["qA1"]["resonator"]["operations"]["readout"]["amplitude"] = 0.05
            (folder / "state.json").write_text(json.dumps(st))
            info = routes._compute_drift(ctx, full=True)
            paths = {e.dot_path for e in info["entries"]}
        assert "qubits.qA1.xy.operations.x180_DragCosine.length" in paths
        assert "qubits.qA1.resonator.operations.readout.amplitude" in paths


class TestSettleMaxDefer:
    def test_forces_a_read_after_k_defers(self, client, monkeypatch):
        app = client._app
        with app.app_context():
            ctx = _active_ctx(app)
            base = routes._drift_baseline(ctx)         # real read → baseline
            # the live now differs from the baseline...
            changed_state = json.loads(json.dumps(base["state"]))
            changed_state["qubits"]["qA1"]["f_01"] = 7.0e9
            monkeypatch.setattr(routes.working_copy, "read_live",
                                lambda wc, **k: (changed_state, base["wiring"]))
            # ...and the mtimes ADVANCE on every poll so the settle-gate would defer
            # forever without the cap.
            seq = {"n": 0}

            def advancing(_folder):
                seq["n"] += 1
                return (seq["n"], seq["n"])
            monkeypatch.setattr(routes.safe_io, "state_wiring_mtimes", advancing)
            counts = []
            for _ in range(5):
                info = routes._compute_drift(ctx)
                counts.append(info["count"] if info else 0)
        # the un-capped gate served 0 forever; the cap forces a read that sees the diff
        assert max(counts) > 0, f"max-defer cap never forced a read (counts={counts})"


class TestAutoPullSurfaced:
    def test_clean_auto_pull_surfaced_once(self, client):
        app = client._app
        with app.app_context():
            _active_ctx(app)["_auto_pulled"] = {"count": 3}
        r1 = client.get("/state/drift").get_json()
        assert r1.get("auto_pulled") == {"count": 3}
        r2 = client.get("/state/drift").get_json()
        assert "auto_pulled" not in r2, "auto_pulled must be one-shot (popped)"
