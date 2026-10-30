"""P2 fixes for the lab-additions feature: honest reasons, wiring, round trips."""
from __future__ import annotations

from quam_state_manager.core import additions

SPEC = {"qubits": ["q1", "q2"], "qubit_pairs": [["q1", "q2"]], "populate": {},
        "lines": [{"line": "drive", "element": "q1"}, {"line": "drive", "element": "q2"}]}
PULSE = {"kind": "pulse", "target": "qubits.q1.xy.operations", "name": "x_lab",
         "class": "labpkg.pulses.LabPulse", "fields": {}}


def test_unprobed_class_is_not_called_absent():
    man = {"classes": {"other.Cls": {"role": "pulse", "fields": {}}}, "missing_classes": []}
    assert additions.manifest_reason(PULSE, man) is None
    res = additions.lint_proposal(SPEC, PULSE, man)
    assert not any("not in this environment" in e for e in res["errors"]), res
    assert any("not been probed" in w for w in res["warnings"]), res


def test_no_manifest_says_not_probed():
    res = additions.lint_proposal(SPEC, PULSE, None)
    assert res["ok"], res
    assert any("not been probed" in w for w in res["warnings"]), res
    assert not any("not in this environment" in e for e in res["errors"])


def test_a_class_the_probe_could_not_find_is_absent():
    man = {"classes": {"other.Cls": {"role": "pulse", "fields": {}}},
           "missing_classes": ["labpkg.pulses.LabPulse"]}
    assert "not in this environment" in additions.manifest_reason(PULSE, man)
