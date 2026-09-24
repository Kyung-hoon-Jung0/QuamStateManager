"""Route tests for the on-load diagnostics surfacing: the auto error-banner,
the tray health badge, and the waveform findings appearing in the Explorer feed.
"""

from __future__ import annotations

import json

from quam_state_manager.web.app import create_app

READOUT = "quam.components.pulses.SquareReadoutPulse"


def _state(readout_amp: float) -> dict:
    return {
        "qubits": {"q1": {
            "id": "q1",
            "resonator": {
                "opx_output": "#/wiring/qubits/q1/rr/opx_output",
                "opx_input": "#/wiring/qubits/q1/rr/opx_input",
                "operations": {"readout": {"__class__": READOUT,
                                           "length": 640, "amplitude": readout_amp}},
            },
        }},
        "qubit_pairs": {},
        "ports": {
            "mw_outputs": {"con1": {"1": {"1": {
                "band": 2, "upconverter_frequency": 7.0e9, "full_scale_power_dbm": 0}}}},
            "analog_outputs": {},
            "mw_inputs": {"con1": {"1": {"1": {}}}},
        },
    }


def _wiring() -> dict:
    return {"wiring": {"qubits": {"q1": {
        "rr": {"opx_output": "#/ports/mw_outputs/con1/1/1",
               "opx_input": "#/ports/mw_inputs/con1/1/1"},
    }}}, "network": {"host": "x", "cluster_name": "t"}}


def _client(tmp_path, readout_amp):
    (tmp_path / "state.json").write_text(json.dumps(_state(readout_amp)), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps(_wiring()), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    client = app.test_client()
    client.post("/load", data={"folder": str(tmp_path)})
    return client


def test_banner_pops_when_chip_has_error(tmp_path):
    body = _client(tmp_path, 1.5).get("/diagnostics/banner").get_data(as_text=True)
    assert "diag-error-banner" in body
    assert "would crash a node run" in body


def test_banner_empty_when_chip_is_clean(tmp_path):
    resp = _client(tmp_path, 0.3).get("/diagnostics/banner")
    assert resp.status_code == 204
    assert resp.get_data(as_text=True) == ""


def test_banner_no_chip_is_204(tmp_path):
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    assert app.test_client().get("/diagnostics/banner").status_code == 204


def test_summary_badge_reflects_error(tmp_path):
    body = _client(tmp_path, 1.5).get("/diagnostics/summary").get_data(as_text=True)
    assert "diag-error" in body and "issue" in body


def test_summary_badge_healthy_when_clean(tmp_path):
    body = _client(tmp_path, 0.3).get("/diagnostics/summary").get_data(as_text=True)
    assert "healthy" in body


def test_waveform_finding_in_explorer_feed(tmp_path):
    feed = _client(tmp_path, 1.5).get("/diagnostics/findings.json").get_json()
    # the waveform range finding is Explorer-jumpable → in the value_spec bucket
    cats = [f["category"] for f in feed["value_spec"]]
    assert "waveform_range" in cats
    assert any(f["jump_path"].endswith("readout.amplitude") for f in feed["value_spec"])


def test_review_diagnostics_shows_the_errors_a_saved_filter_hid(tmp_path):
    """QA F-D: the pill choice persists (one global localStorage key), and the
    crash banner's "Review diagnostics" landed on a page whose saved filter hid
    the very errors it was about. The banner turns the error bucket back on, and
    any page whose filter hides errors says so next to the pills
    (diag_filter_entry_selfcheck.cjs, real app.js + real fragments)."""
    import shutil
    import subprocess
    from pathlib import Path

    import pytest
    if shutil.which("node") is None:
        pytest.skip("node not available")
    client = _client(tmp_path, 1.5)
    banner = client.get("/diagnostics/banner").get_data(as_text=True)
    assert "_diagShowBucket('error')" in banner
    diag = client.get("/diagnostics", headers={"HX-Request": "true"}).get_data(as_text=True)
    bp, dp = tmp_path / "banner.html", tmp_path / "diag.html"
    bp.write_text(banner, encoding="utf-8")
    dp.write_text(diag, encoding="utf-8")
    root = Path(__file__).resolve().parent.parent
    r = subprocess.run(["node", str(root / "tests" / "diag_filter_entry_selfcheck.cjs"), str(bp), str(dp)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=str(root), timeout=180)
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    assert "diag_filter_entry_selfcheck ok" in r.stdout
