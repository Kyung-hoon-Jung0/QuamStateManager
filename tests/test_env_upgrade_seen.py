"""Transitions belong to a chip's last observation of the same interpreter."""
import copy

import pytest

from quam_state_manager.core import env_seen
from quam_state_manager.core import state_env_baseline as baseline


def body():
    return {"key": "reference", "versions": {"quam_builder": "0.4.0", "numpy": "2.0"},
            "official_digest": "official-a", "lab_digest": "lab-a", "source_fingerprint": "source-a"}


@pytest.mark.parametrize("field,value", [("official_digest", "b"),
    ("source_fingerprint", "b"), ("versions", {"quam_builder": "0.4.0", "numpy": "2.1"})])
def test_transition_rule_each_input(field, value):
    before = body()
    after = {**before, field: value}
    assert env_seen.transition(before, after)["changed"]
    assert not env_seen.transition(before, before)["changed"]


def test_lab_digest_only_is_fyi():
    event = env_seen.transition(body(), {**body(), "lab_digest": "lab-b"})
    assert event["lab_changed"] and not event["changed"] and not event["official_changed"]


def test_chip_and_env_are_separate_histories(tmp_path):
    first = env_seen.observe(tmp_path, "a", "env-a", body(), context={})
    assert first["first"] and not first["changed"]
    modified = {**body(), "official_digest": "b"}
    assert env_seen.observe(tmp_path, "b", "env-a", modified, context={})["first"]
    assert env_seen.observe(tmp_path, "a", "env-b", modified, context={})["first"]
    assert env_seen.observe(tmp_path, "a", "env-a", modified, context={})["changed"]


@pytest.mark.parametrize("context", [{"origin": "dataset_archive"}, {"origin": "run_snapshot"},
    {"readonly": True}, {"read_only": True}, {"live_readonly_hint": True}])
def test_archive_gate_writes_nothing(tmp_path, context):
    assert not env_seen.observe(tmp_path, "a", "env-a", body(), context=context)["recorded"]
    assert not list(tmp_path.iterdir())


def test_downgrade_and_ab_alternation(tmp_path):
    before = body()
    after = copy.deepcopy(before)
    after["versions"]["quam_builder"] = "0.6.1"
    env_seen.observe(tmp_path, "a", "env-a", before, context={})
    upgrade = env_seen.observe(tmp_path, "a", "env-a", after, context={})
    assert "quam-builder 0.4.0 \u2192 0.6.1 (upgrade)" == upgrade["direction"]
    downgrade = env_seen.observe(tmp_path, "a", "env-a", before, context={})
    assert "(downgrade)" in downgrade["direction"]
    again = env_seen.observe(tmp_path, "a", "env-a", after, context={})
    assert again["changed"]
    repeated = env_seen.observe(tmp_path, "a", "env-a", after, context={})
    assert not repeated["changed"]
    assert repeated["current"]["pending"]["previous"]["versions"]["quam_builder"] == "0.4.0"


def test_same_version_direction():
    assert "class definitions changed" in env_seen.direction(body(), {**body(), "official_digest": "b"})
    assert "source files changed" in env_seen.direction(body(), {**body(), "source_fingerprint": "b"})


def test_other_chips_classes_do_not_enter_the_digest():
    classes = {"extension.Child": {"bases": ["official.Parent"]},
               "official.Parent": {"bases": ["builtins.object"]},
               "unrelated.Other": {"bases": []}}
    selected = baseline.select_classes({"classes": classes}, ["extension.Child"])
    assert set(selected["classes"]) == {"extension.Child", "official.Parent"}


def test_observation_protects_its_reference(tmp_path, monkeypatch):
    monkeypatch.setattr(baseline, "_MAX_BASELINES", 1)
    original = {"classes": {}, "versions": {"quam": "0.6.0"}}
    key = baseline.record_baseline(tmp_path, original, python_path="env-a")
    stored = baseline.load_baseline(tmp_path, key)
    env_seen.observe(tmp_path, "a", "env-a", stored, context={})
    baseline.record_baseline(tmp_path, {**original, "sources": {"file.py": [1, 2]}}, python_path="env-a")
    assert baseline.load_baseline(tmp_path, key)
