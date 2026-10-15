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


@pytest.mark.parametrize("shape", ["pair_alias", "macros_alias", "pairs_alias", "bad_pairs", "bad_pair", "bad_macros", "bad_macro"])
def test_collection_shapes_never_crash_and_unavailable_entries_are_explained(tmp_path, monkeypatch, shape):
    from quam_state_manager.core.loader import QuamStore
    from quam_state_manager.web import routes
    s, w = _chip()
    pairs = s["qubit_pairs"]
    if shape == "pair_alias":
        pairs["alias"] = "#/qubit_pairs/q1-2"
    elif shape == "macros_alias":
        s["shared"] = pairs["q1-2"]["macros"]
        pairs["q1-2"]["macros"] = "#/shared"
    elif shape == "pairs_alias":
        s["shared"] = s.pop("qubit_pairs")
        s["qubit_pairs"] = "#/shared"
    elif shape == "bad_pairs":
        s["qubit_pairs"] = ["broken"]
    elif shape == "bad_pair":
        pairs["broken"] = "#/missing"
    elif shape == "bad_macros":
        pairs["q1-2"]["macros"] = ["broken"]
    else:
        pairs["q1-2"]["macros"]["cz_broken"] = 12
    store = QuamStore.from_dicts(s, w)
    monkeypatch.setattr(routes, "_store", lambda: store)
    monkeypatch.setattr(routes, "_active_ctx", lambda: None)
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    app.config["TESTING"] = False
    page = c.get("/cz-plan", headers={"HX-Request": "true"})
    data = c.get("/cz-plan/data?op=qubit_pairs.q1-2.macros.cz_two")
    hist = c.get("/cz-plan/history")
    assert page.status_code == hist.status_code == 200
    assert data.status_code == (404 if shape in ("bad_pairs", "bad_macros") else 200)
    if shape.startswith("bad_"):
        assert "unavailable (expected an object" in page.get_data(as_text=True)
        assert any("unavailable (expected an object" in r for r in hist.get_json()["unavailable"])
        if data.status_code == 404:
            assert data.get_json()["unavailable"]
    if shape == "pair_alias":
        alias = c.get("/cz-plan/data?op=qubit_pairs.alias.macros.cz_two").get_json()
        assert alias["trajectory"][0] is not None
        assert alias["trajectory"][0]["frequency_hz"][0] == pytest.approx(4.8848e9)
    if shape in ("macros_alias", "pairs_alias"):
        assert data.get_json()["trajectory"][0] is not None
        assert data.get_json()["trajectory"][0]["frequency_hz"][0] == pytest.approx(4.8848e9)


def test_registered_name_draws_its_resolved_waveform(tmp_path, monkeypatch):
    from quam_state_manager.core.loader import QuamStore
    from quam_state_manager.web import routes
    s, w = _chip()
    s["qubit_pairs"]["q1-2"]["macros"]["cz_unipolar"]["flux_pulse_qubit"] = "cz_flux"
    store = QuamStore.from_dicts(s, w)
    monkeypatch.setattr(routes, "_store", lambda: store)
    monkeypatch.setattr(routes, "_active_ctx", lambda: None)
    c = create_app(testing=True, instance_path=str(tmp_path / "_inst")).test_client()
    data = c.get("/cz-plan/data?op=qubit_pairs.q1-2.macros.cz_unipolar").get_json()
    assert data["row"]["amplitudes"] == ["qubits.q1.z.operations.cz_flux.amplitude"]
    assert len(data["trajectory"][0]["frequency_hz"]) == 40
    assert data["trajectory"][0]["frequency_hz"][0] == pytest.approx(4.8848e9)


@pytest.mark.parametrize("both", [False, True])
@pytest.mark.parametrize("cycles", [4, 16])
def test_play_time_override_converts_cycles_and_resizes_constant_pulses(tmp_path, monkeypatch, both, cycles):
    from quam_state_manager.core.loader import QuamStore
    from quam_state_manager.web import routes
    s, w = _chip()
    macro = s["qubit_pairs"]["q1-2"]["macros"]["cz_unipolar"]
    if both:
        macro["__class__"] = "x.CZGateBothMovers"
        macro["flux_pulse_high"] = macro.pop("flux_pulse_qubit")
        macro["flux_pulse_low"] = "#/qubits/q2/z/operations/cz_flux_t"
    macro["duration" if both else "duration_qubit"] = "#/played"
    s["played"] = cycles
    store = QuamStore.from_dicts(s, w)
    monkeypatch.setattr(routes, "_store", lambda: store)
    monkeypatch.setattr(routes, "_active_ctx", lambda: None)
    c = create_app(testing=True, instance_path=str(tmp_path / "_inst")).test_client()
    data = c.get("/cz-plan/data?op=qubit_pairs.q1-2.macros.cz_unipolar").get_json()
    assert data["row"]["duration_ns"] == 4 * cycles
    assert "clock cycles x 4 ns" in data["row"]["duration_note"]
    for k in range(2 if both else 1):
        trajectory = data["trajectory"][k]
        assert len(trajectory["frequency_hz"]) == 4 * cycles
        assert trajectory["playback_known"]
        assert all(f == pytest.approx(data["row"]["lines"][k]["point"]["frequency_hz"])
                   for f in trajectory["frequency_hz"])


