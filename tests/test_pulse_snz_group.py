"""docs/240 -- an SNZ (both-movers) CZ is ONE event on two flux lines, so the
pulse inspector shows both whichever row it was opened from: the control slot,
the target slot, or a qubit's `z.operations` entry that provably mirrors one
of them. A mirror is proven from the state, never guessed: the amplitude and
every absolute pointer must point into ONE pair-macro slot."""
from __future__ import annotations

import json
import re

import pytest

from quam_state_manager.web.app import create_app

PAIR = "qubit_pairs.qA2-qA1.macros.cz_SNZ"
SLOT = "#/qubit_pairs/qA2-qA1/macros/cz_SNZ"


def _mirror(slot: str) -> dict:
    return {"length": "#./inferred_length",
            "amplitude": f"{SLOT}/{slot}/amplitude",
            "flat_length": f"{SLOT}/{slot}/flat_length",
            "axis_angle": None}


def _state() -> dict:
    return {
        "qubits": {
            "qA1": {"z": {"operations": {
                "cz_SNZ_flux_pulse_qA1_qA2": _mirror("flux_pulse_target"),
            }}},
            "qA2": {"z": {"operations": {
                "cz_SNZ_flux_pulse_qA2_qA1": _mirror("flux_pulse_qubit"),
                # its own amplitude: NOT a mirror, whatever its timings say
                "cz_own_amp": dict(_mirror("flux_pulse_qubit"), amplitude=0.5),
                # fields pointing into TWO slots: NOT a mirror
                "cz_two_slots": dict(_mirror("flux_pulse_qubit"),
                                     flat_length=f"{SLOT}/flux_pulse_target/flat_length"),
            }}},
        },
        "qubit_pairs": {"qA2-qA1": {"macros": {"cz_SNZ": {
            "flux_pulse_qubit": {"amplitude": 0.2, "length": 40, "flat_length": 20},
            "coupler_flux_pulse": None,
            "flux_pulse_target": {"amplitude": 0.1, "length": 40, "flat_length": 20},
        }}}},
        "active_qubit_names": ["qA1", "qA2"],
    }


@pytest.fixture
def client(tmp_path):
    (tmp_path / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (tmp_path / "wiring.json").write_text("{}", encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_app_instance"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(tmp_path)}).status_code in (200, 302)
    return c


def _detail(client, path: str) -> dict:
    html = client.get("/pulse/detail", query_string={"path": path}).data.decode()
    m = re.search(r'<script id="pulse-detail-data"[^>]*>(.*?)</script>', html, re.S)
    assert m, "detail JSON missing"
    return json.loads(m.group(1))


def _view(d):
    return [(p["path"], p["role"]) for p in d["pulses"]]


def test_opened_from_the_control_both_lines_show(client):
    d = _detail(client, f"{PAIR}.flux_pulse_qubit")
    assert d["mode"] == "group"
    assert _view(d) == [(f"{PAIR}.flux_pulse_qubit", "control"),
                        (f"{PAIR}.flux_pulse_target", "target")]


def test_opened_from_the_target_both_lines_show(client):
    """The reported gap: the target slot used to open alone."""
    d = _detail(client, f"{PAIR}.flux_pulse_target")
    assert d["mode"] == "group"
    assert _view(d) == [(f"{PAIR}.flux_pulse_target", "target"),
                        (f"{PAIR}.flux_pulse_qubit", "control")]
    assert d["pulses"][0]["label"].endswith("cz_SNZ · target")


def test_a_mirror_of_the_target_brings_the_control(client):
    d = _detail(client, "qubits.qA1.z.operations.cz_SNZ_flux_pulse_qA1_qA2")
    # the mirrored slot itself is this pulse again -- never drawn twice
    assert [p for p, _ in _view(d)] == ["qubits.qA1.z.operations.cz_SNZ_flux_pulse_qA1_qA2",
                                       f"{PAIR}.flux_pulse_qubit"]


def test_a_mirror_of_the_control_brings_the_target(client):
    d = _detail(client, "qubits.qA2.z.operations.cz_SNZ_flux_pulse_qA2_qA1")
    assert [p for p, _ in _view(d)] == ["qubits.qA2.z.operations.cz_SNZ_flux_pulse_qA2_qA1",
                                       f"{PAIR}.flux_pulse_target"]


@pytest.mark.parametrize("op", ["cz_own_amp", "cz_two_slots"])
def test_not_proven_a_mirror_opens_alone(client, op):
    d = _detail(client, f"qubits.qA2.z.operations.{op}")
    assert [p for p, _ in _view(d)] == [f"qubits.qA2.z.operations.{op}"]


def test_a_qubit_flux_without_a_target_keeps_its_role(client, tmp_path):
    """A coupler-chip CZ is unchanged: its qubit line is still `qubit`."""
    from tests.test_pulse_overlay import PAIR as UNI, _state as uni_state
    folder = tmp_path / "uni"
    folder.mkdir()
    (folder / "state.json").write_text(json.dumps(uni_state()), encoding="utf-8")
    (folder / "wiring.json").write_text("{}", encoding="utf-8")
    assert client.post("/load", data={"folder": str(folder)}).status_code in (200, 302)
    d = _detail(client, f"{UNI}.flux_pulse_qubit")
    assert [r for _, r in _view(d)] == ["qubit", "coupler"]
