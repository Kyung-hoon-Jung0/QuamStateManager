"""Pins for the final Pulses browser findings in v1.3.0."""
import json
import subprocess
from pathlib import Path

import pytest

from tests.test_lab_doors import lab, _store, _v, G, FL_C, FL_T
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
