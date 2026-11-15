"""The Pairs CSV export (``/export?entity=pairs``) reads a pair ``detuning``
through the shared unit fallback (``units.stored_value_unit``): a column
never mixes V and Hz.

Rule: every numeric value plausible volts -> ``detuning [V]`` (as before);
every one judged Hz -> ``detuning [Hz]``; both kinds on one chip -> the
header names no unit, each cell keeps its stored number and a
``detuning_unit`` column beside it names that row's unit.
"""
import csv
import io
import json

from quam_state_manager.web.app import create_app


def _client(tmp_path, detunings):
    chip = tmp_path / "chip"
    chip.mkdir()
    qubits = {}
    pairs = {}
    for i, d in enumerate(detunings):
        a, b = f"q{2 * i + 1}", f"q{2 * i + 2}"
        qubits[a] = {"id": a}
        qubits[b] = {"id": b}
        pairs[f"{a}-{b}"] = {"id": f"{a}-{b}", "qubit_control": f"#/qubits/{a}",
                             "qubit_target": f"#/qubits/{b}", "detuning": d}
    (chip / "state.json").write_text(json.dumps({"qubits": qubits, "qubit_pairs": pairs}),
                                     encoding="utf-8")
    (chip / "wiring.json").write_text('{"wiring":{"qubits":{}}}', encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
    c = app.test_client()
    c.post("/load", data={"folder": str(chip)})
    return c


def _rows(c):
    r = c.get("/export?entity=pairs&props=detuning")
    assert r.status_code == 200
    rows = list(csv.reader(io.StringIO(r.get_data(as_text=True).lstrip("\ufeff"))))
    return rows[0], rows[1:]


def test_plausible_volts_export_exactly_as_before(tmp_path):
    head, body = _rows(_client(tmp_path, [0.12, -0.03]))
    assert head == ["id", "detuning [V]"]
    assert [r[1] for r in body] == ["0.12", "-0.03"]


def test_a_column_judged_hz_is_headed_hz(tmp_path):
    head, body = _rows(_client(tmp_path, [509318255.4322, 4.1e8]))
    assert head == ["id", "detuning [Hz]"]
    assert [r[1] for r in body] == ["509318255.4322", "410000000.0"]


def test_mixed_units_keep_each_number_and_name_its_unit(tmp_path):
    head, body = _rows(_client(tmp_path, [0.12, 509318255.4322]))
    assert head == ["id", "detuning", "detuning_unit"]
    assert [(r[1], r[2]) for r in body] == [("0.12", "V"), ("509318255.4322", "Hz")]


def test_a_bare_pairs_export_has_pair_columns(tmp_path):
    c = _client(tmp_path, [-0.1659])
    head = c.get("/export?entity=pairs").get_data(as_text=True).lstrip("\ufeff").splitlines()[0]
    assert "qubit_control" in head and "detuning" in head and "T1" not in head


def test_an_all_unstated_column_names_its_empty_unit_per_row(tmp_path):
    """Values between the volt bound and the Hz floor carry no unit: the header
    names none and each row's unit cell is empty, never a guessed V."""
    head, body = _rows(_client(tmp_path, [509.3, 77.7]))
    assert head == ["id", "detuning", "detuning_unit"]
    assert [(r[1], r[2]) for r in body] == [("509.3", ""), ("77.7", "")]
