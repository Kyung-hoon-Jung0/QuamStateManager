"""Pins for the final Pulses browser findings in v1.3.0."""
import json
import subprocess
from pathlib import Path

import pytest

from tests.test_lab_doors import lab, _store, _v, _by_name, G, FL_C, FL_T
from tests.test_pulses_routes import _make_state, _make_wiring


@pytest.fixture
def client(tmp_path):
    from quam_state_manager.web.app import create_app
    folder = tmp_path / "chip"
    folder.mkdir()
    (folder / "state.json").write_text(json.dumps(_make_state()), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_make_wiring()), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(folder)}).status_code in (200, 302)
    c._app = app
    return c


@pytest.mark.parametrize("field,value", [("flat_length", 40), ("padding", 8)])
@pytest.mark.parametrize("mirror", [False, True])
def test_coupled_pulses_offer_the_existing_batch(lab, field, value, mirror):
    c, fake = lab
    source = "qubits.q1.z.operations.czl_q1" if mirror else G + ".flux_pulse_qubit"
    target = G + ".flux_pulse_qubit." + field
    partner = G + ".flux_pulse_target." + field
    before = _v(c, target)
    r = c.post("/pulse/edit", data={"path": source, "dot_path": source + "." + field,
                                     "mode": "value", "value": str(value)})
    assert r.status_code == 400
    payload = json.loads(r.headers.get("X-Lab-Refusal", "{}"))
    assert payload.get("lab_follow") == [{"dot_path": target, "value": value},
                                          {"dot_path": partner, "value": value}]
    assert not _store(c).change_log and _v(c, target) == before
    r = c.post("/field/edit-batch", json={"updates": payload["lab_follow"], "group": "new"})
    assert r.status_code == 200, r.data[:300]
    assert _v(c, target) == _v(c, partner) == value
    assert len(_store(c).change_log) == 2
    assert len({e.group_id for e in _store(c).change_log}) == 1
    assert c.post("/undo").status_code == 200
    assert _v(c, target) == _v(c, partner) == before
    assert not _store(c).change_log


