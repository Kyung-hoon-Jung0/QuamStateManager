"""CZ labels, ledger explanations, scroll fit, and played coupler amplitudes."""
import json
import hashlib
import os
import re
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.core import cz_gate_plan as C
from quam_state_manager.core.hub_store import HubStore
from quam_state_manager.web import routes
from tests.test_cz_plan_page import _chip, _client

ROOT = Path(__file__).resolve().parents[1]


def state_with_coupler():
    state, wiring = _chip()
    state["ports"] = {"analog_outputs": {"c1": {"1": {}}}}
    port = "#/ports/analog_outputs/c1/1"
    state["qubits"]["q1"]["z"]["opx_output"] = port
    state["couplers"] = {"c12": {"id": "c12", "opx_output": port, "operations": {
        "flux": {"id": "flux", "__class__": "quam.components.pulses.SquarePulse",
                 "length": 40, "amplitude": .1}}}}
    pair = state["qubit_pairs"]["q1-2"]
    pair["coupler"] = "#/couplers/c12"
    pair["macros"]["cz_unipolar"]["coupler_flux_pulse"] = {
        "id": "flux", "amplitude": .3, "__class__": "quam.components.pulses.SquarePulse", "length": 40}
    return state, wiring


def selected(state):
    rows, conversions = C.operations(state)
    return next(r for r in rows if r["operation"] == "cz_unipolar"), C.chip_map(rows, conversions)


def test_coupler_played_label_and_no_frequency():
    state, _ = state_with_coupler()
    row, mapped = selected(state)
    line = row.get("coupler")
    assert line is not None, "a separate played coupler record is required"
    assert line["amplitude_v"] == row["coupler_amplitude_v"] == .1
    assert line["amp_text"] == "+100 mV"
    assert line["played_pulse_path"] == "couplers.c12.operations.flux"
    assert line["pulse_path"] in line["amp_title"]
    assert any("macro's copy says +300 mV; the coupler plays +100 mV" in n for n in row["notes"])
    assert "a coupler pulse also plays (+100 mV): these bare-qubit frequencies omit the coupler's shift" in row["notes"]
    def keys(value):
        if isinstance(value, dict):
            for key, child in value.items():
                yield key
                yield from keys(child)
        elif isinstance(value, list):
            for child in value:
                yield from keys(child)
    assert not any("freq" in k or k in ("point", "kind") for k in keys(line))
    assert all(s["qubit"] != "c12" for s in mapped["segments"] + mapped["refused"])
    assert all("amp_text" in s for s in mapped["segments"] + mapped["refused"])
    assert mapped["pairs"]["q1-2"]["coupler_amp_text"] == "+100 mV"
    from quam_state_manager.core.physical_units import channel_of
    assert channel_of(state, line["played_pulse_path"] + ".amplitude") == "couplers.c12"


@pytest.mark.parametrize("form", ["label", "pointer", "missing", "no_element", "lobes"])
def test_coupler_resolution_forms(form):
    state, _ = state_with_coupler()
    pair = state["qubit_pairs"]["q1-2"]
    macro = pair["macros"]["cz_unipolar"]
    if form == "label":
        macro["coupler_flux_pulse"] = "flux"
    elif form == "pointer":
        macro["coupler_flux_pulse"] = "#/couplers/c12/operations/flux"
    elif form == "missing":
        state["couplers"]["c12"]["operations"].clear()
    elif form == "no_element":
        pair.pop("coupler")
    else:
        state["couplers"]["c12"]["operations"]["flux"].update(
            __class__="quam.components.pulses.SNZPulse", neg_offset_v=.02)
    row, _ = selected(state)
    line = row.get("coupler")
    assert line is not None
    if form in ("missing", "no_element"):
        assert line["amp_text"] == chr(8212) and "not playable" in line["refusal"]
        assert "(amplitude unknown)" in " ".join(row["notes"])
    else:
        assert line["amplitude_v"] == .1 and line["amp_text"] == "+100 mV"
        if form == "lobes":
            assert line["lobes"][1]["amplitude_v"] == pytest.approx(-.12)
            assert "mirror lobe -120 mV" in line["amp_title"]


def test_coupler_table_and_sort(tmp_path):
    state, wiring = state_with_coupler()
    folder = tmp_path / "state"
    folder.mkdir()
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")
    client = _client(tmp_path, folder)
    html = client.get("/cz-plan").get_data(as_text=True)
    row = re.search(r'<tr[^>]*data-op="qubit_pairs.q1-2.macros.cz_unipolar".*?</tr>', html, re.S)[0]
    assert 'class="muted czp-amp-coupler"' in row and '>coupler +100 mV</small>' in row
    assert float(re.search(r'data-s-amp="([^"]*)"', row)[1]) == pytest.approx(.12)


def test_no_coupler_table_identity(tmp_path):
    html = _client(tmp_path).get("/cz-plan").get_data(as_text=True)
    table = re.search(r'<table id="czp-table".*?</table>', html, re.S)[0]
    assert hashlib.sha256(table.encode()).hexdigest() == "44a97da42eea30c2b0a6f51ffc1d1cd7ae5b82bfa58139bf441f4bbcc272842f"


def test_empty_ledger_explains_data_folder(tmp_path):
    from tests.test_hub_drawer import chip_dir
    client = _client(tmp_path)
    with HubStore(chip_dir({"app": client.application})):
        pass
    answer = client.get("/cz-plan/history").get_json()
    assert answer["mode"] == "ledger", answer
    assert answer["note"] and "data folder" in answer["note"]
    assert answer["link"]["url"] == "/hub/link-folder"
    assert all(o["last_set"] is None and o["unknown"] for o in answer["ops"].values())


def test_ledger_unknown_without_folder_note(tmp_path, monkeypatch):
    client = _client(tmp_path)
    monkeypatch.setattr(routes, "_cz_plan_history", lambda rows: {
        "mode": "ledger", "rows": {}, "notes": {}, "ledger": {"events": 2}})
    answer = client.get("/cz-plan/history").get_json()
    assert answer["mode"] == "ledger"
    assert answer["note"] == "No setting of these amplitudes is recorded in this chip's history."
    assert answer.get("link") is None


def test_table_scroll_css():
    css = (ROOT / "quam_state_manager/web/static/style.css").read_text(encoding="utf-8")
    rules = re.findall(r"\.czp-table-wrap\s*\{([^}]+)\}", css)
    assert rules and all("max-height" not in r and "overflow-y" not in r for r in rules)
    assert "overflow-x: auto" in " ".join(rules)
    assert re.search(r"\.czp-table-wrap\.czp-wrap-fits\s*\{\s*overflow: visible", css)
    assert re.search(r"\.czp-wrap-fits \.czp-table thead th\s*\{[^}]*position: sticky;[^}]*top: calc", css)


@pytest.mark.parametrize("mode", ["history", "fit", "card", "map", "map_segment", "map_refused"])
def test_browser_contracts(mode):
    result = subprocess.run(["node", "tests/cz_batch_b_selfcheck.cjs", mode], cwd=ROOT,
                            capture_output=True, text=True, env=os.environ.copy())
    assert result.returncode == 0, result.stdout + result.stderr
