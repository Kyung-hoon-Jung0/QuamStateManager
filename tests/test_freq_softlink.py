"""Inspector f_01 ↔ RF_frequency soft link (server-side mirror in /qubit/<name>/edit)
plus the _freq_twin_path / _maybe_mirror_freq helpers.

The calibration nodes write f_01 and the matching RF_frequency to the same value;
RF_frequency is the carrier the hardware actually plays (the config IF is inferred
from it), f_01 is bookkeeping. Editing one in the inspector mirrors the twin WHEN
they were equal (coupled), and leaves a deliberate detuning untouched. Advisory
toggle (freq_sync) and pointer-encoded twins are honored.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app
from quam_state_manager.web.routes import _freq_twin_path


def _state(f_01=5.0e9, xy_rf=5.0e9, res_f01=7.0e9, res_rf=7.0e9, xy_rf_pointer=False):
    xy_rf_val = "#/qubits/q1/f_01" if xy_rf_pointer else xy_rf
    return {
        "qubits": {
            "q1": {
                "id": "q1",
                "f_01": f_01,
                "anharmonicity": -2.0e8,
                "xy": {"RF_frequency": xy_rf_val, "intermediate_frequency": 1.0e8},
                "resonator": {"f_01": res_f01, "RF_frequency": res_rf},
            },
        },
        "qubit_pairs": {},
        "active_qubit_names": ["q1"],
    }


def _wiring():
    return {"wiring": {"qubits": {"q1": {}}}, "network": {"host": "10.0.0.1"}}


def _make_client(tmp_path: Path, state: dict):
    folder = tmp_path / "chip"
    folder.mkdir(exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_wiring()), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    c.post("/load", data={"folder": str(folder)})
    c._app = app
    return c


@pytest.fixture
def client(tmp_path):
    return _make_client(tmp_path, _state())


def _store(client):
    app = client._app
    name = list(app.config["contexts"].keys())[0]
    return app.config["contexts"][name]["store"]


def _edit(client, dot_path, value, freq_sync=None):
    data = {"dot_path": dot_path, "value": str(value)}
    if freq_sync is not None:
        data["freq_sync"] = freq_sync
    return client.post("/qubit/q1/edit", data=data)


# ---------------------------------------------------------------------------
# _freq_twin_path
# ---------------------------------------------------------------------------

class TestFreqTwinPath:
    def test_qubit_f01_to_xy_rf(self):
        assert _freq_twin_path("qubits.q1.f_01") == "qubits.q1.xy.RF_frequency"

    def test_xy_rf_to_qubit_f01(self):
        assert _freq_twin_path("qubits.q1.xy.RF_frequency") == "qubits.q1.f_01"

    def test_res_f01_to_res_rf(self):
        assert _freq_twin_path("qubits.q1.resonator.f_01") == "qubits.q1.resonator.RF_frequency"

    def test_res_rf_to_res_f01(self):
        assert _freq_twin_path("qubits.q1.resonator.RF_frequency") == "qubits.q1.resonator.f_01"

    def test_resonator_rule_wins_over_bare_f01(self):
        # The .resonator.f_01 rule must be checked before the bare .f_01 rule so a
        # resonator path never maps to the xy drive.
        assert _freq_twin_path("qubits.q1.resonator.f_01") == "qubits.q1.resonator.RF_frequency"

    def test_non_freq_fields(self):
        assert _freq_twin_path("qubits.q1.anharmonicity") is None
        assert _freq_twin_path("qubits.q1.T1") is None
        assert _freq_twin_path("qubits.q1.xy.intermediate_frequency") is None

    def test_non_qubit_f01_is_none(self):
        # An unrelated `.f_01` suffix elsewhere must not get a phantom twin.
        assert _freq_twin_path("twpas.t1.spectroscopy.f_01") is None
        assert _freq_twin_path("something.f_01") is None
        assert _freq_twin_path("qubit_pairs.q1-2.f_01") is None


# ---------------------------------------------------------------------------
# inspector mirror
# ---------------------------------------------------------------------------

class TestInspectorMirror:
    def test_edit_f01_mirrors_xy_rf_when_equal(self, client):
        _edit(client, "qubits.q1.f_01", "8.0e9")
        s = _store(client)
        assert s.get_value("qubits.q1.f_01") == 8.0e9
        assert s.get_value("qubits.q1.xy.RF_frequency") == 8.0e9   # mirrored

    def test_edit_xy_rf_mirrors_f01_when_equal(self, client):
        _edit(client, "qubits.q1.xy.RF_frequency", "8.0e9")
        s = _store(client)
        assert s.get_value("qubits.q1.xy.RF_frequency") == 8.0e9
        assert s.get_value("qubits.q1.f_01") == 8.0e9

    def test_resonator_pair_mirrors(self, client):
        _edit(client, "qubits.q1.resonator.f_01", "7.5e9")
        s = _store(client)
        assert s.get_value("qubits.q1.resonator.f_01") == 7.5e9
        assert s.get_value("qubits.q1.resonator.RF_frequency") == 7.5e9
        # the xy pair is untouched
        assert s.get_value("qubits.q1.f_01") == 5.0e9

    def test_detuned_pair_not_mirrored(self, client):
        s = _store(client)
        # Detune first (sync off): xy_RF=6e9 while f_01 stays 5e9.
        _edit(client, "qubits.q1.xy.RF_frequency", "6.0e9", freq_sync="0")
        assert s.get_value("qubits.q1.xy.RF_frequency") == 6.0e9
        assert s.get_value("qubits.q1.f_01") == 5.0e9
        # Now edit f_01 with sync ON — twin (6e9) != old f_01 (5e9) → no mirror.
        _edit(client, "qubits.q1.f_01", "8.0e9")
        assert s.get_value("qubits.q1.f_01") == 8.0e9
        assert s.get_value("qubits.q1.xy.RF_frequency") == 6.0e9   # detuning respected

    def test_freq_sync_off_no_mirror(self, client):
        _edit(client, "qubits.q1.f_01", "8.0e9", freq_sync="0")
        s = _store(client)
        assert s.get_value("qubits.q1.f_01") == 8.0e9
        assert s.get_value("qubits.q1.xy.RF_frequency") == 5.0e9   # not mirrored

    def test_non_freq_field_touches_nothing_else(self, client):
        _edit(client, "qubits.q1.anharmonicity", "-1.9e8")
        s = _store(client)
        assert s.get_value("qubits.q1.anharmonicity") == -1.9e8
        assert s.get_value("qubits.q1.f_01") == 5.0e9
        assert s.get_value("qubits.q1.xy.RF_frequency") == 5.0e9

    def test_pointer_twin_skipped(self, tmp_path):
        # xy.RF_frequency is a #/ pointer to f_01 (already hard-linked) → the mirror
        # must skip it (no crash, no literal write over the pointer).
        client = _make_client(tmp_path, _state(xy_rf_pointer=True))
        _edit(client, "qubits.q1.f_01", "8.0e9")
        s = _store(client)
        assert s.get_value("qubits.q1.f_01") == 8.0e9
        assert s.get_value("qubits.q1.xy.RF_frequency") == "#/qubits/q1/f_01"  # pointer intact

    def test_mirror_emits_toast(self, client):
        html = _edit(client, "qubits.q1.f_01", "8.0e9").data.decode()
        assert "linked" in html and "status-bar" in html

    def test_no_mirror_no_toast(self, client):
        html = _edit(client, "qubits.q1.f_01", "8.0e9", freq_sync="0").data.decode()
        assert "f₀₁↔RF linked" not in html


class TestBulkPointerGuardSignal:
    """The bulk soft-link must NOT auto-couple a pointer-encoded RF twin (its resolved
    display looks equal to f_01) — Apply would clobber the #/ pointer with a literal.
    The JS guard keys on data-is-pointer; verify the server emits it on pointer cells."""

    def test_pointer_freq_cell_is_marked(self, tmp_path):
        import re
        client = _make_client(tmp_path, _state(xy_rf_pointer=True))
        html = client.get("/bulk").data.decode()
        # the xy.RF_frequency cell (a #/ pointer here) carries data-is-pointer for the JS
        assert re.search(
            r'data-dot-path="qubits\.q1\.xy\.RF_frequency"[^>]*data-is-pointer="1"', html)

    def test_literal_freq_cell_not_marked(self, tmp_path):
        import re
        client = _make_client(tmp_path, _state())  # xy.RF_frequency is a literal
        html = client.get("/bulk").data.decode()
        m = re.search(r'data-dot-path="qubits\.q1\.xy\.RF_frequency"[^>]*>', html)
        assert m and 'data-is-pointer' not in m.group(0)
