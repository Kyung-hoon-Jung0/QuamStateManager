"""LIVE schema-probe tests against real QM-stack interpreters (skip-gated).

The interpreter paths come from environment variables (never hardcoded — the
public repo carries no local env paths); unset ⇒ skip:

* ``QSM_ENV_MODERN`` — a python(.exe) with the MODERN stack
  (quam 0.6.0 / quam_builder 0.4.0)
* ``QSM_ENV_FORK``   — a python(.exe) with the fork-pin generation
  (quam 0.5.0a3 / quam_builder 0.2.0)

Runs generator/probe_state_schema.py inside each and pins exactly the
generation deltas the quam-builder ground-truth audit documented — what the
state↔env validator's findings stand on:

* modern: CZGate has ``duration_qubit``, lacks ``moving_qubit``/
  ``duration_control``; the pulse roster has NO ErfSquarePulse and ships
  SNZPulse from the quam_builder architecture home with a ``padding`` field.
* fork: CZGate has ``duration_control`` + ``moving_qubit``; ErfSquarePulse
  lives in quam.components.pulses with ``post_zero_padding_length``.

Also asserts the ≥90% non-``any`` field-coverage bar on both (the type
layer's value proposition dies if old forks resolve to ``any``).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

_SCRIPT = (Path(__file__).resolve().parent.parent
           / "quam_state_manager" / "generator" / "probe_state_schema.py")

_ENVS = {
    "modern": Path(os.environ.get("QSM_ENV_MODERN", "/nonexistent")),
    "fork": Path(os.environ.get("QSM_ENV_FORK", "/nonexistent")),
}

_CZ = ("quam_builder.architecture.superconducting.custom_gates."
       "flux_tunable_transmon_pair.two_qubit_gates.CZGate")
_PAIR = ("quam_builder.architecture.superconducting.qubit_pair."
         "flux_tunable_transmon_pair.FluxTunableTransmonPair")
_SQ = "quam.components.pulses.SquarePulse"


def _native(p: Path) -> str:
    """/mnt/c/... → C:/... (a Windows interpreter can't open WSL paths)."""
    s = str(p)
    if s.startswith("/mnt/") and len(s) > 6:
        return s[5].upper() + ":" + s[6:]
    return s


def _probe(python_exe: Path, tmp_path: Path, classes: list[str]) -> dict:
    (tmp_path / "cls.json").write_text(
        json.dumps({"classes": classes, "pulse_roster": True}), encoding="utf-8")
    out = tmp_path / "out.json"
    # a Windows interpreter needs Windows-dialect paths; the scratch dir must
    # therefore live on a drive-visible path — use the repo-local .tmp dir
    r = subprocess.run(
        [str(python_exe), _native(_SCRIPT),
         "--classes", _native(tmp_path / "cls.json"), "--out", _native(out)],
        capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(out.read_text(encoding="utf-8"))


def _scratch(name: str) -> Path:
    d = Path(__file__).resolve().parent.parent / f".tmp_schema_live_{name}"
    d.mkdir(exist_ok=True)
    return d


def _any_rate(manifest: dict) -> tuple[int, int]:
    total = bad = 0
    for entry in manifest["classes"].values():
        for f in (entry.get("fields") or {}).values():
            total += 1
            if f["type"]["base"] == "any":
                bad += 1
    return bad, total


@pytest.mark.skipif(not _ENVS["modern"].exists(), reason="QSM_ENV_MODERN not set")
class TestModernStack:
    @pytest.fixture(scope="class")
    def manifest(self):
        return _probe(_ENVS["modern"], _scratch("modern"), [_CZ, _PAIR, _SQ])

    def test_versions(self, manifest):
        assert manifest["versions"]["quam"] == "0.6.0"
        assert manifest["versions"]["quam_builder"] == "0.4.0"

    def test_czgate_generation_fields(self, manifest):
        fields = manifest["classes"][_CZ]["fields"]
        assert "duration_qubit" in fields
        assert "moving_qubit" not in fields
        assert "duration_control" not in fields
        # ScalarInt collapses to int (QUA runtime arms dropped)
        assert fields["duration_qubit"]["type"]["base"] == "int"

    def test_pair_literal_enum(self, manifest):
        f = manifest["classes"][_PAIR]["fields"]["moving_qubit"]
        assert f["type"]["enum"] == ["control", "target"]

    def test_roster_generation(self, manifest):
        roster = manifest["pulse_roster"]
        assert "ErfSquarePulse" not in roster
        snz = roster["SNZPulse"]
        assert "quam_builder.architecture.superconducting.components.pulses" in snz["homes"]
        assert "padding" in (snz["fields"] or {})

    def test_field_coverage(self, manifest):
        bad, total = _any_rate(manifest)
        assert total > 0 and bad / total <= 0.10, f"any-rate {bad}/{total}"


@pytest.mark.skipif(not _ENVS["fork"].exists(), reason="QSM_ENV_FORK not set")
class TestForkStack:
    @pytest.fixture(scope="class")
    def manifest(self):
        return _probe(_ENVS["fork"], _scratch("fork"), [_CZ, _PAIR, _SQ])

    def test_versions(self, manifest):
        assert manifest["versions"]["quam"] == "0.5.0a3"
        assert manifest["versions"]["quam_builder"] == "0.2.0"

    def test_czgate_generation_fields(self, manifest):
        fields = manifest["classes"][_CZ]["fields"]
        assert "duration_control" in fields
        assert "moving_qubit" in fields
        assert "duration_qubit" not in fields

    def test_roster_generation(self, manifest):
        roster = manifest["pulse_roster"]
        erf = roster.get("ErfSquarePulse")
        assert erf is not None
        assert "quam.components.pulses" in erf["homes"]
        assert "post_zero_padding_length" in (erf["fields"] or {})

    def test_field_coverage(self, manifest):
        bad, total = _any_rate(manifest)
        assert total > 0 and bad / total <= 0.10, f"any-rate {bad}/{total}"
