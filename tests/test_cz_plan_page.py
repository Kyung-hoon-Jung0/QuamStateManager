"""CZ gate plan page (/cz-plan, /cz-plan/data, /cz-plan/history): the route layer.

The physics is pinned in test_cz_gate_plan.py; here: the sidebar entry under Chip Status, the
all-pairs table, the JSON one operation hands the plots (curves, waveform trajectory, card),
honest refusals, and every route answering without a 500 on real chip folders when present.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app


def _chip():
    flux = {"__class__": "quam.components.pulses.SquarePulse", "length": 40, "amplitude": 0.12}
    snz = {"__class__": "quam.components.pulses.SquarePulse", "length": 32, "amplitude": 0.08}
    state = {
        "qubits": {
            "q1": {"id": "q1", "f_01": 5.0e9, "anharmonicity": -200e6, "freq_vs_flux_01_quad_term": -8e9,
                   "z": {"flux_point": "joint", "operations": {"cz_flux": flux}}},
            "q2": {"id": "q2", "f_01": 4.0e9, "anharmonicity": -210e6, "freq_vs_flux_01_quad_term": 9e9,
                   "z": {"flux_point": "min", "operations": {"cz_flux_t": snz}}},
            "q3": {"id": "q3", "f_01": 4.5e9, "anharmonicity": -200e6, "z": {"flux_point": "joint"}},
        },
        "qubit_pairs": {
            "q1-2": {"id": "q1-2", "qubit_control": "#/qubits/q1", "qubit_target": "#/qubits/q2",
                     "moving_qubit": "control", "macros": {
                         "cz_unipolar": {"__class__": "x.CZGate", "flux_pulse_qubit": "#/qubits/q1/z/operations/cz_flux",
                                         "fidelity": {"InterleavedRB": 0.991, "InterleavedRB_load_id": 12}},
                         "cz_two": {"__class__": "x.CZGateTwoFlux", "flux_pulse_qubit": "#/qubits/q1/z/operations/cz_flux",
                                    "flux_pulse_target": "#/qubits/q2/z/operations/cz_flux_t"},
                         "cz": "#./cz_unipolar"}},
            "q3-2": {"id": "q3-2", "qubit_control": "#/qubits/q3", "qubit_target": "#/qubits/q2",
                     "moving_qubit": "control", "macros": {
                         "cz": {"__class__": "x.CZGate", "flux_pulse_qubit": {"__class__": "quam.components.pulses.SquarePulse",
                                                                             "length": 20, "amplitude": 0.1}}}},
        },
    }
    return state, {"wiring": {}}


def _client(tmp_path, folder=None):
    if folder is None:
        folder = tmp_path / "chips" / "live"
        folder.mkdir(parents=True)
        s, w = _chip()
        (folder / "state.json").write_text(json.dumps(s), encoding="utf-8")
        (folder / "wiring.json").write_text(json.dumps(w), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(folder)}).status_code in (200, 302)
    return c


@pytest.fixture
def client(tmp_path):
    return _client(tmp_path)


def test_the_menu_sits_under_chip_status_right_after_z_line_distortion(client):
    html = client.get("/cz-plan").get_data(as_text=True)
    status = html[html.index('id="chip-status-subnav"'):]
    status = status[:status.index("</ul>")]
    # customer feedback 2026-10-15: both views describe the chip's state -> Chip Status
    assert status.index('href="/zline"') < status.index('href="/cz-plan"')
    assert 'class="active">CZ gate plan</a>' in status


def test_the_table_lists_every_operation_with_its_operating_point(client):
    html = client.get("/cz-plan", headers={"HX-Request": "true"}).get_data(as_text=True)
    assert html.count('class="clickable-row czp-row') == 4                # cz_unipolar, cz_two, the alias cz, q3-2 cz
    assert 'data-op="qubit_pairs.q1-2.macros.cz"' in html and ">alias</small>" in html
    # q1 at its upper sweet spot, 0.12 V, |-8e9| Hz/V^2 -> 5.0 - 0.1152 GHz
    assert "4.8848 / 4.0000" in html
    assert "quad-term estimate" in html and "0.9910" in html
    # q3 has no quad term: no frequency, and the table says why instead of a number
    assert "freq_vs_flux_01_quad_term is missing" in html


def test_one_operation_hands_the_plots_curves_waveforms_and_its_card(client):
    d = client.get("/cz-plan/data?op=qubit_pairs.q1-2.macros.cz_two").get_json()
    assert d["ok"] and [q["name"] for q in d["qubits"]] == ["q1", "q2"]
    c = d["qubits"][0]["curve"]
    assert len(c["amplitude_v"]) == len(c["quad_hz"]) and c["model_hz"] is None
    # both move; each trajectory is the pulse's own samples mapped through the same estimate
    t0, t1 = d["trajectory"]
    assert len(t0["frequency_hz"]) == 40 and len(t1["frequency_hz"]) == 32
    assert t0["frequency_hz"][5] == pytest.approx(5e9 - 8e9 * 0.12 ** 2)
    assert t1["frequency_hz"][5] == pytest.approx(4e9 + 9e9 * 0.08 ** 2)       # min: moves up
    assert [o["operation"] for o in d["pair_ops"]] == ["cz_unipolar", "cz_two", "cz"]
    assert d["calibration"]["unknown"] and d["calibration"]["last_set"] is None


def test_unknown_operations_and_no_chip_are_honest(client, tmp_path):
    r = client.get("/cz-plan/data?op=qubit_pairs.q1-2.macros.nope")
    assert r.status_code == 404 and "not a CZ operation" in r.get_json()["error"]
    blank = create_app(testing=True, instance_path=str(tmp_path / "_blank")).test_client()
    assert blank.get("/cz-plan/data?op=x").status_code == 409
    assert blank.get("/cz-plan/history").status_code == 409
    assert blank.get("/cz-plan").status_code == 200


def test_history_says_unknown_rather_than_inventing_a_time(client):
    h = client.get("/cz-plan/history").get_json()
    assert h["ok"] and set(h["ops"]) == {"qubit_pairs.q1-2.macros.cz_unipolar", "qubit_pairs.q1-2.macros.cz_two",
                                         "qubit_pairs.q1-2.macros.cz", "qubit_pairs.q3-2.macros.cz"}
    assert all(o["last_set"] is None and o["unknown"] for o in h["ops"].values())


# Real chip folders, when this machine has them (a full and three partial feature sets).
_REAL = [Path(p) for p in os.environ.get("SM_CZ_PLAN_CHIPS", "").split(os.pathsep) if p]


@pytest.mark.skipif(not _REAL, reason="no real chip folders named in SM_CZ_PLAN_CHIPS")
@pytest.mark.parametrize("folder", _REAL, ids=lambda p: p.name)
def test_every_route_answers_on_a_real_chip(tmp_path, folder):
    c = _client(tmp_path, folder)
    page = c.get("/cz-plan", headers={"HX-Request": "true"})
    assert page.status_code == 200
    ops = [line.split('data-op="')[1].split('"')[0] for line in page.get_data(as_text=True).splitlines()
           if 'data-op="' in line]
    assert ops
    for op in ops:
        r = c.get("/cz-plan/data?op=" + op)
        assert r.status_code == 200, (op, r.get_data(as_text=True)[:300])
        d = r.get_json()
        for ln in d["row"]["lines"]:
            p = ln["point"]
            assert p["frequency_hz"] is not None or p["reason"], (op, ln["name"])
    assert c.get("/cz-plan/history").status_code == 200
