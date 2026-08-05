"""Retained env schema baselines + the manifest-vs-manifest diff (docs/79).

The gap these close: the schema cache is keyed by INTERPRETER and overwrites
itself when the env's versions change, so "this field was int under quam 0.6
and is str under 0.7" could not be computed — the old schema was gone.

The single most important property here is the anti-churn one: a diff must
report what the LIBRARY changed, not what its annotation display text happens
to render as this week. If phantom rows can appear, the whole feature becomes
noise the user learns to ignore.
"""
import copy
import json
from pathlib import Path

import pytest

from quam_state_manager.core import state_env_baseline as seb

_GOLDEN = Path(__file__).resolve().parent / "golden"


def _manifest(name):
    path = _GOLDEN / f"state_schema_{name}.json"
    if not path.exists():
        pytest.skip(f"golden manifest {name} missing")
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def modern():
    return _manifest("modern")


@pytest.fixture
def fork():
    return _manifest("fork")


class TestEnvIdentity:
    def test_the_key_is_order_independent(self):
        a = {"quam": "0.6.0", "quam_builder": "0.4.0", "qm": "1.3.1"}
        b = {"qm": "1.3.1", "quam_builder": "0.4.0", "quam": "0.6.0"}
        assert seb.env_key(a) == seb.env_key(b)

    def test_the_builder_commit_participates(self):
        base = {"quam": "0.6.0", "quam_builder": "0.4.0"}
        assert seb.env_key(base) != seb.env_key({**base, "quam_builder_commit": "abc"})

    def test_it_is_filename_safe_and_tolerates_missing_versions(self, tmp_path):
        key = seb.env_key({"quam": "0.5.0a3/weird\\name", "quam_builder": None})
        assert not set(key) & set('/\\:*?"<>|')
        (tmp_path / f"{key}.json").write_text("{}", encoding="utf-8")   # writable

    def test_version_distance_is_display_only_and_degrades(self):
        assert seb.version_distance({"quam": "0.6.0"}, {"quam": "0.6.0"}) == "same"
        assert seb.version_distance({"quam": "0.6.0"}, {"quam": "0.6.1"}) == "patch"
        assert seb.version_distance({"quam": "0.6.0"}, {"quam": "0.7.0"}) == "minor"
        assert seb.version_distance({"quam": "0.6.0"}, {"quam": "1.0.0"}) == "major"
        # a pre-release must never become a DECISION — only an unknown label
        assert seb.version_distance({"quam": "weird"}, {"quam": "0.6.0"}) == "unknown"


class TestTheStore:
    def test_a_version_change_keeps_the_previous_baseline(self, tmp_path, fork, modern):
        """THE gap this module closes — the cache overwrote, the baseline must not."""
        k1 = seb.record_baseline(tmp_path, fork)
        k2 = seb.record_baseline(tmp_path, modern)
        assert k1 and k2 and k1 != k2
        assert seb.load_baseline(tmp_path, k1) is not None, "the OLD schema survived"
        assert seb.load_baseline(tmp_path, k2) is not None
        assert (seb.previous_baseline(tmp_path, k2) or {}).get("key") == k1

    def test_re_recording_the_same_env_only_refreshes_last_seen(self, tmp_path, modern):
        k = seb.record_baseline(tmp_path, modern)
        first = seb.list_baselines(tmp_path)[0]
        seb.record_baseline(tmp_path, modern)
        again = seb.list_baselines(tmp_path)[0]
        assert len(seb.list_baselines(tmp_path)) == 1
        assert again["first_seen"] == first["first_seen"]

    def test_a_missing_index_is_rebuilt_from_the_bodies(self, tmp_path, modern, fork):
        seb.record_baseline(tmp_path, modern)
        seb.record_baseline(tmp_path, fork)
        (seb.baseline_dir(tmp_path) / seb.INDEX_FILENAME).unlink()
        assert len(seb.list_baselines(tmp_path)) == 2

    def test_a_corrupt_body_is_skipped_not_fatal(self, tmp_path, modern):
        seb.record_baseline(tmp_path, modern)
        (seb.baseline_dir(tmp_path) / "junk.json").write_text("{[", encoding="utf-8")
        (seb.baseline_dir(tmp_path) / seb.INDEX_FILENAME).unlink()
        assert len(seb.list_baselines(tmp_path)) == 1

    def test_it_prunes_but_never_the_one_just_recorded(self, tmp_path, modern):
        for i in range(seb._MAX_BASELINES + 3):
            seb.record_baseline(tmp_path, {**modern,
                                           "versions": {"quam": f"0.{i}.0",
                                                        "quam_builder": "0.4.0"}})
        keys = [e["key"] for e in seb.list_baselines(tmp_path)]
        assert len(keys) <= seb._MAX_BASELINES
        newest = seb.env_key({"quam": f"0.{seb._MAX_BASELINES + 2}.0",
                              "quam_builder": "0.4.0"})
        assert newest in keys

    def test_an_unidentifiable_env_is_not_recorded(self, tmp_path):
        assert seb.record_baseline(tmp_path, {"classes": {}, "versions": {}}) is None


