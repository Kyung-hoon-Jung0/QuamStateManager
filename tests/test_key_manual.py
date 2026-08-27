"""core.key_manual (2026-08-27): the state.json key manual assembled from the
env schema manifest (per-field docstrings) and the curated QM-docs entries —
labelled by source, never blended; an undescribed key says so."""
from __future__ import annotations

import json

import pytest

from quam_state_manager.core import key_manual, key_manual_docs
from quam_state_manager.web.app import create_app

FL = "quam_builder.architecture.superconducting.components.flux_line.FluxLine"
PORT = "quam.components.ports.analog_outputs.MWFEMAnalogOutputPort"


def _state():
    return {
        "qubits": {
            "qA1": {"__class__": "lab.Transmon", "T1": 1e-5,
                    "z": {"__class__": FL, "joint_offset": 0.1, "lab_extra": 7}},
            "qA2": {"__class__": "lab.Transmon", "z": {"__class__": FL, "joint_offset": 0.2,
                                                        "independent_offset": 0.0}},
        },
        "ports": {"analog_outputs": {"con1": {"1": {"1": {
            "__class__": PORT, "band": 2, "full_scale_power_dbm": 10}}}}},
    }


def _manifest():
    return {"classes": {
        FL: {"importable": True, "canonical": FL, "doc": "QUAM component for a flux line.",
             "fields": {
                 "joint_offset": {"type": {"base": "float"}, "raw": "float", "has_default": True,
                                  "default": 0.0, "doc": "the flux bias at the joint point in V."},
                 "independent_offset": {"type": {"base": "float"}, "raw": "float", "has_default": True,
                                        "default": 0.0, "doc": "the flux bias when not interacting."},
                 "output_mode": {"type": {"base": "str", "enum": ["direct", "amplified"]}, "raw": "str",
                                 "has_default": True, "default": "direct"},
                 "opx_output": {"type": {"base": "component"}, "raw": "LFFEMAnalogOutputPort",
                                "has_default": False, "default": None},
             }},
        PORT: {"importable": True, "canonical": PORT, "doc": None,
               "fields": {
                   "band": {"type": {"base": "int"}, "raw": "int", "has_default": False, "default": None},
                   "full_scale_power_dbm": {"type": {"base": "float"}, "raw": "float", "has_default": True, "default": -11},
                   "delay": {"type": {"base": "int"}, "raw": "int", "has_default": True, "default": 0},
               }},
    }}


class TestEntries:
    def test_class_fields_carry_their_own_words_and_where_they_are_used(self):
        d = key_manual.manual_entries(_state(), {}, _manifest())
        by = {e["id"]: e for e in d["entries"]}
        jo = by["FluxLine.joint_offset"]
        assert jo["doc"] == "the flux bias at the joint point in V." and jo["source"] == "class docstring"
        assert jo["present_in"] == 2 and jo["examples"] == ["qubits.qA1.z.joint_offset", "qubits.qA2.z.joint_offset"]
        assert by["FluxLine.independent_offset"]["present_in"] == 1
        assert by["FluxLine.opx_output"]["required"] is True and by["FluxLine.opx_output"]["present_in"] == 0

    def test_literal_choices_and_an_undescribed_field_stay_honest(self):
        d = key_manual.manual_entries(_state(), {}, _manifest())
        by = {e["id"]: e for e in d["entries"]}
        om = by["FluxLine.output_mode"]
        assert om["choices"] == ["direct", "amplified"]
        assert om["doc"] is None
        # FluxLine is not a port class -> the QM-docs output_mode entry (an LF-FEM
        # PORT key) must not be borrowed for it
        assert om["docs"] is None and om["source"] is None

    def test_port_fields_get_the_qm_docs_entry_with_its_page(self):
        d = key_manual.manual_entries(_state(), {}, _manifest())
        by = {e["id"]: e for e in d["entries"]}
        band = by["MWFEMAnalogOutputPort.band"]
        assert band["doc"] is None and band["source"] == "QM docs"
        assert band["docs"]["docs"].endswith("opx1000_fems.md#bands")
        assert [a["value"] for a in band["docs"]["allowed"]] == [1, 2, 3]
        fsp = by["MWFEMAnalogOutputPort.full_scale_power_dbm"]
        assert fsp["docs"]["default"] == -11 and "dBm" in fsp["docs"]["unit"]
        # `delay` has TWO docs entries (analog port vs digital output); the port
        # class picks the analog one
        assert "analog output" in by["MWFEMAnalogOutputPort.delay"]["docs"]["summary"]

    def test_docs_only_keys_are_findable_without_an_env(self):
        d = key_manual.manual_entries(_state(), {}, None)
        assert d["env"] is False and "environment" in d["note"]
        keys = {e["key"] for e in d["entries"]}
        assert {"band", "lo_mode", "sampling_rate", "sticky"} <= keys
        lo = next(e for e in d["entries"] if e["key"] == "lo_mode")
        assert lo["source"] == "QM docs" and lo["cls_path"] is None
        assert [a["value"] for a in lo["docs"]["allowed"]] == ["auto", "always_on"]

    def test_classes_summary_marks_unknown_classes(self):
        d = key_manual.manual_entries(_state(), {}, _manifest())
        rows = {r["cls"]: r for r in d["classes"]}
        assert rows["FluxLine"]["known"] and rows["FluxLine"]["count"] == 2
        assert rows["Transmon"]["known"] is False and rows["Transmon"]["count"] == 2


