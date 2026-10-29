"""A lab's own TWPA component is a lab extension SM must handle as it is.

One lab drives its TWPA from an external pump source and a DC flux line, through a
class of its own (`<lab package>.twpa_ext.ExternalTwpa`) held at a ROOT field the
lab's own root class declares -- not under the stock `twpas` dict, and sharing no
field with the stock TWPA. Release review v1.6.0 measured three surfaces for it:

- load + display: PASS (pinned below at route level);
- re-generate when the build env CANNOT import the class: the object is dropped
  and the drop is reported, never reshaped into a stock TWPA (pinned below);
- re-generate when the build env CAN import the class: the object is still
  dropped, because the merge's value gate admits only classes the rebuild itself
  serialised (regen_merge._gate_graft, the docs/136 value gate) and a rebuild
  never writes a lab TWPA. That is the OPEN P1 of the review, recorded as a strict
  xfail so the day the gate learns to keep it the pin turns red and is promoted.
  No env on the machine this was reviewed on imports the class, so the fix could
  not be proven live and was not shipped unproven (a wrong widening of that gate
  poisons Quam.load()).
"""
import json

import pytest

from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.regen_merge import merge_states
from tests.test_v151_instance_record import _WIRING, _app

LAB_ROOT = "quam_config.my_quam.Quam"
LAB_TWPA = "quam_config.twpa_ext.ExternalTwpa"
TWPA_FIELDS = ["enabled", "auto_apply", "flux_v", "pump_frequency_hz", "pump_power_dbm",
               "flux_ch_hi", "flux_ch_lo", "note"]
ROOT_FIELDS = ["qubits", "qubit_pairs", "twpas", "active_qubit_names", "active_twpa_names", "twpa_ext"]


def _twpa():
    return {"__class__": LAB_TWPA, "enabled": True, "auto_apply": False, "flux_v": 0.12,
            "pump_frequency_hz": 7.9e9, "pump_power_dbm": -3.0, "flux_ch_hi": 3, "flux_ch_lo": 4,
            "note": "external pump"}


def _old():
    return {"__class__": LAB_ROOT, "qubits": {"q1": {"id": "q1", "T1": 1e-5}}, "qubit_pairs": {},
            "active_qubit_names": ["q1"], "active_twpa_names": [], "twpas": {}, "twpa_ext": _twpa()}


def _new(slot):
    """The rebuild: the lab root (its class carried by the spec), no lab TWPA written."""
    s = {"__class__": LAB_ROOT, "qubits": {"q1": {"id": "q1", "T1": None}}, "qubit_pairs": {},
         "active_qubit_names": ["q1"], "active_twpa_names": [], "twpas": {}}
    if slot == "null":
        s["twpa_ext"] = None
    return s


# what the rebuild serialised: the root class only -- it writes no lab TWPA
SCHEMAS = {LAB_ROOT: ROOT_FIELDS}
# the build env's answer when it imports the lab class / when it does not
ENV_IMPORTS = {LAB_TWPA: {"bases": ["quam.core.quam_classes.QuamComponent"], "fields": TWPA_FIELDS}}
ENV_FIELDS_IMPORTS = {LAB_ROOT: ROOT_FIELDS, LAB_TWPA: TWPA_FIELDS}
ENV_FIELDS_CANNOT = {LAB_ROOT: ROOT_FIELDS, LAB_TWPA: None}


@pytest.mark.parametrize("slot", ["absent", "null"])
@pytest.mark.xfail(strict=True, reason="open P1 (v1.6.0 release review): the value gate admits only "
                   "classes the rebuild serialised, so a lab component at a root field is dropped even "
                   "when the build env imports its class")
def test_a_lab_twpa_the_env_imports_survives_a_regenerate(slot):
    r = merge_states(_old(), _new(slot), class_schemas=SCHEMAS, keep_classes=ENV_IMPORTS,
                     env_fields=ENV_FIELDS_IMPORTS)
    assert r.merged.get("twpa_ext") == _twpa()
    assert "twpa_ext" not in r.stats.schema_dropped
    assert all(p != "twpa_ext" for p, _ in r.stats.rebuild_removed)


@pytest.mark.parametrize("slot", ["absent", "null"])
def test_a_lab_twpa_the_env_cannot_import_is_dropped_and_said_never_reshaped(slot):
    r = merge_states(_old(), _new(slot), class_schemas=SCHEMAS, keep_classes={},
                     env_fields=ENV_FIELDS_CANNOT)
    assert r.merged.get("twpa_ext") is None
    named = set(r.stats.schema_dropped) | {p for p, _ in r.stats.rebuild_removed}
    assert "twpa_ext" in named, (r.stats.schema_dropped, r.stats.rebuild_removed)
    # the lab component is never rewritten into the stock shape
    assert r.merged.get("twpas") == {}
    assert LAB_TWPA not in json.dumps(r.merged.get("twpas"))


def test_a_lab_twpa_chip_loads_and_every_surface_renders(tmp_path):
    chip = tmp_path / "chip"
    chip.mkdir()
    (chip / "state.json").write_text(json.dumps(_old()), encoding="utf-8")
    (chip / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")
    store = QuamStore(chip)
    assert store.merged["twpa_ext"]["__class__"] == LAB_TWPA
    app, c = _app(tmp_path / "inst")
    assert c.post("/load", data={"folder": str(chip)}).status_code in (200, 302)
    for url in ("/", "/explorer", "/qubits", "/bulk", "/topology", "/instrument",
                "/diagnostics", "/pulses", "/regenerate"):
        r = c.get(url)
        assert r.status_code == 200, (url, r.status_code)
    # the page walk never touched the lab component on disk
    after = json.loads((chip / "state.json").read_text(encoding="utf-8"))
    assert after["twpa_ext"] == _twpa()
