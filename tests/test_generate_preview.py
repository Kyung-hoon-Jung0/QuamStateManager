"""Drives the Generate-Config live-waveform-preview behavioral check
(tests/generate_preview_selfcheck.cjs) under node + jsdom.

Verifies generate_preview.js maps each previewable populate row (1Q DRAG,
readout, CZ variants, CR drive) to the right synth (qclass, params) and that
focusing/editing a cell POSTs /api/pulse/synth and renders into the preview
panel. Skips without node + jsdom.

It also pins, in-process, that every qclass generate_preview.js emits actually
synthesizes through the (chip-independent) /api/pulse/synth route with NO chip
loaded — the wizard has no store, so it relies on the qclass branch.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "generate_preview_selfcheck.cjs"

# The qclasses generate_preview.js sends, with a minimal valid param set each.
_PREVIEW_QCLASSES = {
    "DragCosinePulse": {"length": 40, "amplitude": 0.1, "axis_angle": 0,
                        "alpha": 0.0, "anharmonicity": -2.0e8, "detuning": 0},
    "SquareReadoutPulse": {"length": 1000, "amplitude": 0.1},
    "SquarePulse": {"length": 100, "amplitude": 1.0},
    "_FlatTopGaussianPulse": {"amplitude": 0.1, "flat_length": 100,
                              "smoothing_length": 20, "post_zero_padding_length": 20},
    "_CosineBipolarPulse": {"amplitude": 0.1, "flat_length": 100,
                            "smoothing_length": 20, "post_zero_padding_length": 20},
    "SNZPulse": {"amplitude": 0.2, "flat_length": 80, "t_phi_eff": 0, "padding": 20},
    "ErfSquarePulse": {"amplitude": 0.1, "flat_length": 100, "risetime_samples": 16,
                       "post_zero_padding_length": 20},
}


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_generate_preview_selfcheck_passes():
    r = subprocess.run(["node", str(_SELFCHECK)], capture_output=True, text=True, cwd=str(_ROOT))
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)


def test_preview_qclasses_synth_without_a_chip():
    """Every qclass the wizard preview emits must synthesize via the store-
    independent /api/pulse/synth path (no chip loaded in the wizard)."""
    from quam_state_manager.web.app import create_app

    app = create_app()
    client = app.test_client()
    headers = {"Origin": "http://localhost"}   # same-origin (the CSRF guard)
    for qclass, params in _PREVIEW_QCLASSES.items():
        resp = client.post("/api/pulse/synth",
                           json={"qclass": qclass, "params": params}, headers=headers)
        assert resp.status_code == 200, (qclass, resp.status_code)
        data = resp.get_json()
        assert data and data.get("ok"), (qclass, data and data.get("error"))
        plot = data.get("plot") or {}
        assert plot.get("ok") and plot.get("traces"), (qclass, plot)
