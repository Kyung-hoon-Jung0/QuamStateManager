"""Tests for generator/probe_state_schema.py — the in-env class-schema dump.

Runs the REAL script under ``sys.executable`` against a synthetic package
built in ``tmp_path`` (no QM stack needed): pins the annotation→TypeSpec
mapping table, ClassVar exclusion, default/default_factory/reference-default
capture, the dotted-qualname import retry, unimportable-class degradation,
and the crash envelope. The SM-side manifest contract (by_leaf /
missing_classes derivation) is covered in test_state_env_schema.py.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = (Path(__file__).resolve().parent.parent
           / "quam_state_manager" / "generator" / "probe_state_schema.py")

_PKG_SRC = '''
from __future__ import annotations
import dataclasses
from dataclasses import field
from typing import Any, ClassVar, Dict, List, Literal, Optional, Tuple, Union


@dataclasses.dataclass
class Pulse:
    operation: ClassVar[str] = "control"
    length: int = None
    id: str = None
    digital_marker: Union[str, List[Tuple[int, int]]] = None


@dataclasses.dataclass
class FancyGate:
    flux_pulse: Union[Pulse, str] = None
    phase_shift: float = 0.0
    duration: Optional[float] = "#./inferred_duration"
    moving_qubit: Literal["control", "target"] = "control"
    confusion: Optional[List[List[float]]] = None
    spectators: Dict[str, Pulse] = field(default_factory=dict)
    bias: List[float] = field(default_factory=lambda: [0.0, 0.0])
    weird: "NoSuchName" = None            # unresolvable forward ref → any


class NotADataclass:
    x = 1


class Outer:
    @dataclasses.dataclass
    class Inner:
        depth: int = 3
'''


def _run_probe(tmp_path: Path, classes: list[str]) -> dict:
    pkg = tmp_path / "quamfake"
    pkg.mkdir(exist_ok=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "components.py").write_text(_PKG_SRC, encoding="utf-8")
    (tmp_path / "cls.json").write_text(
        json.dumps({"classes": classes, "pulse_roster": False}), encoding="utf-8")
    out = tmp_path / "out.json"
    env_path = str(tmp_path)
    r = subprocess.run(
        [sys.executable, str(_SCRIPT),
         "--classes", str(tmp_path / "cls.json"), "--out", str(out)],
        capture_output=True, text=True, timeout=120,
        env={**__import__("os").environ, "PYTHONPATH": env_path},
    )
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(out.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def manifest(tmp_path_factory) -> dict:
    tmp = tmp_path_factory.mktemp("probe")
    return _run_probe(tmp, [
        "quamfake.components.FancyGate",
        "quamfake.components.Pulse",
        "quamfake.components.NotADataclass",
        "quamfake.components.Outer.Inner",
        "no_such_package.Thing",
    ])


class TestMappingTable:
    def test_status_ok(self, manifest):
        assert manifest["status"] == "ok"

    def test_float_with_default(self, manifest):
        f = manifest["classes"]["quamfake.components.FancyGate"]["fields"]["phase_shift"]
        assert f["type"]["base"] == "float"
        assert f["has_default"] is True and f["default"] == 0.0

    def test_optional_float_with_reference_default(self, manifest):
        f = manifest["classes"]["quamfake.components.FancyGate"]["fields"]["duration"]
        assert f["type"]["base"] == "float" and f["type"]["optional"] is True
        assert f["default"] == "#./inferred_duration"
        assert f["default_is_reference"] is True

    def test_literal_enum(self, manifest):
        f = manifest["classes"]["quamfake.components.FancyGate"]["fields"]["moving_qubit"]
        assert f["type"]["base"] == "str"
        assert f["type"]["enum"] == ["control", "target"]

    def test_nested_list_of_lists(self, manifest):
        f = manifest["classes"]["quamfake.components.FancyGate"]["fields"]["confusion"]
        t = f["type"]
        assert t["base"] == "list" and t["optional"] is True
        assert t["item"]["base"] == "list" and t["item"]["item"]["base"] == "float"

    def test_union_component_str_collapses_to_component(self, manifest):
        # quam's "component or reference" idiom: the str arm is the pointer
        # form, covered by the global pointer bypass
        f = manifest["classes"]["quamfake.components.FancyGate"]["fields"]["flux_pulse"]
        assert f["type"]["base"] == "component"
        assert f["type"]["class"].endswith("components.Pulse")

    def test_dict_value_type(self, manifest):
        f = manifest["classes"]["quamfake.components.FancyGate"]["fields"]["spectators"]
        assert f["type"]["base"] == "dict"
        assert f["type"]["item"]["base"] == "component"
        assert f["has_default"] is True and f["default"] == {}

    def test_default_factory_value_captured(self, manifest):
        f = manifest["classes"]["quamfake.components.FancyGate"]["fields"]["bias"]
        assert f["default"] == [0.0, 0.0]

    def test_unresolvable_forward_ref_is_any(self, manifest):
        f = manifest["classes"]["quamfake.components.FancyGate"]["fields"]["weird"]
        assert f["type"]["base"] == "any"

    def test_classvar_excluded(self, manifest):
        fields = manifest["classes"]["quamfake.components.Pulse"]["fields"]
        assert "operation" not in fields
        assert "length" in fields and fields["length"]["type"]["base"] == "int"


class TestDegradation:
    def test_unimportable_package(self, manifest):
        e = manifest["classes"]["no_such_package.Thing"]
        assert e["importable"] is False
        assert e["fields"] is None
        assert "ModuleNotFoundError" in (e["error"] or "")

    def test_non_dataclass_abstains(self, manifest):
        e = manifest["classes"]["quamfake.components.NotADataclass"]
        assert e["importable"] is True
        assert e["fields"] is None      # null = abstain, never {} (unknown_field spam)

    def test_dotted_qualname_import_retry(self, manifest):
        e = manifest["classes"]["quamfake.components.Outer.Inner"]
        assert e["importable"] is True
        assert e["fields"]["depth"]["type"]["base"] == "int"

    def test_canonical_is_defining_path(self, manifest):
        e = manifest["classes"]["quamfake.components.Outer.Inner"]
        assert e["canonical"] == "quamfake.components.Outer.Inner"


class TestEnvelope:
    def test_bad_classes_file_is_structured_error(self, tmp_path):
        out = tmp_path / "o.json"
        bad = tmp_path / "nope.json"
        r = subprocess.run(
            [sys.executable, str(_SCRIPT), "--classes", str(bad), "--out", str(out)],
            capture_output=True, text=True, timeout=60)
        assert r.returncode == 1
        d = json.loads(out.read_text(encoding="utf-8"))
        assert d["status"] == "error" and d["error"]
        echo = json.loads(r.stdout.strip().splitlines()[-1])
        assert echo["status"] == "error"
