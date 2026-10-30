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
    fields = {name: str(default) for name, _, default, _ in add_catalog.GATE_TYPES["cr_gate"]["fields"]}
    fields.update(gate_type="cr_gate", gate_name="gate_probe")
    fields.pop("qc_correction_phase")
    if value is not None:
        fields["qc_correction_phase"] = value
    response = client.post(f"/pair/{pair}/gate", data=fields)
    assert response.status_code == 400
    assert b"qc_correction_phase" in response.data
    assert "gate_probe" not in modifier.store.merged["qubit_pairs"][pair]["macros"]
