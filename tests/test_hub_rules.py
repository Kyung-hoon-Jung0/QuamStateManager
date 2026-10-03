"""Pins for the pure saved-state rules; all fixtures are synthetic."""

import copy
import hashlib
import json
import math

import pytest

from quam_state_manager.core import hub_rules as hub
from quam_state_manager.core.loader import QuamStore, merge_state_wiring


def changes(before, after):
    return hub.diff(hub.flatten(before), hub.flatten(after))


def test_numeric_leaves_and_literal_holder_paths():
    assert hub.flatten({"unit": {"frequency": 1, "gain": 0.5}}) == {
        "unit.frequency": 1, "unit.gain": 0.5,
    }


def test_booleans_strings_null_and_class_are_recorded():
    assert hub.flatten({"enabled": False, "label": "ready", "none": None, "__class__": "Device"}) == {
        "enabled": False, "label": "ready", "none": None, "__class__": "Device",
    }


def test_short_scalar_arrays_include_sixteen_elements():
    assert hub.flatten({"x": list(range(16))}) == {f"x.{i}": i for i in range(16)}


def test_long_scalar_arrays_start_at_seventeen_and_have_known_hash():
    payload = json.dumps(list(range(17)), separators=(",", ":")).encode()
    assert hub.flatten({"x": list(range(17))}) == {
        "x": {"_array": 17, "_hash": hashlib.sha1(payload).hexdigest()},
    }


def test_long_arrays_compare_by_content_and_length():
    a = {"x": list(range(17))}
    assert changes(a, copy.deepcopy(a)) == []
    b = {"x": list(range(17))}
    b["x"][8] = 99
    rows = changes(a, b)
    assert len(rows) == 1 and rows[0].path == "x" and rows[0].op == "set"
    assert json.loads(rows[0].old_txt)["_hash"] != json.loads(rows[0].txt)["_hash"]
    assert len(changes(a, {"x": list(range(18))})) == 1


def test_long_array_hash_obeys_numeric_equality_and_nan():
    a = [1, 0, float("nan")] + [3] * 14
    b = [1.0, -0.0, float("nan")] + [3.0] * 14
    assert changes({"x": a}, {"x": b}) == []
    # Adjacent integers outside float's exact range must not collapse.
    assert not hub.same(hub.flatten({"x": [2**60] * 17})["x"],
                        hub.flatten({"x": [2**60 + 1] * 17})["x"])


def test_long_scalar_arrays_include_strings_bools_and_null():
    a = ["ready", True, None] + [0] * 14
    assert hub.flatten({"x": a})["x"]["_array"] == 17
    b = a.copy()
    b[1] = 1
    assert len(changes({"x": a}, {"x": b})) == 1


def test_nested_lists_of_dicts_do_not_collapse():
    assert hub.flatten({"x": [{"v": i} for i in range(17)]}) == {
        f"x.{i}.v": i for i in range(17)
    }


def test_matrices_and_nested_long_rows():
    assert hub.flatten({"x": [[1, 2], [3, 4]]}) == {
        "x.0.0": 1, "x.0.1": 2, "x.1.0": 3, "x.1.1": 4,
    }
    flat = hub.flatten({"x": [list(range(17)), [{"v": True}]]})
    assert set(flat) == {"x.0", "x.1.0.v"}


def test_escaped_keys_do_not_collide():
    doc = {"a.b": 1, "a": {"b": 2}, "a\\b": 3, "": {"": 4}, "\\e": 5}
    assert hub.flatten(doc) == {"a\\.b": 1, "a.b": 2, "a\\\\b": 3, "\\e.\\e": 4, "\\\\e": 5}
    assert changes({"a.b": 1}, {"a.b": 2})[0].path == "a\\.b"


def test_empty_containers_have_no_leaves_and_root_leaf_uses_empty_path():
    assert hub.flatten({"a": [], "b": {}}) == {}
    assert hub.flatten(1) == {"": 1}
    assert hub.flatten([1, 2]) == {"0": 1, "1": 2}


