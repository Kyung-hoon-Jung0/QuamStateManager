"""A lab's own TWPA component is a lab extension SM must handle as it is.

One lab drives its TWPA from an external pump source and a DC flux line, through a
class of its own (`<lab package>.twpa_ext.ExternalTwpa`) held at a ROOT field the
lab's own root class declares -- not under the stock `twpas` dict, and sharing no
field with the stock TWPA. Release review v1.6.0 measured three surfaces for it:

- load + display: PASS (pinned below at route level);
- re-generate when the build env CANNOT import the class: the object is dropped
  and the drop is reported, never reshaped into a stock TWPA (pinned below);
- re-generate when the build env CAN import the class: the object USED to be
  dropped, because the merge's value gate admitted only classes the rebuild itself
  serialised (regen_merge._gate_graft, the docs/136 value gate) and a rebuild never
  writes a lab TWPA. Fixed in the same review (regen_merge._lab_object_admissible):
  an object of a class the env imports is kept when its parent is the LAB's own
  class declaring that field -- here the lab root the spec carried -- and its
  subtree is gated by the class's own env fields. A stock parent of the same name
  is still not enough (another stack generation may type the field differently).
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
# the build env's answer (every SOURCE class it imports, as the probe reports it)
# when it imports the lab classes / when it cannot import the TWPA class
STOCK_ROOT_BASE = "quam_builder.architecture.superconducting.qpu.flux_tunable_quam.FluxTunableQuam"
ENV_IMPORTS = {LAB_ROOT: {"bases": [STOCK_ROOT_BASE], "fields": ROOT_FIELDS},
               LAB_TWPA: {"bases": ["quam.core.quam_classes.QuamComponent"], "fields": TWPA_FIELDS}}
ENV_FIELDS_IMPORTS = {LAB_ROOT: ROOT_FIELDS, LAB_TWPA: TWPA_FIELDS}
ENV_CANNOT = {LAB_ROOT: {"bases": [STOCK_ROOT_BASE], "fields": ROOT_FIELDS}}
ENV_FIELDS_CANNOT = {LAB_ROOT: ROOT_FIELDS, LAB_TWPA: None}


@pytest.mark.parametrize("slot", ["absent", "null"])
def test_a_lab_twpa_the_env_imports_survives_a_regenerate(slot):
    """Was the open P1 of the v1.6.0 review (a strict xfail); fixed by
    regen_merge._lab_object_admissible, which this pin now guards."""
    r = merge_states(_old(), _new(slot), class_schemas=SCHEMAS, keep_classes=ENV_IMPORTS,
                     env_fields=ENV_FIELDS_IMPORTS)
    assert r.merged.get("twpa_ext") == _twpa()
    assert "twpa_ext" not in r.stats.schema_dropped
    assert all(p != "twpa_ext" for p, _ in r.stats.rebuild_removed)
    assert ("twpa_ext", LAB_TWPA) in r.stats.lab_objects_kept   # said, never silent


def test_a_lab_twpa_under_a_stock_root_is_still_refused():
    """The widening needs the LAB's own class as the parent: under a stock root of
    the same name on both sides the object is dropped as before, because another
    stack generation may declare that field with another type (docs/136)."""
    old, new = _old(), _new("absent")
    old["__class__"] = new["__class__"] = STOCK_ROOT_BASE
    r = merge_states(old, new, class_schemas={STOCK_ROOT_BASE: ROOT_FIELDS},
                     keep_classes={STOCK_ROOT_BASE: {"bases": [], "fields": ROOT_FIELDS},
                                   LAB_TWPA: ENV_IMPORTS[LAB_TWPA]},
                     env_fields={STOCK_ROOT_BASE: ROOT_FIELDS, LAB_TWPA: TWPA_FIELDS})
    assert r.merged.get("twpa_ext") is None
    assert "twpa_ext" in r.stats.schema_dropped
    assert r.stats.lab_objects_kept == []


@pytest.mark.parametrize("slot", ["absent", "null"])
def test_a_lab_twpa_the_env_cannot_import_is_dropped_and_said_never_reshaped(slot):
    r = merge_states(_old(), _new(slot), class_schemas=SCHEMAS, keep_classes=ENV_CANNOT,
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