def test_pulses_wp6_client_selfcheck():
    root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(["node", str(root / "tests/pulses_wp6_selfcheck.cjs")],
                          cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "assertions passed" in proc.stdout


MARKER_OP = "qubits.qA1.xy.operations.saturation"


def _marker_setup(client, names=("ON",), stale=False):
    from quam_state_manager.web import routes
    st = _store(client)
    st.get_value(MARKER_OP)["digital_marker"] = "ON"
    st.generated_config = {"digital_waveforms": {n: {"samples": [[1, 0]]} for n in names}}
    st.generated_config_meta = {"basis_hash": "stale" if stale else routes._config_state_hash(st)}
    return st


@pytest.mark.parametrize("mode", ["value", "literal", "pointer"])
def test_undefined_marker_is_refused_before_staging(client, mode):
    st = _marker_setup(client)
    st.get_value(MARKER_OP)["marker_choice"] = "OFF"
    from quam_state_manager.web import routes
    st.generated_config_meta["basis_hash"] = routes._config_state_hash(st)
    r = client.post("/pulse/edit", data={"path": MARKER_OP, "dot_path": MARKER_OP + ".digital_marker",
                                         "mode": mode, "value": "#./marker_choice" if mode == "pointer" else "OFF"})
    assert r.status_code == 400, r.data[:300]
    assert b"not defined by this chip" in r.data and b"ON" in r.data
    assert st.get_value(MARKER_OP + ".digital_marker") == "ON"
    assert not st.change_log


@pytest.mark.parametrize("value", ["CUSTOM", "null", "[[1, 0]]"])
def test_defined_marker_or_inline_samples_or_none_are_allowed(client, value):
    st = _marker_setup(client, ("ON", "CUSTOM"))
    if value.startswith("["):
        # v1.3.0: samples over text -> samples over null, matching the actual supported marker type.
        st.get_value(MARKER_OP)["digital_marker"] = None
        st.mutation_seq += 1
        from quam_state_manager.web import routes
        st.generated_config_meta["basis_hash"] = routes._config_state_hash(st)
    r = client.post("/pulse/edit", data={"path": MARKER_OP, "dot_path": MARKER_OP + ".digital_marker",
                                         "mode": "value", "value": value})
    assert r.status_code == 200, r.data[:300]
    assert st.change_log


@pytest.mark.parametrize("source", ["absent", "stale", "unknown-basis", "missing-map"])
def test_unknown_marker_set_is_disclosed_and_never_blocks(client, source):
    st = _marker_setup(client, stale=source == "stale")
    if source == "absent":
        st.generated_config = None
    elif source == "unknown-basis":
        st.generated_config_meta = {}
    elif source == "missing-map":
        st.generated_config = {"elements": {}}
    html = client.get("/pulse/detail", query_string={"path": MARKER_OP}).data
    assert b"Marker names cannot be determined" in html
    r = client.post("/pulse/edit", data={"path": MARKER_OP, "dot_path": MARKER_OP + ".digital_marker",
                                         "mode": "value", "value": "OFF"})
    assert r.status_code == 200, r.data[:300]
    assert st.get_value(MARKER_OP + ".digital_marker") == "OFF"
    assert b"Marker names cannot be determined" in r.data


def test_an_empty_defined_marker_set_refuses_names(client):
    _marker_setup(client, ())
    r = client.post("/pulse/edit", data={"path": MARKER_OP, "dot_path": MARKER_OP + ".digital_marker",
                                         "mode": "value", "value": "OFF"})
    assert r.status_code == 400 and b"(none)" in r.data


def test_redone_delete_returns_an_honest_inspector(client):
    r = client.post("/api/pulse/delete", data={"path": MARKER_OP, "confirm": "1"})
    assert r.status_code == 200, r.data[:300]
    assert client.post("/undo").status_code == 200
    assert client.get("/pulse/detail", query_string={"path": MARKER_OP}).status_code == 200
    assert client.post("/redo").status_code == 200
    r = client.get("/pulse/detail", query_string={"path": MARKER_OP})
    assert r.status_code == 404
    assert r.headers.get("X-Pulse-Missing") == "1"
    assert b"This pulse no longer exists" in r.data
    assert b"Deleted saturation" in r.data
    assert b"pulse-edit-form" not in r.data and b"pulse-detail-plot" not in r.data


def test_a_gate_delete_refusal_names_the_delete_and_why(lab):
    c, fake = lab
    st = _store(c)
    _by_name(st)
    r = c.post("/api/pulse/delete", data={"path": "qubits.q1.z.operations.czl_q1", "force": "1"})
    assert r.status_code == 400, r.data[:300]
    assert b"Your gate refused this delete" in r.data
    assert b"Operation" in r.data and b"not found" in r.data
    assert b"refused this value" not in r.data
    assert not st.change_log


def test_config_delete_refusal_names_the_delete():
    from quam_state_manager.web.routes import _lab_refusal_text
    text = _lab_refusal_text("your chip's generate_config() fails: missing operation", action="delete")
    assert "refused this delete" in text and "missing operation" in text


@pytest.mark.parametrize("undone", [False, True])
def test_same_pointer_relink_records_nothing_and_keeps_real_undo(lab, undone):
    c, fake = lab
    st = _store(c)
    op = "qubits.q1.z.operations.czl_q1"
    field = op + ".amplitude"
    pointer = st.get_value(field)
    target = G + ".flux_pulse_qubit.amplitude"
    assert c.post("/pulse/edit", data={"path": op, "dot_path": field,
                                       "mode": "value", "value": "0.2"}).status_code == 200
    if undone:
        assert c.post("/undo").status_code == 200
    before = json.dumps(st.merged, sort_keys=True)
    sequence, log_size = st.mutation_seq, len(st.change_log)
    calls = len(fake.gate_calls)
    r = c.post("/pulse/edit", data={"path": op, "dot_path": field,
                                    "mode": "pointer", "value": "  " + pointer + "  "})
    assert r.status_code == 200, r.data[:300]
    assert st.mutation_seq == sequence and len(st.change_log) == log_size
    assert json.dumps(st.merged, sort_keys=True) == before
    assert len(fake.gate_calls) == calls, "an unchanged link needs no gate check"
    assert "pulses-rows-changed" not in r.headers.get("HX-Trigger", "")
    assert c.post("/redo" if undone else "/undo").status_code == 200
    assert st.get_value(target) == (0.2 if undone else 0.15)


@pytest.mark.parametrize("kind", ["dict", "list"])
@pytest.mark.parametrize("door", ["/field/edit", "/pulse/edit"])
def test_container_refusal_keeps_the_reason_and_names_the_kind(client, kind, door):
    st = _store(client)
    st.merged["shared_container"] = {} if kind == "dict" else [1, 2]
    data = {"path": MARKER_OP, "dot_path": MARKER_OP + ".amplitude",
            "mode": "pointer", "value": "#/shared_container"}
    r = client.post(door, data=data)
    assert r.status_code == 400
    text = r.get_json()["error"] if r.is_json else r.get_data(as_text=True)
    assert "resolves to a container" in text and "not a value" in text
    assert "is a " + kind in text
    assert not st.change_log


@pytest.mark.parametrize("mode", ["pointer", "literal"])
def test_a_refused_explicit_link_action_does_not_offer_a_value_batch(lab, mode):
    c, fake = lab
    st = _store(c)
    st.merged["shared_flat_length"] = 40
    st.state["shared_flat_length"] = 40
    r = c.post("/pulse/edit", data={"path": G + ".flux_pulse_qubit", "dot_path": FL_C,
                                    "mode": mode, "value": "#/shared_flat_length" if mode == "pointer" else "40"})
    assert r.status_code == 400 and b"flat_length differ" in r.data
    assert r.headers.get("X-Lab-Refusal") is None
    assert _v(c, FL_C) == 32 and not st.change_log


@pytest.mark.parametrize("value", ["1", "false"])
@pytest.mark.parametrize("mode", ["value", "literal"])
def test_marker_validation_checks_the_name_that_would_be_written(client, value, mode):
    st = _marker_setup(client)
    r = client.post("/pulse/edit", data={"path": MARKER_OP, "dot_path": MARKER_OP + ".digital_marker",
                                         "mode": mode, "value": value})
    assert r.status_code == 400 and b"not defined by this chip" in r.data
    assert not st.change_log and st.get_value(MARKER_OP + ".digital_marker") == "ON"


def test_a_defined_numeric_marker_name_uses_the_existing_text_coercion(client):
    st = _marker_setup(client, ("ON", "1"))
    r = client.post("/pulse/edit", data={"path": MARKER_OP, "dot_path": MARKER_OP + ".digital_marker",
                                         "mode": "value", "value": "1"})
    assert r.status_code == 200, r.data[:300]
    assert st.get_value(MARKER_OP + ".digital_marker") == "1"
