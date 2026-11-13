"""A run diff shows a pulse CLASS change: a SquarePulse that became a
WaveformPulse between two runs is a counted, listed difference, first in the
table; a diff with no class change is unchanged."""
import json

import pytest

from quam_state_manager.web.app import create_app


@pytest.fixture
def app(tmp_path):
    return create_app(testing=True, instance_path=str(tmp_path / "_app_instance"))


def _run(date, n, pulse):
    run = date / f"#{n}_test_01000{n}"
    run.mkdir(parents=True)
    run.joinpath("node.json").write_text(json.dumps({
        "metadata": {"name": "test", "status": "successful"},
        "data": {"parameters": {"model": {"qubits": ["q1"]}}, "outcomes": {}},
        "id": n, "parents": [], "created_at": f"2026-05-29T01:00:0{n}",
    }), encoding="utf-8")
    run.joinpath("data.json").write_text("{}", encoding="utf-8")
    qs = run / "quam_state"
    qs.mkdir()
    qs.joinpath("state.json").write_text(json.dumps({
        "qubit_pairs": {"p1": {"macros": {"cz": {"flux_pulse": pulse}}}}}), encoding="utf-8")
    qs.joinpath("wiring.json").write_text("{}", encoding="utf-8")


SQUARE = {"__class__": "quam.components.pulses.SquarePulse", "amplitude": 0.2, "length": 44}
WAVE = {"__class__": "quam.components.pulses.WaveformPulse", "amplitude": 0.2, "length": 44}


def _get(app, tmp_path, second):
    from quam_state_manager.core.dataset import DatasetStore
    from quam_state_manager.web import routes as _r
    date = tmp_path / "ds" / "2026-05-29"
    _run(date, 1, SQUARE)
    _run(date, 2, second)
    ds = DatasetStore(tmp_path / "ds")
    app.config["dataset_store"] = ds
    fk = _r._folder_key(ds.folder_path)
    return app.test_client().get(f"/dataset/{fk}:2/prev-state-diff").data.decode("utf-8")


def test_a_class_change_is_counted_and_listed_first(app, tmp_path):
    body = _get(app, tmp_path, WAVE)
    assert "qubit_pairs.p1.macros.cz.flux_pulse.__class__" in body
    assert "1 changed (1 class)" in body
    assert "WaveformPulse" in body
    tbody = body.split("<tbody>", 1)[1]
    first_row = tbody.split("</tr>", 1)[0]
    assert "__class__" in first_row


def test_no_class_change_reads_as_before(app, tmp_path):
    body = _get(app, tmp_path, dict(SQUARE, amplitude=0.3))
    assert "__class__" not in body
    assert "1 changed</span>" in body
    assert "class)" not in body


def test_the_folder_diff_keeps_class_only_on_request(tmp_path):
    from quam_state_manager.core.history import diff_state_folders
    for n, p in ((1, SQUARE), (2, WAVE)):
        d = tmp_path / str(n)
        d.mkdir()
        d.joinpath("state.json").write_text(json.dumps({"x": {"p": p}}), encoding="utf-8")
        d.joinpath("wiring.json").write_text("{}", encoding="utf-8")
    plain = [e.dot_path for e in diff_state_folders(tmp_path / "1", tmp_path / "2")]
    kept = [e.dot_path for e in diff_state_folders(tmp_path / "1", tmp_path / "2", keep_class=True)]
    assert plain == []
    assert kept == ["x.p.__class__"]
