"""docs/141 4ak — a declared pair that produced nothing must say so.

A qubit pair materialises from a pair LINE (``coupler`` / ``cross_resonance`` /
``zz_drive``, each of which allocates a channel) or from the ``cz_fixed`` pair
gate, which creates it with ``coupler: None`` and seeds the CZ macros on the
moving qubit's own flux — the shape a chip with no tunable couplers has.

A spec that declares ``qubit_pairs`` and gives neither built ZERO pairs and
reported a clean success. Found reproducing a real 17Q chip: 16 declared pairs,
none built, no warning, and the gap only shows up when someone runs a two-qubit
node months later. The vocabulary was never the problem — `cz_fixed` expresses
exactly this — so the fix is to name the silence, not to add a feature.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_GEN = Path(__file__).resolve().parent.parent / "quam_state_manager" / "generator"
sys.path.insert(0, str(_GEN))

run_build = pytest.importorskip("run_build")


class _Machine(SimpleNamespace):
    pass


def _machine(*pair_ids: str) -> _Machine:
    return _Machine(qubit_pairs={p: object() for p in pair_ids})


class TestDeclaredPairsNotBuilt:
    def test_every_declared_pair_built_reports_nothing(self):
        spec = {"qubit_pairs": [["q1", "q2"], ["q3", "q4"]]}
        assert run_build._declared_pairs_not_built(spec, _machine("q1-2", "q3-4")) == []

    def test_a_pair_that_produced_nothing_is_named(self):
        spec = {"qubit_pairs": [["q1", "q2"], ["q3", "q4"]]}
        assert run_build._declared_pairs_not_built(spec, _machine("q1-2")) == ["q3-4"]

    def test_the_17q_case_all_sixteen(self):
        """The real shape that went unreported: pairs declared, no pair line,
        no pair gate -> not one of them exists."""
        ids = ["q1-2", "q3-4", "q4-5", "q5-6", "q7-8", "q8-9", "q4-9", "q8-3",
               "q3-1", "q2-4", "q9-10", "q10-11", "q15-10", "q17-15", "q5-10", "q6-11"]
        spec = {"qubit_pairs": [[p.split("-")[0], "q" + p.split("-")[1]] for p in ids]}
        assert run_build._declared_pairs_not_built(spec, _machine()) == sorted(ids)

    def test_a_machine_with_no_pairs_attribute_is_not_a_crash(self):
        spec = {"qubit_pairs": [["q1", "q2"]]}
        assert run_build._declared_pairs_not_built(spec, SimpleNamespace()) == ["q1-2"]

    def test_declaring_nothing_reports_nothing(self):
        assert run_build._declared_pairs_not_built({}, _machine("q1-2")) == []
        assert run_build._declared_pairs_not_built({"qubit_pairs": []}, _machine()) == []

    def test_an_unparseable_declaration_is_skipped_not_guessed(self):
        spec = {"qubit_pairs": [["q1", "q2"], "not-a-pair", None, []]}
        assert run_build._declared_pairs_not_built(spec, _machine()) == ["q1-2"]

    def test_the_names_are_quam_ids_so_they_match_what_the_chip_shows(self):
        """`_norm_pair_qubits` is the one place the id spelling is decided;
        reporting anything else would send the reader looking for a pair that
        is not spelled that way on the chip."""
        spec = {"qubit_pairs": [["q15", "q10"]]}
        assert run_build._declared_pairs_not_built(spec, _machine()) == ["q15-10"]


class TestItIsWiredIntoTheResult:
    def test_the_result_carries_the_field(self):
        src = (_GEN / "run_build.py").read_text(encoding="utf-8")
        assert '"pairs_declared_not_built": _declared_pairs_not_built(spec, machine),' in src

    def test_the_warning_is_actually_raised_when_there_are_unbuilt_pairs(self):
        """A message in the source proves nothing if nothing reaches it: the
        guard has to be the unbuilt list, and the append has to follow it."""
        src = (_GEN / "run_build.py").read_text(encoding="utf-8")
        i = src.index("_unbuilt = _declared_pairs_not_built(")
        block = src[i:i + 900]
        assert "if _unbuilt:" in block, "the warning must be guarded by the list itself"
        assert block.index("if _unbuilt:") < block.index("warnings.append("),             "the append must be inside that guard"

    def test_the_warning_names_BOTH_ways_out(self):
        """Naming only the lines would send a chip with no couplers down a path
        that costs a DC channel per pair it does not have; naming only the gate
        would hide the ordinary case. The reader needs both, and needs to know
        the gate's pair has no coupler."""
        src = (_GEN / "run_build.py").read_text(encoding="utf-8")
        i = src.index("declared qubit pair(s) were not built")
        msg = src[i:i + 500]
        for phrase in ("coupler / cross_resonance / ", "allocates a channel",
                       "cz_fixed", "no coupler", "moving qubit"):
            assert phrase in msg, f"the warning stopped saying {phrase!r}"

    def test_it_runs_before_the_save(self):
        """The warning is assembled into the same result the save reports, so
        it must be computed while the machine still exists."""
        src = (_GEN / "run_build.py").read_text(encoding="utf-8")
        assert src.index("_unbuilt = _declared_pairs_not_built(") < src.index("        machine.save()")