@pytest.mark.parametrize("stored", [16, "#/runtime/duration"])
def test_standard_macro_duration_property_has_no_proven_unit(stored):
    from quam_state_manager.core import cz_gate_plan as C
    s, _w = _chip()
    s["qubit_pairs"]["q1-2"]["macros"]["cz_unipolar"]["duration"] = stored
    row = C.operations(s)[0][0]
    assert row["duration_ns"] is None
    assert row["duration_note"].startswith("unknown:")


def test_arbitrary_override_keeps_nominal_samples_and_names_that_limit(tmp_path, monkeypatch):
    from quam_state_manager.core.loader import QuamStore
    from quam_state_manager.web import routes
    s, w = _chip()
    pulse = s["qubits"]["q1"]["z"]["operations"]["cz_flux"]
    pulse.update(__class__="quam.components.pulses.GaussianPulse", sigma=8)
    s["qubit_pairs"]["q1-2"]["macros"]["cz_unipolar"]["duration_qubit"] = 16
    store = QuamStore.from_dicts(s, w)
    monkeypatch.setattr(routes, "_store", lambda: store)
    monkeypatch.setattr(routes, "_active_ctx", lambda: None)
    c = create_app(testing=True, instance_path=str(tmp_path / "_inst")).test_client()
    data = c.get("/cz-plan/data?op=qubit_pairs.q1-2.macros.cz_unipolar").get_json()
    assert data["row"]["duration_ns"] == 64
    assert len(data["trajectory"][0]["frequency_hz"]) == 40
    assert not data["trajectory"][0]["playback_known"]
    assert any("drawn waveform is the nominal one" in n and "64 ns" in n for n in data["notes"])


def test_unsupported_mover_is_explicitly_different_from_a_stationary_qubit(tmp_path, monkeypatch):
    from quam_state_manager.core.loader import QuamStore
    from quam_state_manager.web import routes
    s, w = _chip()
    s["qubits"]["q1"]["z"]["operations"]["cz_flux"]["__class__"] = "x.UnsupportedPulse"
    store = QuamStore.from_dicts(s, w)
    monkeypatch.setattr(routes, "_store", lambda: store)
    monkeypatch.setattr(routes, "_active_ctx", lambda: None)
    c = create_app(testing=True, instance_path=str(tmp_path / "_inst")).test_client()
    data = c.get("/cz-plan/data?op=qubit_pairs.q1-2.macros.cz_unipolar").get_json()
    assert data["trajectory"] == [None, None]
    assert [ln["moves"] for ln in data["row"]["lines"]] == [True, False]
    assert any("cannot draw this pulse" in n for n in data["notes"])


@pytest.mark.parametrize("provenance", ["first_record", "observed", "held_before_write", "run_uncertain_chip"])
def test_history_card_and_table_do_not_promote_observations_or_uncertain_writers(client, monkeypatch, provenance):
    from quam_state_manager.web import routes

    def history(rows):
        return {"mode": "ledger", "rows": {p: {"effective": [
            {"t": "2026-01-01T00:00:00Z", "t_us": 10, "run_id": 8,
             "kind": "run", "proven": True, "provenance": provenance}]}
            for row in rows for p in row["amplitudes"]}}

    monkeypatch.setattr(routes, "_cz_plan_history", history)
    data = client.get("/cz-plan/data?op=qubit_pairs.q1-2.macros.cz_unipolar").get_json()["calibration"]
    table = client.get("/cz-plan/history").get_json()["ops"]["qubit_pairs.q1-2.macros.cz_unipolar"]
    assert not data["proven"] and not table["proven"]
    if provenance != "run_uncertain_chip":
        assert data["last_set"] is None and table["last_set"] is None
        assert data["unknown"] and table["unknown"]
        assert "first recorded in run #8" in data["note"] and "first recorded in run #8" in table["note"]


@pytest.mark.parametrize("cycles", [None, 4])
def test_constant_pulse_without_nominal_length_never_invents_time(tmp_path, monkeypatch, cycles):
    from quam_state_manager.core.loader import QuamStore
    from quam_state_manager.web import routes
    s, w = _chip()
    del s["qubits"]["q1"]["z"]["operations"]["cz_flux"]["length"]
    if cycles is not None:
        s["qubit_pairs"]["q1-2"]["macros"]["cz_unipolar"]["duration_qubit"] = cycles
    monkeypatch.setattr(routes, "_store", lambda: QuamStore.from_dicts(s, w))
    monkeypatch.setattr(routes, "_active_ctx", lambda: None)
    c = create_app(testing=True, instance_path=str(tmp_path / "_inst")).test_client()
    data = c.get("/cz-plan/data?op=qubit_pairs.q1-2.macros.cz_unipolar").get_json()
    if cycles is None:
        assert data["trajectory"][0] is None
        assert any("cannot draw this pulse" in n and "length" in n for n in data["notes"])
    else:
        assert data["trajectory"][0] is not None
        assert len(data["trajectory"][0]["frequency_hz"]) == 4 * cycles
