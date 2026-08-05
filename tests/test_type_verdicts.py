"""Env-scoped user verdicts — "in this environment, that type is right now"
(docs/79).

Two things must hold for this to be safe to ship:

* **Dormancy.** With no verdict stored, every resolution is byte-identical to
  the env-only behaviour — the mechanism is invisible until used.
* **Carry is gated on the FIELD, not on the version number.** A verdict made
  against quam 0.7 applies to 0.7.1 iff that class·field still looks the way it
  did when the user decided. Anything else and an upgrade could silently
  enforce a stale opinion.
"""
import copy
import json
from pathlib import Path

import pytest

from quam_state_manager.core import state_env_schema as ses
from quam_state_manager.core import type_policy as tp
from quam_state_manager.core import type_verdicts as tv

_GOLDEN = Path(__file__).resolve().parent / "golden"


def _manifest(name="modern"):
    path = _GOLDEN / f"state_schema_{name}.json"
    if not path.exists():
        pytest.skip(f"golden manifest {name} missing")
    return ses._decorate(json.loads(path.read_text(encoding="utf-8")))


@pytest.fixture
def manifest():
    return _manifest()


@pytest.fixture
def cls_field(manifest):
    """A real class·field whose env type is numeric."""
    for cp, entry in manifest["classes"].items():
        for name, f in (entry.get("fields") or {}).items():
            if (f.get("type") or {}).get("base") in ("float", "int"):
                return cp, name
    pytest.skip("no numeric field in the golden manifest")


@pytest.fixture
def state(cls_field):
    cp, field = cls_field
    return {"qubits": {"q1": {"__class__": cp, field: 5}}}


@pytest.fixture
def path(cls_field):
    return f"qubits.q1.{cls_field[1]}"


class TestDormancy:
    def test_no_store_means_no_verdicts_and_no_overlay(self, tmp_path, manifest):
        policy = tp.load_policy(tmp_path, tmp_path / "chip", manifest)
        assert policy.verdicts == {}
        assert policy.manifest is policy.env_manifest, \
            "with nothing taught, the manifest must not even be copied"

    def test_an_unreadable_store_is_empty_not_fatal(self, tmp_path):
        tv.verdicts_path(tmp_path).write_text("{[not json", encoding="utf-8")
        assert tv.load_store(tmp_path)["envs"] == {}


class TestSaveAndResolve:
    def test_an_override_changes_the_expectation(self, tmp_path, manifest,
                                                 cls_field, state, path):
        cp, field = cls_field
        env_spec, _ = tv.env_field_spec(manifest, cp, field)
        tv.save_verdict(tmp_path, manifest["versions"], cp, field,
                        decision="override", spec={"base": "str"},
                        type_expr="str", env_spec=env_spec)
        policy = tp.load_policy(tmp_path, tmp_path / "chip", manifest)
        exp = policy.expected_for(state, path)
        assert exp.source == "verdict" and exp.spec["base"] == "str"
        assert exp.enforced, "teaching SM must let the user WRITE that type"

    def test_an_accept_records_agreement_without_changing_anything(
            self, tmp_path, manifest, cls_field, state, path):
        cp, field = cls_field
        env_spec, _ = tv.env_field_spec(manifest, cp, field)
        tv.save_verdict(tmp_path, manifest["versions"], cp, field,
                        decision="accept", spec=env_spec, spec_source="env",
                        env_spec=env_spec)
        policy = tp.load_policy(tmp_path, tmp_path / "chip", manifest)
        assert policy.expected_for(state, path).source == "env"
        assert len(policy.verdicts) == 1, "the answer is remembered..."
        assert policy.manifest is policy.env_manifest, "...but nothing is overlaid"

    def test_an_override_needs_a_spec(self, tmp_path, manifest, cls_field):
        with pytest.raises(ValueError):
            tv.save_verdict(tmp_path, manifest["versions"], cls_field[0],
                            cls_field[1], decision="override")

    def test_revoke_restores_the_environment(self, tmp_path, manifest,
                                             cls_field, state, path):
        cp, field = cls_field
        env_spec, _ = tv.env_field_spec(manifest, cp, field)
        tv.save_verdict(tmp_path, manifest["versions"], cp, field,
                        decision="override", spec={"base": "str"}, env_spec=env_spec)
        key = list(tv.load_store(tmp_path)["envs"])[0]
        assert tv.revoke_verdict(tmp_path, key, cp, field) is True
        policy = tp.load_policy(tmp_path, tmp_path / "chip", manifest)
        assert policy.expected_for(state, path).source == "env"


