"""The pulse inspector is a VIEW of up to four pulses (docs/141 4k, superseding
the 2026-08-27 overlay bar): every pulse is time × voltage, so any set can
share one plot. The server renders one section per pulse in view -- a CZ
macro's qubit flux + coupler flux by default, or the 2-4 pulses picked for
Compare -- each synthesized, each with the colour its traces wear in the
shared plot; the view bar drops/adds pulses by re-rendering the same route.
Route pins here; the drawing + view bar are pinned by pulse_overlay_selfcheck.cjs."""
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
                        "x180": {"amplitude": 0.2, "length": 40, "__class__": "quam.components.pulses.SquarePulse"},
                        "x90": {"amplitude": 0.1, "length": 40, "__class__": "quam.components.pulses.SquarePulse"},
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


def _detail(client, query: str):
    html = client.get(f"/pulse/detail?{query}").data.decode()
    m = re.search(r'<script id="pulse-detail-data"[^>]*>(.*?)</script>', html, re.S)
    assert m, "detail JSON missing"
    return html, json.loads(m.group(1))


def test_cz_macro_companion_is_in_view_by_default(client):
    _, d = _detail(client, f"path={PAIR}.flux_pulse_qubit")
    assert d["mode"] == "group"
    assert [p["path"] for p in d["pulses"]] == [f"{PAIR}.flux_pulse_qubit", f"{PAIR}.coupler_flux_pulse"]
    q, c = d["pulses"]
    assert q["role"] == "qubit" and c["role"] == "coupler"
    assert c["label"].endswith("cz_unipolar · coupler") and q["label"].endswith("cz_unipolar · qubit")
    assert c["plot"]["ok"] is True and c["plot"]["traces"], "a REAL synth, not a stub"
    assert q["color"] != c["color"], "one colour per pulse, shared by its section and its traces"
    assert "overlays" not in d, "the 2026-08-27 overlay list is gone -- sections are the one model"
    # and it is symmetric: opening the coupler pulse brings the qubit pulse along
    _, d2 = _detail(client, f"path={PAIR}.coupler_flux_pulse")
    assert [p["path"] for p in d2["pulses"]] == [f"{PAIR}.coupler_flux_pulse", f"{PAIR}.flux_pulse_qubit"]


def test_no_companion_is_never_invented(client):
    _, d = _detail(client, "path=qubit_pairs.qA1-qA2.macros.cz_solo.flux_pulse_qubit")
    assert d["mode"] == "single" and len(d["pulses"]) == 1, "an empty coupler slot must not produce a section"


def test_channel_operations_are_alternatives_not_companions(client):
    """x90 is not played WITH x180 -- a channel's operations reach the view
    only through the picker (or Compare), never as a default section."""
    _, d = _detail(client, "path=qubits.qA1.xy.operations.x180")
    assert d["mode"] == "single" and [p["path"] for p in d["pulses"]] == ["qubits.qA1.xy.operations.x180"]


def test_compare_is_the_same_route_with_paths(client):
    _, d = _detail(client, "path=qubits.qA1.xy.operations.x180&paths=qubits.qA1.xy.operations.x180,qubits.qA1.xy.operations.x90")
    assert d["mode"] == "compare"
    assert [p["path"] for p in d["pulses"]] == ["qubits.qA1.xy.operations.x180", "qubits.qA1.xy.operations.x90"]
    assert all(p["plot"]["ok"] for p in d["pulses"])


def test_view_bar_markup_present(client):
    html, _ = _detail(client, f"path={PAIR}.flux_pulse_qubit")
    assert 'class="pulse-overlay-bar pulse-view-bar"' in html and 'class="pulse-overlay-pick"' in html
    assert html.count('class="pulse-overlay-chip on"') == 2, "one chip per pulse in view"
    assert html.count("data-drop-path=") == 2, "with more than one pulse in view, every chip can be dropped"
    assert f'data-view-paths="{PAIR}.flux_pulse_qubit,{PAIR}.coupler_flux_pulse"' in html
    assert f'data-view-main="{PAIR}.flux_pulse_qubit"' in html
    # a single-pulse view offers no drop
    solo, _ = _detail(client, "path=qubits.qA1.xy.operations.x180")
    assert solo.count('class="pulse-overlay-chip on"') == 1 and "data-drop-path=" not in solo


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_pulse_overlay_selfcheck():
    if subprocess.run(["node", "-e", "require('jsdom')"], capture_output=True, cwd=str(_ROOT)).returncode != 0:
        pytest.skip("jsdom not installed for node")
    res = subprocess.run(["node", str(_ROOT / "tests" / "pulse_overlay_selfcheck.cjs")],
                         capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT))
    assert res.returncode == 0, res.stdout + "\n" + res.stderr
    assert "ok -" in res.stdout and "FAIL" not in res.stdout + res.stderr
