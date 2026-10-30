"""Chip-exact case verdicts and one planted fault per offline subprocess."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from quam_state_manager.core import env_upgrade
from quam_state_manager.core.state_env_baseline import project_manifest


def schemas():
    field = {"type": {"base": "int"}, "has_default": True, "default": 1,
             "defining_class": "official.Parent"}
    return {"schema_format": 4, "classes": {
        "official.Parent": {"importable": True, "origin": "official", "bases": [],
                            "signatures": {}, "fields": {"old": field}},
        "extension.Child": {"importable": True, "origin": "lab", "bases": ["official.Parent"],
                            "signatures": {}, "fields": {"old": copy.deepcopy(field)}}}}


def reference(manifest):
    return {**project_manifest(manifest), "version": 2}


def test_unknown_fields_uncapped_and_all_object_paths_are_exact():
    old = schemas()
    current = copy.deepcopy(old)
    for entry in old["classes"].values():
        for i in range(325):
            entry["fields"][f"removed{i}"] = copy.deepcopy(entry["fields"]["old"])
    state = {"items": [{"__class__": "extension.Child", **{f"removed{i}": 1 for i in range(325)}}
                       for _ in range(7)]}
    result = env_upgrade.report(state, current, reference(old))
    assert len(result["cases"]["3"]["rows"]) == 325
    assert all(len(row["paths"]) == 7 for row in result["cases"]["3"]["rows"])


def test_rename_candidates_require_same_defining_class_and_carry_evidence():
    old = schemas()
    current = copy.deepcopy(old)
    for entry in current["classes"].values():
        field = entry["fields"].pop("old")
        field["doc"] = "Duration in samples."
        entry["fields"]["new"] = field
    current["classes"]["extension.Child"]["fields"]["unrelated"] = {
        "type": {"base": "int"}, "has_default": True, "defining_class": "extension.Child"}
    result = env_upgrade.report({"__class__": "extension.Child", "old": 1}, current, reference(old))
    row = result["cases"]["3"]["rows"][0]
    assert row["was_declared"] and row["heuristic"]
    assert [c["field"] for c in row["candidates"]] == ["new"]
    assert row["candidates"][0]["doc"] == "Duration in samples."
    assert "score" not in row["candidates"][0]


def test_default_removed_marks_only_missing_objects():
    old = schemas()
    current = copy.deepcopy(old)
    for entry in current["classes"].values():
        entry["fields"]["old"]["has_default"] = False
    result = env_upgrade.report({"items": [{"__class__": "extension.Child"},
        {"__class__": "extension.Child", "old": 2}]}, current, reference(old))
    row = result["cases"]["2"]["rows"][0]
    assert row["default_removed"]
    assert row["paths"] == ["items.0.old"]


@pytest.mark.parametrize("reference_value", [None, {"version": 1},
    {"version": 2, "schema_format": 3}])
def test_older_reference_checks_abstain(reference_value):
    result = env_upgrade.report({"__class__": "extension.Child"}, schemas(), reference_value)
    assert result["cases"]["1"]["status"] == "not_checked"
    assert result["cases"]["5"]["status"] == "not_checked"


def test_probe_timeout_does_not_say_none_found():
    result = env_upgrade.report({}, {"ok": False, "error": "probe timeout"})
    for n in range(1, 6):
        assert "could not check" in result["cases"][str(n)]["message"]


def test_non_waveform_values_not_just_keys():
    before = {"integration_weights": {"w": {"cosine": [[1, 4]]}}, "controllers": {
        "controller": {"analog_outputs": {"1": {"filter": {"feedforward": [1]}, "crosstalk": {"2": .1}}}}}}
    after = copy.deepcopy(before)
    after["integration_weights"]["w"]["cosine"][0][0] = 2
    after["controllers"]["controller"]["analog_outputs"]["1"]["crosstalk"]["2"] = .2
    rows = env_upgrade.non_waveform_diff(before, after)
    assert len(rows) == 2
    assert any(row["path"].endswith("crosstalk") for row in rows)


_DRIVER = '''
import json
from pathlib import Path
import sys
from quam_state_manager.generator.probe_state_schema import dump_schemas
from quam_state_manager.core.state_env_baseline import project_manifest
from quam_state_manager.core.env_upgrade import report, verdict_lines
folder = Path(sys.argv[1])
current = dump_schemas(["extension_fixture.Child", "official_fixture.Other"], pulse_roster=False)
current["schema_format"] = 4
old_path = folder / "reference.json"
if old_path.exists():
    old = json.loads(old_path.read_text())
else:
    old = dict(project_manifest(current), version=2)
    old_path.write_text(json.dumps(old))
state = {"first": {"__class__": "extension_fixture.Child", "length": 4, "old": 1, "extra_phase": 0.0},
         "second": {"__class__": "official_fixture.Other"}}
result = report(state, current, old, catalog={"moved.Parent": {"importable": True}})
(folder / "report.json").write_text(json.dumps(result))
print("\\n".join(verdict_lines(result)))
'''


def _fault_run(tmp_path, fault):
    site = tmp_path / "Lib/site-packages/official_fixture"
    site.mkdir(parents=True)
    (tmp_path / "extension_fixture.py").write_text(
        "from official_fixture import Parent\nfrom dataclasses import dataclass\n"
        "@dataclass(kw_only=True)\nclass Child(Parent):\n    extra_phase: float = 0.0\n"
        "    def samples(self, length):\n        return [1]\n", encoding="utf-8")

    def write(variant):
        fields = "    length: int = 4\n"
        fields += "    new: int = 1\n" if variant in ("3", "combined") else "    old: int = 1\n"
        if variant in ("1", "combined"):
            fields += "    added: int = 2\n"
        if variant in ("2", "combined"):
            fields += "    required: int\n"
        name = "MovedParent" if variant == "4" else "Parent"
        phase = ", phase" if variant in ("5", "combined") else ""
        source = ("from dataclasses import dataclass\n@dataclass(kw_only=True)\n"
                  f"class {name}:\n{fields}    def samples(self, length{phase}):\n        return [1]\n")
        source += "@dataclass\nclass Other:\n" + (
            "    extra_default: int = 1\n" if variant == "combined" else "    pass\n")
        (site / "__init__.py").write_text(source, encoding="utf-8")
        # Disable bytecode so equal-length source variants cannot reuse stale code.

    driver = tmp_path / "driver.py"
    driver.write_text(_DRIVER, encoding="utf-8")
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(site.parent), str(root), str(tmp_path)]),
           "PYTHONDONTWRITEBYTECODE": "1"}

    def run():
        process = subprocess.run([sys.executable, str(driver), str(tmp_path)], env=env,
                                 capture_output=True, text=True, encoding="utf-8", timeout=120)
        assert process.returncode == 0, process.stderr
        return json.loads((tmp_path / "report.json").read_text()), process.stdout

    write("baseline")
    before, _ = run()
    write(fault)
    after, lines = run()
    return before, after, lines


@pytest.mark.parametrize("fault", ["1", "2", "3", "4", "5", "combined"])
def test_offline_fault_verdict_and_other_cases_unchanged(tmp_path, fault):
    before, after, lines = _fault_run(tmp_path, fault)
    changed = {"1", "2", "3", "5"} if fault == "combined" else {fault}
    for n in map(str, range(1, 6)):
        assert before["cases"][n]["status"] == "none_found"
        assert after["cases"][n]["status"] == ("found" if n in changed else "none_found")
        if n not in changed:
            assert after["cases"][n] == before["cases"][n]
    assert all(f"VERDICT case {n}: found" in lines for n in changed)
    if "5" in changed:
        assert [row["method"] for row in after["cases"]["5"]["rows"]] == ["samples"]
    if fault == "4":
        row = after["cases"]["4"]["rows"][0]
        assert row["frames"][0]["file"].endswith("extension_fixture.py")
        assert row["candidates"] == ["moved.Parent"]