def test_nonstring_keys_are_rejected():
    with pytest.raises(TypeError, match="keys must be strings"):
        hub.flatten({1: "ambiguous"})


def test_bool_is_never_a_number():
    for boolean, number in [(True, 1), (False, 0), (True, 1.0), (False, 0.0)]:
        assert not hub.same(boolean, number) and not hub.same(number, boolean)
        row = changes({"x": boolean}, {"x": number})[0]
        assert row.old_num is None and row.old_txt == json.dumps(boolean)
        assert row.num == number and row.txt is None
    assert hub.same(True, True) and not hub.same(True, False)


def test_int_float_type_only_change_is_equal():
    assert hub.same(1, 1.0) and hub.same(1.0, 1)
    assert hub.same(0, -0.0)
    assert not hub.same(2**60 + 1, float(2**60))
    assert changes({"x": 1}, {"x": 1.0}) == []


def test_floats_are_exact_without_tolerance():
    assert not hub.same(1.0, 1.0 + 1e-13)
    assert len(changes({"x": 1.0}, {"x": 1.0 + 1e-13})) == 1


def test_nan_constant_leaf_gives_zero_rows():
    assert hub.same(float("nan"), float("nan"))
    assert changes({"x": float("nan")}, {"x": float("nan")}) == []
    assert not hub.same(float("nan"), 0)
    assert not hub.same(float("nan"), 0.0)
    assert not hub.same(0.0, float("nan"))
    assert not hub.same(float("nan"), float("inf"))
    assert not hub.same(float("nan"), None)


def test_nonfinite_numbers_are_numeric_and_exact():
    assert hub.same(float("inf"), float("inf"))
    assert not hub.same(float("inf"), float("-inf"))
    row = changes({"x": 0}, {"x": float("nan")})[0]
    assert isinstance(row.num, float) and math.isnan(row.num) and row.txt is None
    assert row.old_num == 0 and row.old_txt is None


def test_strings_are_exact_and_never_numbers():
    assert hub.same("ready", "ready")
    assert not hub.same("ready", "Ready")
    assert not hub.same("1", 1)
    assert len(changes({"x": "ready"}, {"x": "Ready"})) == 1


def test_recursive_equality_keeps_bool_distinct_and_matches_nan():
    assert hub.same({"x": [1, float("nan")]}, {"x": [1.0, float("nan")]})
    assert not hub.same({"x": True}, {"x": 1})
    assert not hub.same([True], [1])
    assert not hub.same([1], [1, 2])
    assert not hub.same({"x": 1}, {"y": 1})
    assert not hub.same([], {})


def test_pointer_prefixes_are_raw_and_retargeted():
    for prefix in ("#/", "#../", "#./"):
        assert hub.flatten({"x": prefix + "missing"}) == {"x": prefix + "missing"}
        row = changes({"x": prefix + "old"}, {"x": "#/new"})[0]
        assert row.op == "retarget"
        assert row.old_txt == json.dumps(prefix + "old") and row.txt == '"#/new"'
        assert row.old_num is None and row.num is None


def test_pointer_to_plain_value_is_set_and_add_gone_stay_structural():
    assert changes({"x": "#/old"}, {"x": "plain"})[0].op == "set"
    assert changes({"x": "plain"}, {"x": "#/new"})[0].op == "set"
    assert changes({"x": "#word"}, {"x": "#other"})[0].op == "set"
    assert changes({}, {"x": "#/new"})[0].op == "add"
    assert changes({"x": "#/old"}, {})[0].op == "gone"


def test_pointer_target_change_does_not_change_the_holder():
    rows = changes({"holder": "#/target", "target": 1}, {"holder": "#/target", "target": 2})
    assert [row.path for row in rows] == ["target"]


