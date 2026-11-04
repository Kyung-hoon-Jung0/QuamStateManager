"""Pins for four walk findings (2026-11-04 GUI walk):

F1  an invalid gate-macro pulse object that no channel operation plays is a
    warning, not "an error that would crash a node run" (measured in a real
    env: calculate_waveform on the macro object raises, generate_config()
    succeeds because it only builds channel operations).
F10 the getting-started text no longer says Auto-Sync is off by default.
F14 the Resonators page shows the RF the hardware plays beside f_01, and the
    IF column says it is computed from RF.
F18 the Pulses search fires on ``input`` (paste / autofill / IME), not only on
    ``keyup``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from quam_state_manager.core import diagnostics
from quam_state_manager.core.loader import QuamStore
from quam_state_manager.web.app import create_app

BIPOLAR = "quam.components.pulses._CosineBipolarPulse"
SQUARE = "quam.components.pulses.SquarePulse"
TPL = Path(__file__).resolve().parents[1] / "quam_state_manager" / "web" / "templates"


def _chip(alias_whole: bool = False, in_ops: bool = False):
    macro_pulse = {"__class__": BIPOLAR, "length": 40, "flat_length": 46,
                   "amplitude": 0.25, "id": "cz_bipolar_pulse_q2"}
    z_ops = {"flux": {"__class__": SQUARE, "length": 100, "amplitude": 0.1}}
    if alias_whole:
        z_ops["cz_bipolar_pulse_q2"] = "#/qubit_pairs/q1-q2/macros/cz_bipolar/flux_pulse_qubit"
    if in_ops:
        z_ops["bad"] = dict(macro_pulse)
    state = {
        "qubits": {"q1": {"id": "q1", "z": {
            "opx_output": "#/ports/analog_outputs/con1/5/6", "operations": z_ops}}},
        "qubit_pairs": {"q1-q2": {"macros": {"cz_bipolar": {
            "flux_pulse_qubit": macro_pulse}}}},
        "ports": {"analog_outputs": {"con1": {"5": {"6": {"output_mode": "direct"}}}}},
    }
    return QuamStore.from_dicts(state, {"network": {"host": "x", "cluster_name": "t"}})


def _invalid(store):
    return [f for f in diagnostics.lint_state(store) if f.category == "waveform_invalid"]


class TestF1MacroPulseIsNotANodeCrash:
    def test_macro_only_object_is_a_warning(self):
        fs = _invalid(_chip())
        assert len(fs) == 1
        assert fs[0].severity == "warning"
        assert "does not build" in fs[0].message
        assert "would raise" in fs[0].message  # the object itself is still inconsistent

    def test_an_alias_operation_playing_the_whole_object_stays_an_error(self):
        fs = [f for f in _invalid(_chip(alias_whole=True))
              if f.location.startswith("qubit_pairs")]
        assert len(fs) == 1 and fs[0].severity == "error"

    def test_a_channel_operation_stays_an_error(self):
        fs = [f for f in _invalid(_chip(in_ops=True)) if ".operations." in f.location]
        assert len(fs) == 1 and fs[0].severity == "error"
        assert "generate_config() would raise" in fs[0].message


class TestF10AutoSyncDefault:
    def test_help_text_says_on_by_default(self):
        txt = (TPL / "_landing_getting_started.html").read_text(encoding="utf-8")
        assert "off by default" not in txt
        assert "on by default" in txt


@pytest.fixture
def client(tmp_path):
    live = tmp_path / "chip"
    live.mkdir()
    state = {
        "qubits": {"qA1": {"id": "qA1", "resonator": {
            "f_01": 7126237653.73, "RF_frequency": 7126087653.73,
            "LO_frequency": 7.46e9,
            "operations": {"readout": {"__class__": "quam.components.pulses.SquareReadoutPulse",
                                       "length": 1000, "amplitude": 0.1}}}}},
        "qubit_pairs": {}, "active_qubit_names": ["qA1"],
    }
    (live / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps({"network": {"host": "h"}}), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    return c


class TestF14ResonatorsShowWhatIsPlayed:
    def test_rf_column_beside_f01(self, client):
        html = client.get("/resonators").data.decode()
        assert "Readout RF" in html and "Resonator f<sub>01</sub>" in html
        row = html[html.index('data-qubit-id="qA1"'):]
        row = row[:row.index("</tr>")]
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        # f_01 then RF: two different numbers, RF = 7.12609 GHz
        assert "7.1262" in cells[2]
        assert "7.12609" in cells[3] or "7.1261" in cells[3]
        assert cells[2] != cells[3]

    def test_if_header_names_its_source(self, client):
        html = client.get("/resonators").data.decode()
        assert "Readout IF = RF - LO (the Readout RF column, not f_01)" in html


class TestF18SearchFiresOnInput:
    def test_pulses_search_listens_to_input(self):
        txt = (TPL / "_pulses.html").read_text(encoding="utf-8")
        m = re.search(r'name="q"[^>]*?hx-trigger="([^"]+)"', txt, re.S)
        assert m and m.group(1).startswith("input changed"), m and m.group(1)

    def test_global_search_listens_to_input(self):
        txt = (TPL / "base.html").read_text(encoding="utf-8")
        m = re.search(r'hx-get="/search" hx-trigger="([^"]+)"', txt)
        assert m and m.group(1).startswith("input changed")
