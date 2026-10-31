"""Reference evidence, planted package faults, and retention boundaries."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from quam_state_manager.core import state_env_baseline as baseline


def manifest():
    return {"schema_format": 4, "versions": {"quam": "0.6.0", "numpy": "2.0"},
            "sources": {"lab/extension.py": [1, 20]}, "classes": {
                "official.Parent": {"importable": True, "origin": "official",
                    "bases": ["builtins.object"], "signatures": {}, "fields": {
                        "length": {"type": {"base": "int"}, "has_default": True,
                                   "defining_class": "official.Parent"}}},
                "extension.Child": {"importable": True, "origin": "lab",
                    "bases": ["official.Parent", "builtins.object"],
                    "signatures": {}, "fields": {}}}}


def test_two_part_digest_tracks_only_its_own_classes():
    original = manifest()
    before = baseline.project_manifest(original)
    changed = copy.deepcopy(original)
    changed["classes"]["extension.Child"]["bases"].append("extension.Mixin")
    after = baseline.project_manifest(changed)
    assert before["official_digest"] == after["official_digest"]
    assert before["lab_digest"] != after["lab_digest"]
    changed = copy.deepcopy(original)
    changed["classes"]["official.Parent"]["fields"]["length"]["has_default"] = False
    after = baseline.project_manifest(changed)
    assert before["official_digest"] != after["official_digest"]
    assert before["lab_digest"] == after["lab_digest"]


def test_fingerprint_detects_computation_only_source_change():
    original = manifest()
    before = baseline.project_manifest(original)
    original["sources"]["lab/extension.py"][0] += 1
    after = baseline.project_manifest(original)
    assert before["source_fingerprint"] != after["source_fingerprint"]
    assert before["official_digest"] == after["official_digest"]


def test_bodies_survive_same_version_changes_and_env_aliases(tmp_path):
    original = manifest()
    env = str(tmp_path / "env" / "python.exe")
    first = baseline.record_baseline(tmp_path, original, python_path=env)
    original["classes"]["official.Parent"]["bases"].append("official.Mixin")
    second = baseline.record_baseline(tmp_path, original, python_path=env)
    assert first != second
    assert baseline.load_baseline(tmp_path, first)["version"] == 2
    assert baseline.load_baseline(tmp_path, second)["classes"]["official.Parent"]["bases"]
    assert baseline.normalize_env_path(env) == baseline.normalize_env_path(
        str(tmp_path / "env" / "pythonw.exe"))


def test_prune_guard_and_per_env_bound(tmp_path, monkeypatch):
    monkeypatch.setattr(baseline, "_MAX_BASELINES", 2)
    original = manifest()
    first = baseline.record_baseline(tmp_path, original, python_path="env-a")
    seen = tmp_path / "env_seen"
    seen.mkdir()
    (seen / "record.json").write_text(json.dumps({"envs": {
        "env-a": {"baseline_key": first}}}), encoding="utf-8")
    for i in range(6):
        original["sources"]["lab/extension.py"][0] = i + 2
        baseline.record_baseline(tmp_path, original, python_path="env-a")
        baseline.record_baseline(tmp_path, original, python_path="env-b")
    assert baseline.load_baseline(tmp_path, first) is not None
    entries = baseline.list_baselines(tmp_path)
    assert len(entries) == 4
    assert len({e["python_path"] for e in entries}) == 2


def test_byte_budget_prunes_unreferenced_bodies(tmp_path, monkeypatch):
    monkeypatch.setattr(baseline, "_MAX_ENV_BYTES", 1)
    original = manifest()
    baseline.record_baseline(tmp_path, original, python_path="env-a")
    original["sources"]["lab/extension.py"][0] += 1
    newest = baseline.record_baseline(tmp_path, original, python_path="env-a")
    assert [e["key"] for e in baseline.list_baselines(tmp_path)] == [newest]


def test_version_one_body_remains_readable(tmp_path):
    folder = baseline.baseline_dir(tmp_path)
    folder.mkdir()
    (folder / "old.json").write_text(json.dumps({
        "version": 1, "classes": {}, "versions": {"quam": "0.5"}}), encoding="utf-8")
    assert baseline.load_baseline(tmp_path, "old")["version"] == 1


def test_deep_mro_is_not_capped(tmp_path, monkeypatch):
    from quam_state_manager.generator import probe_state_schema as probe

    parent = type("Base", (), {})
    for i in range(12):
        parent = type(f"Layer{i}", (parent,), {})
    monkeypatch.setattr(probe, "_import_class", lambda _: parent)
    assert len(probe._dump_class("fixture.Layer")["bases"]) == 13


def test_both_version_reporters_include_numeric_libraries():
    from quam_state_manager.generator._script_common import library_versions
    from quam_state_manager.core.config_generator import _PROBE_SRC

    assert {"numpy", "scipy"} <= library_versions().keys()
    result = subprocess.run([sys.executable, "-c", _PROBE_SRC],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert {"numpy", "scipy"} <= json.loads(result.stdout).keys()


@pytest.mark.parametrize("fault", ["default", "required", "rename", "import", "signature", "body"])
def test_probe_planted_fault_evidence(tmp_path, fault):
    """The in-env probe consumes an official fixture and an external subclass."""
    site = tmp_path / "Lib" / "site-packages"
    package = site / "official_fixture"
    package.mkdir(parents=True)
    additions = {"default": "    added: int = 4\n", "required": "    added: int\n",
                 "rename": "    renamed: int = 1\n"}
    source = ("from dataclasses import dataclass\n@dataclass(kw_only=True)\nclass Parent:\n"
              "    length: int = 1\n" + additions.get(fault, "") +
              "    def samples(self, length" + (", phase" if fault == "signature" else "") +
              "):\n        return " + ("[0, 1]" if fault == "body" else "[1]") + "\n")
    (package / "__init__.py").write_text(source, encoding="utf-8")
    extension = tmp_path / "extension_fixture.py"
    extension.write_text("from official_fixture import " +
        ("Missing" if fault == "import" else "Parent") +
        "\nfrom dataclasses import dataclass\n@dataclass\nclass Child(Parent):\n"
        "    extra_phase: float = 0.0\n    def samples(self, length):\n        return [1]\n",
        encoding="utf-8")
    (tmp_path / "classes.json").write_text(json.dumps({
        "classes": ["extension_fixture.Child"], "pulse_roster": False}), encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / "quam_state_manager/generator/probe_state_schema.py"
    result = subprocess.run([sys.executable, str(script), "--classes", str(tmp_path / "classes.json"),
        "--out", str(tmp_path / "result.json")], capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONPATH": os.pathsep.join([str(site), str(tmp_path)])}, timeout=120)
    assert result.returncode == 0, result.stderr
    data = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    child = data["classes"]["extension_fixture.Child"]
    if fault == "import":
        assert child["error_type"] == "ImportError"
        assert any(f["file"] == str(extension) and f["line"] == 1 for f in child["error_frames"])
        assert all("site-packages" not in f["file"] for f in child["error_frames"])
    else:
        assert child["origin"] == "lab"
        parent = data["classes"]["official_fixture.Parent"]
        assert parent["origin"] == "official"
        assert child["bases"] == ["official_fixture.Parent", "builtins.object"]
        assert child["fields"]["length"]["defining_class"] == "official_fixture.Parent"
        assert child["signatures"]["samples"]["parent"]["count"] == (
            "3" if fault == "signature" else "2")
        if fault in ("default", "required"):
            assert parent["fields"]["added"]["has_default"] == (fault == "default")
        if fault == "rename":
            assert "renamed" in parent["fields"]
    assert {"numpy", "scipy"} <= data["versions"].keys()