class TestTheDiff:
    def test_it_finds_the_real_cross_generation_rename(self, fork, modern):
        diff = seb.diff_manifests(fork, modern)
        cz = [(r["kind"], r["field"]) for r in diff["rows"] if "CZGate" in r["class"]]
        assert ("field_removed", "duration_control") in cz
        assert ("field_added", "duration_qubit") in cz

    def test_a_raw_only_change_produces_NOTHING(self, modern):
        """The anti-churn invariant. ``raw`` is annotation display text and
        legitimately differs between generations for the same effective type;
        if it reached the diff, every upgrade would report hundreds of phantom
        changes and the feature would be pure noise."""
        churned = copy.deepcopy(modern)
        for entry in churned["classes"].values():
            for f in (entry.get("fields") or {}).values():
                if isinstance(f.get("type"), dict):
                    f["type"]["raw"] = "COMPLETELY DIFFERENT TEXT"
                f["default_repr"] = "noise"
                f["default"] = "noise"
            entry["bases"] = ["noise"]
        assert seb.diff_manifests(modern, churned)["total"] == 0

    def test_an_identical_manifest_diffs_to_nothing(self, modern):
        assert seb.diff_manifests(modern, copy.deepcopy(modern))["total"] == 0

    def test_a_type_change_is_reported_with_both_sides(self, modern):
        changed = copy.deepcopy(modern)
        cls = next(c for c, e in changed["classes"].items() if e.get("fields"))
        fld = next(iter(changed["classes"][cls]["fields"]))
        changed["classes"][cls]["fields"][fld]["type"] = {"base": "str", "raw": "str"}
        rows = [r for r in seb.diff_manifests(modern, changed)["rows"]
                if r["field"] == fld]
        assert rows and rows[0]["kind"] == "type_changed"
        assert rows[0]["new"]["base"] == "str"

    def test_an_abstained_class_produces_no_field_rows(self, modern):
        blinded = copy.deepcopy(modern)
        cls = next(c for c, e in blinded["classes"].items() if e.get("fields"))
        blinded["classes"][cls]["fields"] = None
        rows = [r for r in seb.diff_manifests(modern, blinded)["rows"]
                if r["class"] == cls]
        assert rows == [], "never flag what the probe could not introspect"
        assert cls in seb.diff_manifests(modern, blinded)["abstained"]

    def test_a_moved_class_is_a_move_not_a_death(self, modern):
        moved = copy.deepcopy(modern)
        old_path = next(c for c in moved["classes"] if "." in c)
        entry = moved["classes"].pop(old_path)
        entry["canonical"] = old_path                    # same defining home
        moved["classes"]["some.new.home." + old_path.rsplit(".", 1)[-1]] = entry
        diff = seb.diff_manifests(modern, moved)
        kinds = {r["kind"] for r in diff["rows"] if old_path in r["class"]}
        assert "class_removed" not in kinds
        assert diff["moved"]

    def test_rows_are_capped(self, modern):
        big = copy.deepcopy(modern)
        for entry in big["classes"].values():
            if entry.get("fields") is not None:
                entry["fields"] = {}
        diff = seb.diff_manifests(modern, big, cap=5)
        assert len(diff["rows"]) == 5 and diff["truncated"]


class TestTheTransition:
    def test_the_first_ever_probe_says_so_instead_of_inventing_a_diff(
            self, tmp_path, modern):
        seb.record_baseline(tmp_path, modern)
        t = seb.env_transition(tmp_path, modern)
        assert t["first"] is True and t["changed"] is False and t["diff"] is None

    def test_an_upgrade_reports_the_change(self, tmp_path, fork, modern):
        seb.record_baseline(tmp_path, fork)
        seb.record_baseline(tmp_path, modern)
        t = seb.env_transition(tmp_path, modern)
        assert t["changed"] and t["diff"]["total"] > 0
        assert t["from_label"].startswith("quam 0.5")
        assert t["distance"] in ("patch", "minor", "major", "unknown")

    def test_dismissal_is_delta_gated(self, tmp_path, fork, modern):
        seb.record_baseline(tmp_path, fork)
        seb.record_baseline(tmp_path, modern)
        t = seb.env_transition(tmp_path, modern)
        seb.dismiss_transition(tmp_path, t["from_key"], t["to_key"], t["sig"])
        assert seb.env_transition(tmp_path, modern)["dismissed"] is True
        seb.dismiss_transition(tmp_path, t["from_key"], t["to_key"], "somethingelse")
        assert seb.env_transition(tmp_path, modern)["dismissed"] is False

    def test_no_manifest_means_no_answer(self, tmp_path):
        assert seb.env_transition(tmp_path, None) is None