def test_add_gone_set_old_and_new_and_null_presence():
    rows = changes({"gone": True, "set": 2, "null_gone": None},
                   {"add": "ready", "set": 3, "null_add": None})
    assert rows == [
        hub.Change("add", "add", txt='"ready"'),
        hub.Change("gone", "gone", old_txt="true"),
        hub.Change("null_add", "add", txt="null"),
        hub.Change("null_gone", "gone", old_txt="null"),
        hub.Change("set", "set", old_num=2, num=3),
    ]


def test_non_number_text_is_canonical_json():
    rows = hub.diff({"x": {"z": "µ", "a": False}}, {"x": {"z": "λ", "a": True}})
    assert rows[0].old_txt == '{"a":false,"z":"µ"}'
    assert rows[0].txt == '{"a":true,"z":"λ"}'
    assert rows[0].num is None and rows[0].old_num is None


def test_rows_are_lexically_sorted_by_path():
    assert [r.path for r in hub.diff({}, {"x.2": 2, "z": 0, "x.10": 10, "a": 1})] == [
        "a", "x.10", "x.2", "z",
    ]


def test_array_threshold_crossing_adds_and_removes_holder_paths():
    rows = changes({"x": list(range(16))}, {"x": list(range(17))})
    assert len(rows) == 17
    assert rows[0].path == "x" and rows[0].op == "add"
    assert all(row.op == "gone" for row in rows[1:])


def test_merge_is_the_shared_pure_loader_rule():
    from quam_state_manager.core.state_merge import merge_state_wiring as pure_merge
    assert hub.merged is pure_merge
    state = {"unit": {"keep": [1], "nested": {"keep": 1, "replace": 2}}, "replace": {"x": 1}}
    wiring = {"unit": {"nested": {"replace": 3, "add": 4}}, "replace": [5], "extra": True}
    expected = {"unit": {"keep": [1], "nested": {"keep": 1, "replace": 3, "add": 4}},
                "replace": [5], "extra": True}
    assert hub.merged(state, wiring) == expected
    assert merge_state_wiring(state, wiring) == expected
    assert QuamStore.from_dicts(state, wiring).merged == expected
    assert hub.merged(state, wiring)["unit"]["keep"] is state["unit"]["keep"]


def test_loader_preserves_top_level_collision_warnings(caplog):
    merge_state_wiring({"x": 1, "y": {}}, {"x": 2, "y": {"z": 3}})
    assert len(caplog.records) == 1
    assert caplog.records[0].getMessage() == (
        "Key 'x' exists in both state.json and wiring.json; wiring value will shadow state value"
    )


def test_all_pure_rules_leave_inputs_unchanged_and_do_not_log(caplog):
    state = {"x": {"v": [1] * 17}, "flag": True}
    wiring = {"x": {"new": False}, "flag": False}
    originals = copy.deepcopy((state, wiring))
    before = hub.flatten(state)
    after = hub.flatten(hub.merged(state, wiring))
    flat_originals = copy.deepcopy((before, after))
    hub.diff(before, after)
    hub.same(before, after)
    assert (state, wiring) == originals and (before, after) == flat_originals
    assert caplog.records == []


def test_state_hash_uses_original_bytes_delimiter_and_order():
    assert hub.state_hash(b'{"x":1}', b'{}') == hashlib.sha1(b'{"x":1}\0{}').hexdigest()
    assert hub.state_hash(b"ab", b"c") != hub.state_hash(b"a", b"bc")
    assert hub.state_hash(b"a", b"b") != hub.state_hash(b"b", b"a")
    assert hub.state_hash(b'{ "x": 1 }', b'{}') != hub.state_hash(b'{"x":1}', b'{}')


def test_run_by_run_reversion_is_recorded():
    a, b = {"x": 1}, {"x": 2}
    forward, back = changes(a, b), changes(b, a)
    assert forward == [hub.Change("x", "set", old_num=1, num=2)]
    assert back == [hub.Change("x", "set", old_num=2, num=1)]
    assert changes(a, a) == []
