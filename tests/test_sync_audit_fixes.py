"""Regression tests for the round-2 sync-audit P0 breakages.

The audit found six state-reflection breakages ("nothing about reflecting/
detecting/showing state changes may be broken"). The client ones are guarded
statically (JS isn't run headlessly); the server one (apply-to-live HX-Trigger)
is tested live.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

_ROOT = Path(__file__).resolve().parent.parent
_APP_JS = (_ROOT / "quam_state_manager" / "web" / "static" / "app.js").read_text(encoding="utf-8")
_TPL = _ROOT / "quam_state_manager" / "web" / "templates"


def _state() -> dict:
    return {"qubits": {"qA1": {"id": "qA1", "f_01": 6.25e9}},
            "qubit_pairs": {}, "active_qubit_names": ["qA1"]}


@pytest.fixture
def client(tmp_path: Path):
    (tmp_path / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps({"network": {"host": "1.1.1.1"}}), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    c = app.test_client()
    c.post("/load", data={"folder": str(tmp_path)})
    return c


class TestApplyToLiveTriggers:
    def test_apply_to_live_emits_drift_and_timeline_triggers_not_staterestored(self, client):
        # audit P0-6: the tray "apply to live" only swaps #pending-tray, so it MUST
        # HX-Trigger the drift + timeline surfaces or they stay stale until reload.
        # audit P1: but it must NOT fire stateRestored (which blanks an open inspector) —
        # use the dedicated stateHistoryChanged for the timeline refresh instead.
        client.post("/field/edit-batch", json={"updates": [
            {"dot_path": "qubits.qA1.f_01", "value": "6.3e9"}]})
        r = client.post("/state/apply-to-live")
        assert r.status_code == 200
        trig = r.headers.get("HX-Trigger", "")
        assert "liveDriftChanged" in trig and "stateHistoryChanged" in trig
        assert "stateRestored" not in trig


class TestClientGuards:
    """Static guards for the JS-only audit fixes (P0-1/2/3/4/5)."""

    def test_p0_1_state_pages_uses_real_bulk_route(self):
        # the dead "/bulkedit" path made Sync silently show pre-sync values on /bulk
        assert '"/bulkedit"' not in _APP_JS
        assert '"/bulk"' in _APP_JS

    def test_p0_2_3_afterswap_scrolls_below_fold_detail(self):
        # the compare/detail result lands below the timeline → must scroll into view
        assert "state-history-detail" in _APP_JS and "scrollIntoView" in _APP_JS
        assert "history-detail-area" in _APP_JS  # the wiring-page drawer sibling

    def test_p0_4_drift_event_bubbles_from_body(self):
        # a non-bubbling event on document never reaches the from:body listener
        assert _APP_JS.count('CustomEvent("liveDriftChanged", { bubbles: true })') >= 2
        assert 'document.dispatchEvent(new CustomEvent("liveDriftChanged"))' not in _APP_JS

    def test_p0_5_drift_repolls_after_apply(self):
        # doStateSync success + a body liveDriftChanged listener both re-poll the banner
        assert "window._pollDrift()" in _APP_JS
        assert 'addEventListener("liveDriftChanged", function () { poll(); })' in _APP_JS

    def test_p1_tray_markers_clear_on_zero_change_count(self):
        # the old clear was gated on the dead "#pending-tray.tray-empty" (only set with
        # NO active chip) → stale markers after a save/apply on a loaded chip
        tray = (Path(__file__).resolve().parent.parent / "quam_state_manager" / "web"
                / "templates" / "_pending_tray.html").read_text(encoding="utf-8")
        assert 'data-change-count="{{ change_count }}"' in tray
        assert 'getAttribute("data-change-count")' in _APP_JS
        # _swapPendingTray (the 7-caller funnel) now restores tray state itself
        swap = _APP_JS[_APP_JS.index("function _swapPendingTray"):][:600]
        assert "_restoreTrayState()" in swap


class TestP1P2Followups:
    def test_pin_retarget_preserves_open_diff(self):
        # Pin re-renders only the timeline (body=1 → #state-history-body), not the whole
        # #table-pane, so an open compare/diff in #state-history-detail survives.
        body = (_TPL / "_state_history_body.html").read_text(encoding="utf-8")
        assert "/label?" in body and "&body=1" in body
        assert 'hx-target="#state-history-body"' in body

    def test_backfill_residual_gate(self):
        ph = (_TPL / "_param_history.html").read_text(encoding="utf-8")
        assert 'data-pending-import-count="{{ pending_import_count }}"' in ph
        assert "data-pending-import-count" in _APP_JS
        # the old aligned-vs-indexed threshold-of-5 gate is gone
        assert "importable - experimentTotal < 5" not in _APP_JS

    def test_indexed_run_ids(self, tmp_path):
        from quam_state_manager.core.history import HistoryManager
        hm = HistoryManager(tmp_path / "instance")
        chip = tmp_path / "data" / "chipA" / "quam_state"
        assert hm.indexed_run_ids(chip) == set()      # fresh chip → empty
        conn = hm._open_index(chip)
        for ts, rid in [("20260101_000000", 7), ("20260101_000001", 9)]:
            conn.execute(
                "INSERT INTO param_history (timestamp, qubit, property, value, raw_pointer, "
                "trigger, run_id, experiment) VALUES (?,?,?,?,?,?,?,?)",
                (ts, "qA1", "f_01", "5e9", "", "experiment", rid, "exp"))
        conn.commit()
        conn.close()
        assert hm.indexed_run_ids(chip) == {7, 9}
