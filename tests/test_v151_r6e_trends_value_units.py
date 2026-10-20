"""R6e: Trends units must describe data.json numbers from the same run."""
import json
import pytest
from tests.test_v151_r5c_trends_units_real_shapes import _run, _scale, _var, h5py, np, _fresh
from quam_state_manager.web.app import create_app
from quam_state_manager.web import routes


@pytest.mark.parametrize("stored,plotted,expected", [
    (80928.0, 8.0928e-5, {}), (80928.0, 80928.0, {"T2_echo": "ns"}),
    (80928.0, 80928.000001, {"T2_echo": "ns"}),
])
def test_trends_requires_same_run_value_agreement(tmp_path, stored, plotted, expected):
    root = tmp_path / "data"
    def build(f):
        _var(f, "T2_echo", [stored], ("qubit",), units="ns")
        _var(f, "T2_echo_error", [stored / 10], ("qubit",), units="ns")
    _run(root, 1, "26_echo", {"T2_echo": plotted, "T2_echo_error": plotted * 1e-9}, build)
    app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
    c = app.test_client()
    c.post("/workspace/add", data={"folder": str(root)})
    assert c.get("/trends/units?experiment=26_echo").get_json()["units"] == expected


def test_value_evidence_uses_entity_labels_and_long_name(tmp_path):
    root = tmp_path / "data"
    run = root / "2026-10-04" / "#1_25_T1_070001"
    def build(f):
        # Deliberately reverse the entity order between the files.
        del f["qubit"]
        _scale(f, "qubit", np.array(["q2", "q1"], dtype=h5py.string_dtype()))
        _var(f, "tau", [22000.0, 11000.0], ("qubit",), units="ns", long_name="T1")
    _run(root, 1, "25_T1", {"t1": 11000.0}, build)
    data = {"fit_results": {"q1": {"t1": 11000.0}, "q2": {"t1": 22000.0}}}
    (run / "data.json").write_text(json.dumps(data), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
    c = app.test_client(); c.post("/workspace/add", data={"folder": str(root)})
    assert c.get("/trends/units?experiment=25_T1").get_json()["units"] == {"t1": "ns"}


def test_one_mismatching_run_prevents_a_misleading_common_unit(tmp_path):
    root = tmp_path / "data"
    def build(f):
        _var(f, "T2_echo", [80000.0], ("qubit",), units="ns")
    _run(root, 1, "26_echo", {"T2_echo": 80000.0}, build)
    _run(root, 2, "26_echo", {"T2_echo": 8e-5}, build)
    app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
    c = app.test_client(); c.post("/workspace/add", data={"folder": str(root)})
    assert c.get("/trends/units?experiment=26_echo").get_json()["units"] == {}


def test_missing_value_evidence_has_no_unit(tmp_path):
    root = tmp_path / "data"
    def build(f):
        # No entity coordinate: equality by guessed row order proves nothing.
        d = f.create_dataset("T2_echo", data=np.array([80000.0]))
        d.attrs["units"] = "ns"
    _run(root, 1, "26_echo", {"T2_echo": 80000.0}, build)
    app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
    c = app.test_client(); c.post("/workspace/add", data={"folder": str(root)})
    assert c.get("/trends/units?experiment=26_echo").get_json()["units"] == {}


@pytest.mark.parametrize("missing", [False, True])
def test_all_plotted_runs_need_value_evidence(tmp_path, missing):
    root = tmp_path / "data"
    def build(f):
        _var(f, "T2_echo", [80000.0], ("qubit",), units="ns")
    for rid in range(1, 5):
        _run(root, rid, "26_echo", {"T2_echo": 8e-5 if rid == 1 else 80000.0}, build)
    if missing:
        next(root.glob("*/#1_*/ds_fit.h5")).unlink()
    app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
    c = app.test_client(); c.post("/workspace/add", data={"folder": str(root)})
    assert c.get("/trends/units?experiment=26_echo").get_json()["units"] == {}