class TestTheCarryRule:
    """Whether a verdict still applies is decided by the FIELD, not the version."""

    def _teach(self, tmp_path, versions, cp, field, env_spec):
        tv.save_verdict(tmp_path, versions, cp, field, decision="override",
                        spec={"base": "str"}, env_spec=env_spec)

    def test_same_env_same_field_is_exact(self, tmp_path, manifest, cls_field):
        cp, field = cls_field
        env_spec, _ = tv.env_field_spec(manifest, cp, field)
        self._teach(tmp_path, manifest["versions"], cp, field, env_spec)
        r = tv.resolve_for_manifest(tmp_path, manifest)
        entry = next(iter(r.values()))
        assert entry["status"] == tv.EXACT and entry["enforced"]

    def test_a_later_version_carries_when_the_field_did_not_move(
            self, tmp_path, manifest, cls_field):
        cp, field = cls_field
        env_spec, _ = tv.env_field_spec(manifest, cp, field)
        self._teach(tmp_path, manifest["versions"], cp, field, env_spec)
        upgraded = copy.deepcopy(manifest)
        upgraded["versions"] = {**manifest["versions"], "quam": "9.9.9"}
        entry = next(iter(tv.resolve_for_manifest(tmp_path, upgraded).values()))
        assert entry["status"] == tv.CARRIED and entry["enforced"]

    def test_a_moved_field_needs_re_confirming_and_is_NOT_enforced(
            self, tmp_path, manifest, cls_field, state, path):
        cp, field = cls_field
        env_spec, _ = tv.env_field_spec(manifest, cp, field)
        self._teach(tmp_path, manifest["versions"], cp, field, env_spec)
        moved = copy.deepcopy(manifest)
        moved["versions"] = {**manifest["versions"], "quam": "9.9.9"}
        moved["classes"][cp]["fields"][field]["type"] = {
            "base": "list", "item": {"base": "float"}, "raw": "list[float]"}
        entry = next(iter(tv.resolve_for_manifest(tmp_path, moved).values()))
        assert entry["status"] == tv.NEEDS_REAFFIRM
        assert not entry["enforced"], "a stale opinion must never be enforced silently"
        policy = tp.load_policy(tmp_path, tmp_path / "chip", moved)
        assert policy.expected_for(state, path).source == "env"

    def test_the_env_catching_up_makes_it_obsolete(self, tmp_path, manifest,
                                                   cls_field):
        cp, field = cls_field
        env_spec, _ = tv.env_field_spec(manifest, cp, field)
        self._teach(tmp_path, manifest["versions"], cp, field, env_spec)
        caught_up = copy.deepcopy(manifest)
        caught_up["classes"][cp]["fields"][field]["type"] = {"base": "str",
                                                             "raw": "str"}
        entry = next(iter(tv.resolve_for_manifest(tmp_path, caught_up).values()))
        assert entry["status"] == tv.OBSOLETE and not entry["enforced"]


class TestWhatCannotBeTaught:
    def test_a_field_the_env_does_not_declare(self, manifest, cls_field):
        spec, reason = tv.env_field_spec(manifest, cls_field[0], "definitely_not_here")
        assert spec is None and reason == "unknown_field", \
            "that is a Quam.load() crash, not a disagreement about types"

    def test_an_abstained_class(self, manifest, cls_field):
        blinded = copy.deepcopy(manifest)
        blinded["classes"][cls_field[0]]["fields"] = None
        spec, reason = tv.env_field_spec(blinded, cls_field[0], cls_field[1])
        assert spec is None and reason == "abstained"

    def test_an_unknown_class(self, manifest):
        spec, reason = tv.env_field_spec(manifest, "no.such.Class", "x")
        assert spec is None and reason == "unknown_class"


