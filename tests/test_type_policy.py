"""Tests for the per-key type-policy layer (core/type_policy.py +
core/state_env_validate.py resolver/judge) and its modifier/route wiring.

Pins the unified contracts: ONE resolver (walk-down with actual-__class__
re-anchoring), ONE judge (codes → tier maps), layering
user-override → env → user → inference, pointer/null bypass everywhere,
no env-driven value rewriting (old-value numeric reconciliation only), the
EMPTY-POLICY GOLDEN (a store with a policy attached but no manifest and no
assignments behaves byte-identically to no policy at all), and the
type-assign route contracts (env-conflict 409, repair flow).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.core import state_env_validate as sev
from quam_state_manager.core import type_policy as tp
from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.modifier import Modifier


# ---------------------------------------------------------------------------
# hand-made manifest fixture (shape pinned against the probe contract)
# ---------------------------------------------------------------------------

def _ts(base, **kw):
    out = {"base": base, "optional": False, "item": None, "enum": None,
           "union": None, "class": None, "raw": base}
    out.update(kw)
    return out


def _field(ts, *, optional=None, has_default=True, default=None):
    return {"type": ts, "optional": ts.get("optional") if optional is None else optional,
            "has_default": has_default, "default": default, "default_repr": None,
            "default_is_reference": isinstance(default, str) and default.startswith("#"),
            "raw": ts.get("raw", "")}


MANIFEST = {
    "versions": {"quam": "0.6.0", "quam_builder": "0.4.0"},
    "classes": {
        "q.Root": {"importable": True, "canonical": "q.Root", "bases": [],
                   "is_dataclass": True, "error": None, "fields": {
            "qubits": _field(_ts("dict", item=_ts("component", **{"class": "q.Transmon"}))),
            "ports": _field(_ts("component", **{"class": "q.Ports"})),
            "custom": _field(_ts("component", **{"class": "otherlab_tools.Custom"}),
                             optional=True),
        }},
        "q.Transmon": {"importable": True, "canonical": "q.Transmon", "bases": [],
                       "is_dataclass": True, "error": None, "fields": {
            "f_01": _field(_ts("float", optional=True)),
            "length": _field(_ts("int")),
            "flux_point": _field(_ts("str", enum=["joint", "independent"])),
            "active": _field(_ts("bool")),
            "confusion_matrix": _field(_ts("list", optional=True,
                                           item=_ts("list", item=_ts("float")))),
            "xy": _field(_ts("component", **{"class": "q.Channel"})),
            "extras": _field(_ts("dict"), default={}),
        }},
        "q.Channel": {"importable": True, "canonical": "q.Channel", "bases": [],
                      "is_dataclass": True, "error": None, "fields": {
            "operations": _field(_ts("dict", item=_ts("component", **{"class": "q.Pulse"}))),
            "intermediate_frequency": _field(_ts("float", optional=True)),
        }},
        "q.Pulse": {"importable": True, "canonical": "q.Pulse", "bases": [],
                    "is_dataclass": True, "error": None, "fields": {
            "amplitude": _field(_ts("float"), has_default=False),
            "length": _field(_ts("int")),
        }},
        "q.Ports": {"importable": True, "canonical": "q.Ports", "bases": [],
                    "is_dataclass": True, "error": None, "fields": {
            "mw_outputs": _field(_ts("dict",
                item=_ts("dict", item=_ts("dict",
                    item=_ts("component", **{"class": "q.MWPort"}))))),
        }},
        "q.MWPort": {"importable": True, "canonical": "q.MWPort", "bases": [],
                     "is_dataclass": True, "error": None, "fields": {
            "band": _field(_ts("int")),
            "upconverters": _field(_ts("dict", optional=True,
                                       item=_ts("dict", item=_ts("float")))),
        }},
        "otherlab_tools.Custom": {"importable": False, "canonical": None, "bases": [],
                              "is_dataclass": False, "error": "ModuleNotFoundError",
                              "fields": None},
    },
    "pulse_roster": {},
}
MANIFEST["by_leaf"] = {}
MANIFEST["missing_classes"] = ["otherlab_tools.Custom"]


def _state():
    return {
        "__class__": "q.Root",
        "qubits": {"qA1": {
            "__class__": "q.Transmon",
            "f_01": 6.25e9, "length": 40, "flux_point": "joint", "active": True,
            "confusion_matrix": [[0.98, 0.02], [0.03, 0.97]],
            "xy": {"__class__": "q.Channel",
                   "intermediate_frequency": None,
                   "operations": {
                       "x180": {"__class__": "q.Pulse", "amplitude": 0.1, "length": 40},
                       "alias": "#./x180",
                   }},
            "extras": {"free_form": "anything"},
        }},
        "custom": {"__class__": "otherlab_tools.Custom", "weird": 1},
        "ports": {"__class__": "q.Ports",
                  "mw_outputs": {"con1": {"1": {"2": {
                      "__class__": "q.MWPort", "band": 2,
                      "upconverters": {"1": {"frequency": 8.6e9}}}}}}},
    }


# ---------------------------------------------------------------------------
# THE resolver
# ---------------------------------------------------------------------------

class TestResolver:
    def test_direct_field(self):
        ts = sev.expected_type_for("qubits.qA1.f_01", _state(), MANIFEST)
        assert ts and ts["base"] == "float"

    def test_reanchor_on_actual_class(self):
        ts = sev.expected_type_for("qubits.qA1.xy.operations.x180.amplitude",
                                   _state(), MANIFEST)
        assert ts and ts["base"] == "float"

    def test_int_string_dict_keys(self):
        ts = sev.expected_type_for("ports.mw_outputs.con1.1.2.band",
                                   _state(), MANIFEST)
        assert ts and ts["base"] == "int"

    def test_multi_duc_descent(self):
        ts = sev.expected_type_for("ports.mw_outputs.con1.1.2.upconverters.1.frequency",
                                   _state(), MANIFEST)
        assert ts and ts["base"] == "float"

    def test_matrix_element_typed(self):
        ts = sev.expected_type_for("qubits.qA1.confusion_matrix.0.1",
                                   _state(), MANIFEST)
        assert ts and ts["base"] == "float"

    def test_unknown_field_abstains(self):
        assert sev.expected_type_for("qubits.qA1.no_such", _state(), MANIFEST) is None

    def test_extras_children_abstain(self):
        assert sev.expected_type_for("qubits.qA1.extras.free_form",
                                     _state(), MANIFEST) is None

    def test_unimportable_class_abstains(self):
        assert sev.expected_type_for("custom.weird", _state(), MANIFEST) is None

    def test_wiring_paths_abstain(self):
        assert sev.expected_type_for("wiring.qubits.qA1.z", _state(), MANIFEST) is None

    def test_no_manifest_abstains(self):
        assert sev.expected_type_for("qubits.qA1.f_01", _state(), None) is None


# ---------------------------------------------------------------------------
# THE judge
# ---------------------------------------------------------------------------

class TestJudge:
    @pytest.mark.parametrize("value", ["#/a/b", "#./x", "#../y", None])
    def test_pointer_and_null_always_pass(self, value):
        assert sev.judge(value, _ts("int"))[0] is True

    def test_int_widening_and_integral_floats(self):
        assert sev.judge(5, _ts("float"))[0] is True          # int where float
        assert sev.judge(5.0, _ts("int"))[0] is True          # integral float where int
        ok, code, _ = sev.judge(5.5, _ts("int"))
        assert not ok and code == "non_integral_int"

    def test_bool_in_numeric(self):
        ok, code, _ = sev.judge(True, _ts("int"))
        assert not ok and code == "bool_in_numeric"

    def test_str_numeric_cross(self):
        assert sev.judge("abc", _ts("float"))[0] is False
        assert sev.judge(3, _ts("str"))[0] is False

    def test_non_finite(self):
        ok, code, _ = sev.judge(float("inf"), _ts("float"))
        assert not ok and code == "non_finite"

    def test_enum_is_warning_tier(self):
        ok, code, _ = sev.judge("weird", _ts("str", enum=["joint"]))
        assert not ok and code == "enum_miss"
        assert code not in sev.EDIT_BLOCKING          # v1: never blocks an edit

    def test_matrix_element_check(self):
        m = _ts("list", item=_ts("list", item=_ts("float")))
        assert sev.judge([[1.0, 2.0]], m)[0] is True
        ok, code, msg = sev.judge([[1.0, "x"]], m)
        assert not ok and code == "element_mismatch" and "[0]" in msg
        ok, code, _ = sev.judge([1.0, 2.0], m)
        assert not ok and code == "list_shape"

    def test_component_accepts_dict_rejects_scalar(self):
        c = _ts("component", **{"class": "q.Pulse"})
        assert sev.judge({"amplitude": 1}, c)[0] is True      # classless dict OK
        assert sev.judge(0.5, c)[0] is False

    def test_union_any_arm(self):
        u = _ts("union", union=[_ts("int"), _ts("str")])
        assert sev.judge(3, u)[0] is True
        assert sev.judge("x", u)[0] is True
        assert sev.judge([1], u)[0] is False


# ---------------------------------------------------------------------------
# layering + enforcement
# ---------------------------------------------------------------------------

class TestLayering:
    def _policy(self, assignments=None):
        return tp.TypePolicy(MANIFEST, assignments or {})

    def test_env_wins_over_plain_user(self):
        p = self._policy({"qubits.qA1.f_01": {"type": "str"}})
        exp = p.expected_for(_state(), "qubits.qA1.f_01")
        assert exp.source == "env" and exp.spec["base"] == "float"

    def test_override_env_wins(self):
        p = self._policy({"qubits.qA1.f_01": {"type": "str", "override_env": True}})
        exp = p.expected_for(_state(), "qubits.qA1.f_01")
        assert exp.source == "user" and exp.spec["base"] == "str"

    def test_user_fills_env_gap(self):
        p = self._policy({"qubits.qA1.extras.free_form": {"type": "number"}})
        exp = p.expected_for(_state(), "qubits.qA1.extras.free_form")
        assert exp.source == "user" and exp.spec["base"] == "float"

    def test_inference_is_display_only(self):
        p = self._policy()
        exp = p.expected_for(_state(), "qubits.qA1.extras.free_form", "hello")
        assert exp is not None and exp.source == "inferred"
        assert exp.enforced is False

    def test_fully_unknown(self):
        p = self._policy()
        assert p.expected_for(_state(), "qubits.qA1.extras.free_form",
                              None, infer=False) is None


class TestCheck:
    def test_raises_with_provenance(self):
        p = tp.TypePolicy(MANIFEST, {})
        exp = p.expected_for(_state(), "qubits.qA1.length")
        with pytest.raises(tp.TypeMismatchError) as ei:
            p.check(exp, 40.5, path="qubits.qA1.length", old_value=40)
        msg = str(ei.value)
        assert "expected int" in msg and "Transmon.length" in msg
        j = ei.value.as_json()
        assert j["error_kind"] == "type_mismatch" and j["expected"]["source"] == "env"

    def test_numeric_reconciliation_only(self):
        p = tp.TypePolicy(MANIFEST, {})
        exp = p.expected_for(_state(), "qubits.qA1.f_01")
        # old float + int new → float (today's semantics, idempotent)
        assert p.check(exp, 6, path="p", old_value=6.25e9) == 6.0
        # old int + integral float → int
        exp_i = p.expected_for(_state(), "qubits.qA1.length")
        assert p.check(exp_i, 40.0, path="p", old_value=40) == 40
        # old None → verbatim
        assert p.check(exp, 7, path="p", old_value=None) == 7


# ---------------------------------------------------------------------------
# modifier integration
# ---------------------------------------------------------------------------

def _store_with_policy(assignments=None, manifest=MANIFEST):
    store = QuamStore.from_dicts(_state(), {"wiring": {}})
    store.type_policy = tp.TypePolicy(manifest, assignments or {})
    return store


class TestModifierGate:
    def test_blocking_write_leaves_no_trace(self):
        store = _store_with_policy()
        mod = Modifier(store)
        seq = store.mutation_seq
        with pytest.raises(TypeError):          # TypeMismatchError ⊂ TypeError
            mod.set_value("qubits.qA1.length", 40.5)
        assert store.merged["qubits"]["qA1"]["length"] == 40
        assert store.mutation_seq == seq and not store.change_log

    def test_pointer_and_null_bypass(self):
        store = _store_with_policy()
        mod = Modifier(store)
        mod.set_value("qubits.qA1.length", "#/qubits/qA1/f_01")
        mod.set_value("qubits.qA1.f_01", None)
        assert store.merged["qubits"]["qA1"]["f_01"] is None

    def test_coerce_false_still_enforced(self):
        store = _store_with_policy()
        mod = Modifier(store)
        with pytest.raises(TypeError):
            mod.set_value("qubits.qA1.length", "not-a-number", coerce=False)

    def test_enforce_false_verbatim(self):
        store = _store_with_policy()
        mod = Modifier(store)
        mod.set_value("qubits.qA1.length", "drifted", coerce=False, enforce=False)
        assert store.merged["qubits"]["qA1"]["length"] == "drifted"

    def test_repair_str_to_int_via_user_assignment(self):
        # the previously-impossible repair: a str-typed numeral fixed through
        # the generic path once the user assigns the type
        state = _state()
        state["qubits"]["qA1"]["extras"]["stored_as_str"] = "40"
        store = QuamStore.from_dicts(state, {"wiring": {}})
        store.type_policy = tp.TypePolicy(
            MANIFEST, {"qubits.qA1.extras.stored_as_str": {"type": "int"}})
        mod = Modifier(store)
        mod.set_value("qubits.qA1.extras.stored_as_str", 40)
        assert store.merged["qubits"]["qA1"]["extras"]["stored_as_str"] == 40

    def test_create_subtree_checked(self):
        store = _store_with_policy()
        mod = Modifier(store)
        with pytest.raises(TypeError):
            mod.create_subtree("qubits.qA1.xy.operations.bad", {
                "__class__": "q.Pulse", "amplitude": "oops", "length": 40})
        mod.create_subtree("qubits.qA1.xy.operations.good", {
            "__class__": "q.Pulse", "amplitude": 0.2, "length": 40})
        assert "good" in store.merged["qubits"]["qA1"]["xy"]["operations"]

    def test_batch_rollback_on_type_error(self):
        store = _store_with_policy()
        mod = Modifier(store)
        with pytest.raises(TypeError):
            mod.batch_set({"qubits.qA1.f_01": 6.3e9,
                           "qubits.qA1.length": 40.5})
        assert store.merged["qubits"]["qA1"]["f_01"] == 6.25e9   # rolled back

    def test_empty_policy_golden(self):
        """Policy attached, NO manifest + NO assignments ⇒ byte-identical
        legacy coercion (the zero-behavior-change pin)."""
        store = _store_with_policy(manifest=None)
        mod = Modifier(store)
        # int field keeps today's non-truncation float drift
        mod.set_value("qubits.qA1.length", 40.5)
        assert store.merged["qubits"]["qA1"]["length"] == 40.5
        # str coercion still swallows numbers
        mod.set_value("qubits.qA1.flux_point", 3)
        assert store.merged["qubits"]["qA1"]["flux_point"] == "3"
        # bool whitelist still errors
        with pytest.raises(TypeError):
            mod.set_value("qubits.qA1.active", "flase")


# ---------------------------------------------------------------------------
# parse_with_expected + grammar
# ---------------------------------------------------------------------------

class TestParse:
    def _exp(self, expr):
        return tp.Expected(spec=tp.parse_type(expr), source="user")

    def test_no_expectation_is_parse_value(self):
        assert tp.parse_with_expected("6.25e9", None) == 6.25e9
        assert tp.parse_with_expected('"02"', None) == "02"

    def test_str_verbatim(self):
        assert tp.parse_with_expected("02", self._exp("str")) == "02"
        assert tp.parse_with_expected('"null"', self._exp("str")) == "null"

    def test_null_tokens_always_none(self):
        assert tp.parse_with_expected("null", self._exp("str")) is None
        assert tp.parse_with_expected("None", self._exp("int")) is None

    def test_pointer_always_wins(self):
        assert tp.parse_with_expected("#/a/b", self._exp("int")) == "#/a/b"

    def test_number_and_int(self):
        assert tp.parse_with_expected("1,000", self._exp("int")) == 1000
        assert tp.parse_with_expected("2.5", self._exp("number")) == 2.5
        with pytest.raises(ValueError):
            tp.parse_with_expected("abc", self._exp("number"))
        with pytest.raises(ValueError):
            tp.parse_with_expected("1e999", self._exp("number"))

    def test_bool_whitelist(self):
        assert tp.parse_with_expected("on", self._exp("bool")) is True
        with pytest.raises(ValueError):
            tp.parse_with_expected("flase", self._exp("bool"))

    def test_containers_require_json(self):
        assert tp.parse_with_expected("[[1,2],[3,4]]", self._exp("matrix<number>")) \
            == [[1, 2], [3, 4]]
        with pytest.raises(ValueError):
            tp.parse_with_expected("1,2,3", self._exp("list"))

    def test_grammar_round_trip(self):
        for expr in ("int", "number", "str", "bool", "dict", "list",
                     "list<int>", "matrix", "matrix<number>"):
            ts = tp.parse_type(expr)
            assert isinstance(ts, dict) and ts["base"]
        with pytest.raises(ValueError):
            tp.parse_type("complex128")


# ---------------------------------------------------------------------------
# sidecar
# ---------------------------------------------------------------------------

class TestSidecar:
    def test_save_load_delete_roundtrip(self, tmp_path):
        live = tmp_path / "chip"
        live.mkdir()
        (live / "state.json").write_text('{"qubits": {}}', encoding="utf-8")
        rec = tp.save_assignment(tmp_path, live, "a.b", {"type": "number"})
        assert rec["type"] == "number"
        pol = tp.load_policy(tmp_path, live, None)
        assert "a.b" in pol.assignments
        assert tp.delete_assignment(tmp_path, live, "a.b") is True
        assert tp.delete_assignment(tmp_path, live, "a.b") is False
        assert tp.load_policy(tmp_path, live, None).assignments == {}

    def test_bad_expr_raises(self, tmp_path):
        live = tmp_path / "chip"
        live.mkdir()
        with pytest.raises(ValueError):
            tp.save_assignment(tmp_path, live, "a.b", {"type": "nope"})

    def test_corrupt_sidecar_never_crashes(self, tmp_path):
        live = tmp_path / "chip"
        live.mkdir()
        p = tp.assignments_path(tmp_path, live)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("NOT JSON", encoding="utf-8")
        assert tp.load_policy(tmp_path, live, None).assignments == {}


# ---------------------------------------------------------------------------
# route level
# ---------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path):
    from quam_state_manager.web.app import create_app
    (tmp_path / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps({"wiring": {}}), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    c = app.test_client()
    c.post("/load", data={"folder": str(tmp_path)})
    # inject the manifest directly (no env in tests): rebuild the policy with it
    with app.app_context():
        ctx = app.config["contexts"][app.config["active_context"]]
        ctx["store"].type_policy = tp.TypePolicy(MANIFEST, {})
    return c


class TestRoutes:
    def test_field_edit_blocks_with_error_contract(self, client):
        r = client.post("/field/edit", data={
            "dot_path": "qubits.qA1.length", "value": "40.5"})
        assert r.status_code == 400
        j = r.get_json()
        assert j["error_kind"] == "type_mismatch"
        assert j["expected"]["type"] == "int" and j["expected"]["source"] == "env"
        assert "Transmon.length" in j["error"]

    def test_field_edit_str_expected_keeps_leading_zero(self, client):
        r = client.post("/field/edit", data={
            "dot_path": "qubits.qA1.flux_point", "value": "joint"})
        assert r.status_code == 200

    def test_peek_expected_block(self, client):
        j = client.get("/field/peek?dot_path=qubits.qA1.f_01"
                       "&dot_path=qubits.qA1.extras.free_form").get_json()
        exp = j["expected"]
        assert exp["qubits.qA1.f_01"]["type"] == "number"
        assert exp["qubits.qA1.f_01"]["source"] == "env"
        assert exp["qubits.qA1.extras.free_form"]["source"] == "inferred"

    def test_type_assign_env_conflict_409_then_override(self, client):
        r = client.post("/field/type-assign", data={
            "dot_path": "qubits.qA1.f_01", "type": "str"})
        assert r.status_code == 409
        assert r.get_json()["error_kind"] == "env_conflict"
        r2 = client.post("/field/type-assign", data={
            "dot_path": "qubits.qA1.f_01", "type": "str", "override_env": "1"})
        assert r2.status_code == 200
        assert r2.get_json()["expected"]["source"] == "user"

    def test_type_assign_free_key_and_current_violation_warning(self, client):
        r = client.post("/field/type-assign", data={
            "dot_path": "qubits.qA1.extras.free_form", "type": "number"})
        j = r.get_json()
        assert r.status_code == 200 and j["ok"] is True
        assert j["warning"] and "CURRENT value" in j["warning"]

    def test_type_unassign_restores_env(self, client):
        client.post("/field/type-assign", data={
            "dot_path": "qubits.qA1.f_01", "type": "str", "override_env": "1"})
        r = client.post("/field/type-unassign", data={"dot_path": "qubits.qA1.f_01"})
        j = r.get_json()
        assert j["removed"] is True and j["expected"]["source"] == "env"

    def test_edit_batch_per_row_type_error(self, client):
        j = client.post("/field/edit-batch", json={"updates": [
            {"dot_path": "qubits.qA1.length", "value": 40.5}]}).get_json()
        assert j["ok"] is False
        row = j["results"][0]
        assert row["error_kind"] == "type_mismatch" and row["applied"] is False