class TestNodeKeys:
    def test_a_node_lists_set_and_unset_keys(self):
        d = key_manual.node_keys(_state(), {}, _manifest(), "qubits.qA1.z")
        assert d["ok"] and d["cls"] == "FluxLine" and d["known"]
        f = {x["key"]: x for x in d["fields"]}
        assert f["joint_offset"]["present"] and not f["independent_offset"]["present"]
        assert set(d["unset"]) == {"independent_offset", "output_mode", "opx_output"}
        assert f["lab_extra"]["undeclared"] and f["lab_extra"]["present"], "a lab's extra key is shown, marked undeclared"

    def test_a_leaf_focuses_its_parent_view(self):
        d = key_manual.node_keys(_state(), {}, _manifest(), "qubits.qA1.z.joint_offset")
        assert d["owner"] == "qubits.qA1.z" and d["focus"] == "joint_offset"
        assert next(x for x in d["fields"] if x["focus"])["key"] == "joint_offset"

    def test_unknown_class_and_missing_path_say_so(self):
        d = key_manual.node_keys(_state(), {}, _manifest(), "qubits.qA1")
        assert d["ok"] and d["known"] is False and "environment" in d["note"]
        assert key_manual.node_keys(_state(), {}, _manifest(), "qubits.nope.z")["ok"] is False


class TestTypeLabel:
    def test_reprs_are_cleaned_but_written_annotations_kept(self):
        tl = key_manual._type_label
        assert tl({"raw": "<class 'float'>"}) == "float"
        assert tl({"raw": "typing.Optional[typing.Literal['auto', 'always_on']]"}) == "Optional[Literal['auto', 'always_on']]"
        assert tl({"raw": "Dict[str, quam.components.pulses.Pulse]"}) == "Dict[str, Pulse]"
        assert tl({"raw": "Optional[float]"}) == "Optional[float]"
        assert tl({"raw": None, "type": {"base": "int", "optional": True}}) == "Optional[int]"


class TestDocsMap:
    def test_every_entry_names_a_real_docs_file_and_a_key(self):
        from pathlib import Path
        root = Path(key_manual_docs.DOCS_ROOT)
        for e in key_manual_docs.DOC_ENTRIES:
            assert e["key"] and e["applies"] and e["summary"] and e["docs"]
            f = root / e["docs"].split("#", 1)[0]
            if root.exists():
                assert f.exists(), f"docs page missing for {e['key']}: {f}"

    def test_scoping_by_class(self):
        assert [e["docs"] for e in key_manual_docs.entries_for("DigitalOutputChannel", "delay")][0].endswith("#elements")
        assert len(key_manual_docs.entries_for(None, "delay")) == 2


class TestRoutes:
    @pytest.fixture
    def client(self, tmp_path):
        (tmp_path / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
        (tmp_path / "wiring.json").write_text("{}", encoding="utf-8")
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        c = app.test_client()
        assert c.post("/load", data={"folder": str(tmp_path)}).status_code in (200, 302)
        return c

    def test_manual_without_a_chip_and_with_one(self, tmp_path):
        app = create_app(testing=True, instance_path=str(tmp_path / "_i2"))
        d = app.test_client().get("/api/manual").get_json()
        assert d["ok"] and d["chip"] is None and any(e["key"] == "band" for e in d["entries"])

    def test_node_route(self, client):
        d = client.get("/api/manual/node?path=qubits.qA1.z").get_json()
        assert d["ok"] and d["cls"] == "FluxLine"
        assert client.get("/api/manual/node").get_json()["ok"] is False

    def test_a_selected_env_reaches_the_route(self, client, monkeypatch):
        """The route must actually consult the env schema manifest (a missing
        module import here once made every chip look env-less)."""
        from quam_state_manager.core import config_generator, state_env_schema
        app = client.application
        config_generator.set_selected_env(app.instance_path, "C:/fake/python.exe")
        calls = {}

        def fake_manifest(store, python_path, inst, *, cached_only=False, force=False):
            calls["python"] = python_path
            calls["cached_only"] = cached_only
            return _manifest()
        monkeypatch.setattr(state_env_schema, "manifest_for_store", fake_manifest)
        d = client.get("/api/manual").get_json()
        assert calls == {"python": "C:/fake/python.exe", "cached_only": True}, "request path: cached, never spawns"
        assert d["env"] is True and d["note"] is None
        by = {e["id"]: e for e in d["entries"]}
        assert by["FluxLine.joint_offset"]["doc"].startswith("the flux bias")
        n = client.get("/api/manual/node?path=qubits.qA1.z").get_json()
        assert n["known"] is True and "independent_offset" in n["unset"]