class TestTheOverlay:
    def test_it_does_not_mutate_the_original(self, tmp_path, manifest, cls_field):
        cp, field = cls_field
        before = copy.deepcopy(manifest["classes"][cp]["fields"][field]["type"])
        resolved = {f"{manifest['classes'][cp].get('canonical') or cp}.{field}": {
            "enforced": True, "spec": {"base": "str"}, "from_label": "x",
            "status": "exact", "decided_at": "now", "note": ""}}
        overlaid = tv.overlay_manifest(manifest, resolved)
        assert manifest["classes"][cp]["fields"][field]["type"] == before
        assert overlaid["classes"][cp]["fields"][field]["type"]["base"] == "str"
        assert overlaid["classes"][cp]["fields"][field]["env_type"] == before, \
            "both sides must stay visible — the UI has to show the disagreement"

    def test_untouched_classes_are_shared_not_copied(self, manifest, cls_field):
        cp, field = cls_field
        other = next(c for c in manifest["classes"] if c != cp)
        resolved = {f"{manifest['classes'][cp].get('canonical') or cp}.{field}": {
            "enforced": True, "spec": {"base": "str"}, "status": "exact"}}
        overlaid = tv.overlay_manifest(manifest, resolved)
        assert overlaid["classes"][other] is manifest["classes"][other]

    def test_an_empty_resolution_is_a_no_op(self, manifest):
        assert tv.overlay_manifest(manifest, {}) is manifest

    def test_the_signature_tracks_only_enforced_verdicts(self):
        enforced = {"A.b": {"enforced": True, "spec": {"base": "str"}}}
        assert tv.verdict_signature(enforced)
        assert tv.verdict_signature({"A.b": {"enforced": False,
                                             "spec": {"base": "str"}}}) == ""


class TestLayering:
    def test_a_per_key_assignment_still_outranks_a_verdict(
            self, tmp_path, manifest, cls_field, state, path):
        cp, field = cls_field
        env_spec, _ = tv.env_field_spec(manifest, cp, field)
        tv.save_verdict(tmp_path, manifest["versions"], cp, field,
                        decision="override", spec={"base": "str"}, env_spec=env_spec)
        base = tp.load_policy(tmp_path, tmp_path / "chip", manifest)
        policy = tp.TypePolicy(base.manifest,
                               {path: {"type": "int", "override_env": True}},
                               verdicts=base.verdicts, env_manifest=manifest)
        exp = policy.expected_for(state, path)
        assert exp.source == "user", \
            "one exact path on one chip is more specific than a class-wide verdict"

    def test_a_path_BELOW_the_field_is_not_claimed(self, tmp_path, manifest,
                                                    cls_field, state, path):
        cp, field = cls_field
        env_spec, _ = tv.env_field_spec(manifest, cp, field)
        tv.save_verdict(tmp_path, manifest["versions"], cp, field,
                        decision="override", spec={"base": "list"}, env_spec=env_spec)
        policy = tp.load_policy(tmp_path, tmp_path / "chip", manifest)
        deeper = policy.expected_for(state, path + ".0")
        assert deeper is None or deeper.source != "verdict"

    def test_the_blocked_write_message_names_the_verdict(self, tmp_path, manifest,
                                                         cls_field, state, path):
        cp, field = cls_field
        env_spec, _ = tv.env_field_spec(manifest, cp, field)
        tv.save_verdict(tmp_path, manifest["versions"], cp, field,
                        decision="override", spec={"base": "str"}, env_spec=env_spec)
        policy = tp.load_policy(tmp_path, tmp_path / "chip", manifest)
        exp = policy.expected_for(state, path)
        with pytest.raises(tp.TypeMismatchError) as e:
            policy.check(exp, 5, path=path)
        assert "taught" in str(e.value).lower()


class TestBlastRadius:
    def test_conflicting_leaves_are_counted_before_saving(self, manifest, cls_field):
        cp, field = cls_field
        state = {"qubits": {f"q{i}": {"__class__": cp, field: 5} for i in range(4)}}
        hits = tv.conflicting_leaves(state, manifest, cp, field, {"base": "str"})
        assert len(hits) == 4, "the user must see how far a class-wide verdict reaches"

    def test_pointers_are_never_counted_as_conflicts(self, manifest, cls_field):
        cp, field = cls_field
        state = {"qubits": {"q1": {"__class__": cp, field: "#/qubits/q0/x"}}}
        assert tv.conflicting_leaves(state, manifest, cp, field, {"base": "int"}) == []
