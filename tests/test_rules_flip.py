"""X1: a CZ orientation flip keeps a pair's additions and rules.

Drives ``tests/rules_flip_selfcheck.cjs`` (the shipped generate.js flips two
pairs from a Populate frequency edit) and then judges the flipped spec with
the server's own rule expansion: the hand-made source gate and the rule's
instance are still on their pairs, both read "Needs a look" with the role
swap named, and the rule counts both pairs.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.core import addition_rules, gate_action

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "rules_flip_selfcheck.cjs"


def _flipped_spec():
    if shutil.which("node") is None:
        pytest.skip("node not on PATH")
    r = subprocess.run(["node", str(_SELFCHECK)], capture_output=True, text=True,
                       encoding="utf-8", cwd=str(_ROOT))
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr
    line = next(ln for ln in r.stdout.splitlines() if ln.startswith("SPEC_JSON "))
    return json.loads(line[len("SPEC_JSON "):])


def test_a_flip_keeps_the_gates_and_marks_them_for_a_look():
    spec = _flipped_spec()
    rule = spec["addition_rules"][0]
    plan = addition_rules.expand(spec, rule)
    rows = {r["pair"]: r for r in plan["pairs"]}
    assert set(rows) == {"q2-1", "q3-2"}
    for pid in ("q2-1", "q3-2"):
        row = rows[pid]
        assert row["existing"], (pid, row)
        assert row["status"] == "Needs a look", (pid, row["status"], row["reason"])
        assert row["reason"] == "control and target swapped by Populate frequency", row["reason"]
    # The hand gate is the source: still found through the rule's (followed) source.
    assert gate_action.pair_members(spec, rule["source"]) == ("q2", "q1")


def test_an_old_pair_id_still_names_the_pair_by_membership():
    spec = {"qubit_pairs": [["q2", "q1"]]}
    assert gate_action.pair_members(spec, "q1-2") == ("q2", "q1")
    assert gate_action.pair_members(spec, "q1-q2") == ("q2", "q1")


def test_an_unflipped_instance_reads_already_has_it():
    spec = _flipped_spec()
    rule = spec["addition_rules"][0]
    # The review records the recalculated signatures: after that no look is asked.
    plan = addition_rules.expand(spec, rule)
    rule["reviewed"] = {r["key"]: r["instance_sig"] for r in plan["pairs"]}
    again = addition_rules.expand(spec, rule)
    assert all(r["status"] == "Already has it" for r in again["pairs"]), [r["status"] for r in again["pairs"]]
