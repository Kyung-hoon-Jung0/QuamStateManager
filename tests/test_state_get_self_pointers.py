"""A-17: store and agent reads follow the grids' self-pointer semantics."""

from __future__ import annotations

import json
import logging

import pytest

from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.pointer_path import resolve_field_target
from quam_state_manager.web.app import create_app

OPS = "qubits.q1.xy.operations"
PULSE = {"amplitude": 0.3, "length": 40}


@pytest.fixture
def reader(tmp_path, request):
    operations = {
        "x180": "#./x180_DragCosine",
        "x180_DragCosine": PULSE,
        "dangling": "#./missing_pulse",
        "cycle": "#./cycle",
        "x90": {"length": "#../length_alias"},
        "length_alias": "#./shared_length",
        "shared_length": 40,
    }
    folder = tmp_path / "chip"
    folder.mkdir()
    state = {"qubits": {"q1": {"xy": {"operations": operations}}}}
    state["qubits"]["q1"]["xy"]["LO_frequency"] = "#/output/frequency"
    state["output"] = "#/port"
    state["port"] = {"frequency": 5_000_000_000}
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text("{}", encoding="utf-8")
    if request.param == "store":
        store = QuamStore(folder)

        def read(path):
            return store.resolve_value(path), None

    else:
        client = create_app(testing=True, instance_path=str(tmp_path / "instance")).test_client()
        assert client.post("/load", data={"folder": str(folder)}).status_code == 302

        def read(path):
            response = client.get("/api/agent/state", query_string={"path": path})
            assert response.status_code == 200, response.get_json()
            body = response.get_json()
            assert body["ok"] and body["path"] == path
            assert body["is_pointer"] is True
            return body["resolved"], body

    return read, state


@pytest.mark.parametrize("reader", ["store", "api"], indirect=True)
def test_alias(reader):
    read, state = reader
    value, body = read(f"{OPS}.x180")
    assert value == PULSE
    assert resolve_field_target(state, f"{OPS}.x180")["resolved_path"] == f"{OPS}.x180_DragCosine"
    if body:
        assert body["value"] == "#./x180_DragCosine"
        assert body["resolved_path"] == f"{OPS}.x180_DragCosine"


@pytest.mark.parametrize("reader", ["store", "api"], indirect=True)
def test_through_alias(reader):
    read, state = reader
    value, body = read(f"{OPS}.x180.amplitude")
    assert value == 0.3
    assert resolve_field_target(state, f"{OPS}.x180.amplitude")["resolved_value"] == value
    if body:
        assert body["value"] == 0.3
        assert body["resolved_path"] == f"{OPS}.x180_DragCosine.amplitude"
        assert body["source_file"] == "state"


@pytest.mark.parametrize("reader", ["store", "api"], indirect=True)
def test_dangling(reader, caplog):
    read, _ = reader
    with caplog.at_level(logging.WARNING):
        value, body = read(f"{OPS}.dangling")
    assert value == "#./missing_pulse"
    assert any("pointer" in r.message.lower() and "dangling" in r.message
               for r in caplog.records if r.levelno >= logging.WARNING)
    if body:
        assert body["value"] == value
        assert body["resolved_path"] is None


@pytest.mark.parametrize("reader", ["store", "api"], indirect=True)
@pytest.mark.timeout(10, func_only=True)
def test_self_cycle(reader, caplog):
    read, _ = reader
    with caplog.at_level(logging.WARNING):
        value, body = read(f"{OPS}.cycle")
    assert value == "#./cycle"
    assert any("pointer" in r.message.lower() and "cycle" in r.message
               for r in caplog.records if r.levelno >= logging.WARNING)
    if body:
        assert body["value"] == value
        assert body["resolved_path"] is None


@pytest.mark.parametrize("reader", ["store", "api"], indirect=True)
def test_parent_then_self_chain(reader):
    read, state = reader
    path = f"{OPS}.x90.length"
    value, body = read(path)
    assert value == 40
    target = resolve_field_target(state, path)
    assert [hop["pointer"] for hop in target["chain"]] == ["#../length_alias", "#./shared_length"]
    if body:
        assert body["value"] == "#../length_alias"
        assert body["resolved_path"] == f"{OPS}.shared_length"


@pytest.mark.parametrize("reader", ["store", "api"], indirect=True)
def test_absolute_target_crossing_pointer(reader):
    """Keep the legacy resolver's embedded-target-pointer behavior."""
    read, _ = reader
    value, body = read("qubits.q1.xy.LO_frequency")
    assert value == 5_000_000_000
    if body:
        assert body["value"] == "#/output/frequency"
        assert body["source_file"] == "state"
