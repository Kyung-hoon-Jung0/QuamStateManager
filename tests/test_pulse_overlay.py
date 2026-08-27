"""Pulse overlays (customer ask 2026-08-27): every pulse is time × voltage, so
any set can share one plot. The server hands the detail page the SAME
component's companions (a CZ macro's qubit flux + coupler flux) synthesized
and on by default; the client draws them and lets the user add any other
pulse. Route pins here; the drawing is pinned by pulse_overlay_selfcheck.cjs."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

_ROOT = Path(__file__).resolve().parent.parent
PAIR = "qubit_pairs.qA1-qA2.macros.cz_unipolar"


def _state() -> dict:
    return {
        "qubits": {
            "qA1": {
                "xy": {
                    "operations": {
                        "x180": {"amplitude": 0.2, "length": 40},
                        "x90": {"amplitude": 0.1, "length": 40},
                    },
                },
            },
        },
        "qubit_pairs": {
            "qA1-qA2": {
                "macros": {
                    "cz_unipolar": {
                        "flux_pulse_qubit": {"amplitude": 0.05, "length": 100},
                        "coupler_flux_pulse": {"amplitude": -0.12, "length": 100},
                        "phase_shift_control": 0.0,
                    },
                    # a macro whose coupler slot is genuinely empty
                    "cz_solo": {
                        "flux_pulse_qubit": {"amplitude": 0.03, "length": 60},
                        "coupler_flux_pulse": None,
                    },
                },
            },
        },
        "active_qubit_names": ["qA1"],
    }


@pytest.fixture
def client(tmp_path):
    (tmp_path / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (tmp_path / "wiring.json").write_text("{}", encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_app_instance"))
    c = app.test_client()
    r = c.post("/load", data={"folder": str(tmp_path)})
    assert r.status_code in (200, 302), r.data[:300]
    return c


def _detail_json(client, path: str) -> dict:
    html = client.get(f"/pulse/detail?path={path}").data.decode()
    m = re.search(r'<script id="pulse-detail-data"[^>]*>(.*?)</script>', html, re.S)
    assert m, "detail JSON missing"
    return json.loads(m.group(1))


def test_cz_macro_companion_is_offered_on_by_default(client):
    d = _detail_json(client, f"{PAIR}.flux_pulse_qubit")
    ov = d.get("overlays")
    assert ov and [o["path"] for o in ov] == [f"{PAIR}.coupler_flux_pulse"]
    assert ov[0]["label"] == "coupler_flux_pulse" and ov[0]["default_on"] is True
    assert ov[0]["plot"]["ok"] is True and ov[0]["plot"]["traces"], "a REAL synth, not a stub"
    # and it is symmetric: opening the coupler pulse offers the qubit pulse
    d2 = _detail_json(client, f"{PAIR}.coupler_flux_pulse")
    assert [o["path"] for o in d2["overlays"]] == [f"{PAIR}.flux_pulse_qubit"]


def test_no_companion_is_never_invented(client):
    d = _detail_json(client, "qubit_pairs.qA1-qA2.macros.cz_solo.flux_pulse_qubit")
    assert d["overlays"] == [], "an empty coupler slot must not produce an overlay"


def test_channel_operations_are_alternatives_not_companions(client):
    """x90 is not played WITH x180 — a channel's operations reach the plot
    only through the picker, never as a default overlay."""
    d = _detail_json(client, "qubits.qA1.xy.operations.x180")
    assert d["overlays"] == []


def test_overlay_bar_markup_present(client):
    html = client.get(f"/pulse/detail?path={PAIR}.flux_pulse_qubit").data.decode()
    assert 'class="pulse-overlay-bar"' in html and 'class="pulse-overlay-pick"' in html


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_pulse_overlay_selfcheck():
    node = shutil.which("node")
    if subprocess.run([node, "-e", "require('jsdom')"], capture_output=True, cwd=str(_ROOT)).returncode != 0:
        pytest.skip("jsdom not installed for node")
    res = subprocess.run([node, str(_ROOT / "tests" / "pulse_overlay_selfcheck.cjs")],
                         capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT))
    assert res.returncode == 0, res.stdout + "\n" + res.stderr
    assert "ok - the companion coupler pulse is drawn WITH the committed trace" in res.stdout
