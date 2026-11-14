"""Path C refuses bad or missing fields without writing defaults."""
import pytest
from tests.test_add_paths_golden import _shapes, _client
from quam_state_manager.core import add_catalog
from quam_state_manager.web import routes


@pytest.mark.parametrize("value", [None, "", "bad", "nan", "inf", "1e999"])
def test_missing_or_invalid_gate_field_is_named_and_writes_nothing(tmp_path, monkeypatch, value):
    state, wiring, pair = _shapes()["cr_zz"]
    client = _client(tmp_path, "field", state, wiring)
    writes = []
    original = routes._modifier
    modifier = None
    def get_modifier():
        nonlocal modifier
        modifier = original()
        return modifier
    monkeypatch.setattr(routes, "_modifier", get_modifier)
    fields = {name: str(default) for name, _, default, _ in add_catalog.GATE_TYPES["stark_cz"]["fields"]}
    fields.update(gate_type="stark_cz", gate_name="gate_probe")
    fields.pop("qc_correction_phase")
    if value is not None:
        fields["qc_correction_phase"] = value
    response = client.post(f"/pair/{pair}/gate", data=fields)
    assert response.status_code == 400
    assert b"qc_correction_phase" in response.data
    assert "gate_probe" not in modifier.store.merged["qubit_pairs"][pair]["macros"]



def _spy_modifier(monkeypatch):
    seen = {}
    original = routes._modifier
    def get_modifier():
        seen["m"] = original()
        return seen["m"]
    monkeypatch.setattr(routes, "_modifier", get_modifier)
    return seen


def test_the_pair_add_gate_form_creates_a_stark_cz_macro(tmp_path, monkeypatch):
    """The pair page's Add gate stays: its form renders and a Stark-induced CZ gate is written."""
    state, wiring, pair = _shapes()["cr_zz"]
    client = _client(tmp_path, "stark", state, wiring)
    seen = _spy_modifier(monkeypatch)
    form = client.get(f"/pair/{pair}/gate/new")
    assert form.status_code == 200
    assert b'value="stark_cz"' in form.data
    fields = {name: str(default) for name, _, default, _ in add_catalog.GATE_TYPES["stark_cz"]["fields"]}
    fields.update(gate_type="stark_cz", gate_name="g_stark_probe")
    response = client.post(f"/pair/{pair}/gate", data=fields)
    assert response.status_code == 200
    assert "g_stark_probe" in seen["m"].store.merged["qubit_pairs"][pair]["macros"]


def test_the_form_offers_no_cr_gate_and_the_post_refuses_it(tmp_path, monkeypatch):
    """A CR pair would qualify for cr_gate; the form does not offer it and the POST writes nothing."""
    import json
    state, wiring, pair = _shapes()["cr_zz"]
    client = _client(tmp_path, "nocr", state, wiring)
    form = client.get(f"/pair/{pair}/gate/new").data
    assert b'value="cr_gate"' not in form
    seen = _spy_modifier(monkeypatch)
    client.post(f"/pair/{pair}/gate", data={"gate_type": "nope", "gate_name": "x"})
    before = json.dumps(seen["m"].store.merged, sort_keys=True)
    fields = {name: str(default) for name, _, default, _ in add_catalog.GATE_TYPES["cr_gate"]["fields"]}
    fields.update(gate_type="cr_gate", gate_name="g_cr_probe")
    response = client.post(f"/pair/{pair}/gate", data=fields)
    assert response.status_code == 409
    assert b"CR gates are not created from this form." in response.data
    assert json.dumps(seen["m"].store.merged, sort_keys=True) == before
