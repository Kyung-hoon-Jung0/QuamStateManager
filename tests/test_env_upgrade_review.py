"""Batch leaf moves, pointer edges, one undo group and event acknowledgement."""
from __future__ import annotations

import copy

import pytest

from quam_state_manager.core import env_rename, env_seen
from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.modifier import Modifier


def fixture():
    state = {"objects": {
        "first": {"__class__": "extension.Child", "old": 2},
        "collision": {"__class__": "extension.Child", "old": 3, "new": 4},
        "redeclared": {"__class__": "extension.Own", "old": 5},
        "broken": {"__class__": "extension.Broken", "old": 6}},
        "absolute": "#/objects/first/old", "relative": "#./objects/first/old"}
    wiring = {"wiring": {"ref": "#/objects/first/old", "nested": "#/objects/first/old/0"}}
    manifest = {"classes": {"extension.Child": {"importable": True, "bases": ["official.Parent"],
                  "fields": {}}, "extension.Own": {"importable": True, "bases": ["official.Parent"],
                  "fields": {"old": {"defining_class": "extension.Own"}}},
                  "extension.Broken": {"importable": False, "bases": ["official.Parent"]}}}
    selections = [{"class": "official.Parent", "old": "old", "new": "new",
                   "confirmed": True, "meaning_unchanged": True}]
    return state, wiring, manifest, selections


def test_plan_rewrites_both_files_and_lists_collisions_and_import_failures():
    state, wiring, manifest, selections = fixture()
    planned = env_rename.plan(state, wiring, manifest, selections)
    assert planned["moves"] == [{"old": "objects.first.old", "new": "objects.first.new"}]
    assert planned["state"]["objects"]["first"]["new"] == 2
    assert planned["state"]["absolute"] == "#/objects/first/new"
    assert planned["state"]["relative"] == "#./objects/first/new"
    assert planned["wiring"]["wiring"]["nested"] == "#/objects/first/new/0"
    assert {row["reason"] for row in planned["skipped"]} == {"new key already exists", "class not importable"}
    assert state["objects"]["first"]["old"] == 2
    assert wiring["wiring"]["ref"] == "#/objects/first/old"


def test_apply_is_one_undo_group_and_touches_no_live_files():
    state, wiring, manifest, selections = fixture()
    original_state, original_wiring = copy.deepcopy(state), copy.deepcopy(wiring)
    store = QuamStore.from_dicts(state, wiring)
    modifier = Modifier(store)
    planned = env_rename.plan(store.state, store.wiring, manifest, selections)
    entries = env_rename.apply(modifier, planned)
    assert len({entry.group_id for entry in entries}) == 1
    assert store.state == planned["state"]
    assert store.wiring == planned["wiring"]
    modifier.undo_group()
    assert store.state == original_state
    assert store.wiring == original_wiring


def test_confirmations_are_per_row():
    state, wiring, manifest, selections = fixture()
    selections[0]["meaning_unchanged"] = False
    with pytest.raises(ValueError, match="meaning and unit"):
        env_rename.plan(state, wiring, manifest, selections)


def test_reference_owner_excludes_subclass_redeclaration():
    state, wiring, manifest, selections = fixture()
    reference = {"classes": {"extension.Child": {"fields": {"old": {"defining_class": "extension.Child"}}}}}
    result = env_rename.plan(state, wiring, manifest, selections, reference)
    assert not result["moves"]


def test_acknowledgement_reappears_for_changed_finding_set(tmp_path):
    previous = {"official_digest": "a"}
    current = {"official_digest": "b"}
    env_seen.observe(tmp_path, "key", "python", {**current, "versions": {"quam": "0.6"}}, context={})
    event = {"official_changed": True, "first": False, "stops_loading": True}
    assert env_seen.acknowledge(tmp_path, "key", "python", previous, current, "finding-a", context={}, event=event)
    assert env_seen.acknowledged(tmp_path, "key", "python", previous, current, "finding-a")
    assert not env_seen.acknowledged(tmp_path, "key", "python", previous, current, "finding-b")
    assert not env_seen.acknowledged(tmp_path, "other-key", "python", previous, current, "finding-a")


@pytest.mark.parametrize("event,context", [({"first": True, "official_changed": True}, {}),
    ({"first": False, "official_changed": True}, {}),
    ({"first": False, "official_changed": True, "stops_loading": True}, {"origin": "dataset_archive"})])
def test_no_ack_for_first_archive_or_fyi(tmp_path, event, context):
    assert not env_seen.acknowledge(tmp_path, "key", "python", {}, {}, "finding", context=context, event=event)


def test_accepted_rename_survives_observation_changes(tmp_path):
    env_seen.observe(tmp_path, "key", "python", {"versions": {"quam": "0.6"}}, context={})
    selection = fixture()[3]
    env_seen.remember_renames(tmp_path, "key", "python", selection, {"previous": {"official_digest": "a"}})
    env_seen.observe(tmp_path, "key", "python", {"versions": {"quam": "0.7"}}, context={})
    assert env_seen.accepted_renames(tmp_path, "key")[0]["new"] == "new"
