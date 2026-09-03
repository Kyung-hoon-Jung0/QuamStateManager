"""docs/162 — a value that cannot be what its field names.

A real donor chip in this archive carries ``IRB = 1.5345``. That is a fidelity
above 1, and it made the Chip Status Overview report the chip's 2Q gate
fidelity as **107.47%** — a claim SM was making, not the lab.

The rule this pins, decided with the user:

  * SM may refuse to AGGREGATE a value it can prove is outside a DEFINITIONAL
    domain, but never hides it, never rewrites it, and always says how many it
    set aside. (Storage stays untouched: CLAUDE.md's Type Coercion Philosophy
    says ranges are never validated on write — real data has coupler
    amplitudes >1 and negative T2, and researcher input is trusted.)
  * "definitional" only. A fidelity lives in ``(0, 1]`` — zero EXCLUSIVE,
    because a fit returning exactly zero has not converged (user-directed) —
    and a readout confusion matrix is row-stochastic. Anharmonicity (sign
    convention differs by lab) and amplitudes (>1 is normal) are deliberately
    NOT checked.
  * The qubit metrics have been gated since docs/141 4o via
    ``chip_health.make_record``. The 2Q PAIR rows never were: they are read
    straight off ``gf.value``, which is how the 1.5345 reached an average.
"""

from __future__ import annotations

import math

import pytest

from quam_state_manager.core import chip_health, diagnostics, query


class TestTheBound:
    """One bound, in one place. `physicality` keys on a metric NAME; a pair's
    rows are named by the lab, so they key on `level` instead — and both must
    reach the same function or the two will drift."""

    @pytest.mark.parametrize("value,ok", [
        (-0.31, False), (0.0, False),          # zero is EXCLUSIVE (user-directed)
        (1e-12, True), (0.5, True), (1.0, True),
        (1.0 + 5e-7, True),                    # float overshoot of an exact 1.0
        (1.0001, False), (1.5345, False),      # the real donor value
        (float("nan"), False), (float("inf"), False),
    ])
    def test_fidelity_domain(self, value, ok):
        assert chip_health.physical_fidelity(value) is ok

    def test_missing_is_not_impossible(self):
        assert chip_health.physical_fidelity(None) is True
        assert chip_health.physical_fidelity("n/a") is True
        assert chip_health.physical_fidelity(True) is True   # a bool is not a fidelity

    def test_physicality_delegates_rather_than_repeating_itself(self):
        for v in (-0.31, 0.0, 1.0, 1.5345):
            assert (chip_health.physicality("cz_fidelity", v)
                    is chip_health.physical_fidelity(v))


def _row(metric, value, **extra):
    return dict(gate="cz_unipolar", metric=metric, value=value,
                level=query._rb_level(metric), **extra)


class TestThePairRowGate:
    def test_an_impossible_value_loses_its_value_and_keeps_its_number(self):
        r = query._gate_fidelity_row(_row("InterleavedRB", 1.5345))
        # losing `value` is the safety property: every reader tests
        # `typeof gf.value === 'number'`, so none can use it by accident
        assert "value" not in r
        assert r["raw_value"] == 1.5345
        assert r["physical"] is False

    @pytest.mark.parametrize("metric,value", [
        ("InterleavedRB", 1.5345), ("InterleavedRB", 0.0),
        ("StandardRB", -0.31), ("IRB", 2.7),
        ("StandardRB_alpha", 1.22),            # a decay base shares the domain
        ("Bell_State", 1.4),
    ])
    def test_every_level_is_gated(self, metric, value):
        r = query._gate_fidelity_row(_row(metric, value))
        assert r.get("physical") is False and "value" not in r

    @pytest.mark.parametrize("value", [1e-4, 0.5, 1.0])
    def test_a_possible_value_is_untouched(self, value):
        r = query._gate_fidelity_row(_row("InterleavedRB", value))
        assert r["value"] == value and "physical" not in r and "raw_value" not in r

    def test_an_unclassified_metric_is_left_alone(self):
        """Inventing a domain for a metric nobody has classified is how a
        plausibility band gets invented, which docs/78 refuses without
        evidence. `error_per_gate` is an error, not a fidelity."""
        r = query._gate_fidelity_row(_row("error_per_gate", 41.0))
        assert r["value"] == 41.0 and "physical" not in r

    def test_the_nested_spellings_are_gated_too(self):
        r = query._gate_fidelity_row(
            _row("Bell_State", 0.9, Fidelity=1.9, Purity=0.98))
        assert r.get("physical") is False
        assert r["raw_Fidelity"] == 1.9
        assert r["Purity"] == 0.98              # not a fidelity field — untouched

    def test_it_runs_on_the_real_extraction_path(self):
        macros = {"cz_unipolar": {"fidelity": {
            "StandardRB": 0.97, "InterleavedRB": 1.5345, "StandardRB_load_id": 22}}}
        rows = {r["metric"]: r for r in query._extract_pair_gate_fidelities(macros)}
        assert rows["StandardRB"]["value"] == 0.97
        assert "value" not in rows["InterleavedRB"]
        assert rows["InterleavedRB"]["raw_value"] == 1.5345
        assert "StandardRB_load_id" not in rows      # provenance, never a row


class TestConfusionMatrices:
    """Decision 2: the row-sum check counts as definitional. It already
    existed (`_valid_confusion_matrix`); what was missing was SAYING so."""

    GOOD = [[0.95, 0.03, 0.02], [0.04, 0.92, 0.04], [0.02, 0.08, 0.90]]

    def test_a_row_stochastic_matrix_derives_a_fidelity(self):
        assert query._valid_confusion_matrix(self.GOOD)
        assert query._assignment_fidelity_n(self.GOOD) == pytest.approx(
            (0.95 + 0.92 + 0.90) / 3)

    @pytest.mark.parametrize("cm", [
        [[0.9, 0.4, 0.3], [0.1, 0.8, 0.2], [0.0, 0.1, 0.9]],      # rows 1.6/1.1/1.0
        [[0.5, -0.1, 0.6], [0.1, 0.8, 0.1], [0.0, 0.1, 0.9]],     # negative entry
        [[float("nan"), 0.0, 1.0], [0.1, 0.8, 0.1], [0.0, 0.1, 0.9]],
    ])
    def test_a_matrix_that_is_not_a_distribution_derives_nothing(self, cm):
        assert not query._valid_confusion_matrix(cm)
        assert query._assignment_fidelity_n(cm) is None

    def test_rounding_is_tolerated(self):
        cm = [[0.949, 0.03, 0.02], [0.04, 0.92, 0.04], [0.02, 0.08, 0.90]]
        assert abs(sum(cm[0]) - 1.0) > 1e-6 and query._valid_confusion_matrix(cm)


class TestTheAlarm:
    """The count on a tile says HOW MANY; Diagnostics says WHERE. An exclusion
    the user cannot trace is the silently-smaller-N failure docs/94 fixed."""

    @staticmethod
    def _chip():
        return {
            "qubits": {
                "q1": {"id": "q1", "resonator": {"gef_confusion_matrix":
                       [[0.9, 0.4, 0.3], [0.1, 0.8, 0.2], [0.0, 0.1, 0.9]]}},
                "q2": {"id": "q2", "resonator": {"gef_confusion_matrix":
                       TestConfusionMatrices.GOOD}},
            },
            "qubit_pairs": {
                "q1-2": {"macros": {"cz_unipolar": {"fidelity": {
                    "InterleavedRB": 1.5345, "StandardRB": 0.97}}}},
                "q2-3": {"macros": {"cz_flattop": {"fidelity": {
                    "StandardRB": 0.0, "StandardRB_alpha": 0.88}}}},
            },
        }

    def test_it_names_the_path_of_every_impossible_value(self):
        f = diagnostics._unphysical_findings(self._chip())
        locs = {x.location for x in f}
        assert locs == {
            "qubits.q1.resonator.gef_confusion_matrix",
            "qubit_pairs.q1-2.macros.cz_unipolar.fidelity.InterleavedRB",
            "qubit_pairs.q2-3.macros.cz_flattop.fidelity.StandardRB",
        }
        assert all(x.severity == "warning" for x in f)

    def test_it_says_what_is_wrong_in_the_message(self):
        f = {x.location: x for x in diagnostics._unphysical_findings(self._chip())}
        assert "1.5345" in f["qubit_pairs.q1-2.macros.cz_unipolar.fidelity.InterleavedRB"].message
        assert "(0, 1]" in f["qubit_pairs.q1-2.macros.cz_unipolar.fidelity.InterleavedRB"].message
        assert "row-stochastic" in f["qubits.q1.resonator.gef_confusion_matrix"].message

    def test_a_clean_chip_raises_nothing(self):
        chip = self._chip()
        chip["qubits"]["q1"]["resonator"]["gef_confusion_matrix"] = \
            TestConfusionMatrices.GOOD
        chip["qubit_pairs"]["q1-2"]["macros"]["cz_unipolar"]["fidelity"]["InterleavedRB"] = 0.98
        chip["qubit_pairs"]["q2-3"]["macros"]["cz_flattop"]["fidelity"]["StandardRB"] = 0.95
        assert diagnostics._unphysical_findings(chip) == []

    def test_the_conventions_it_must_never_police(self):
        """Anharmonicity's sign and an amplitude above 1 are lab conventions,
        not definitions — CLAUDE.md says to trust researcher input."""
        chip = {"qubits": {"q1": {"anharmonicity": -250e6,
                                  "xy": {"operations": {"x180": {"amplitude": 1.8}}}}},
                "qubit_pairs": {}}
        assert diagnostics._unphysical_findings(chip) == []

    def test_it_is_a_values_check(self):
        assert diagnostics.domain_of("value_unphysical") == "values"

    def test_it_is_listed_in_what_this_page_checks(self):
        blob = repr(diagnostics.check_catalog()
                    if hasattr(diagnostics, "check_catalog") else diagnostics._CHECK_CATALOG)
        assert "(0, 1]" in blob


class TestARefusedSourceIsNotAMissingOne:
    """A confusion matrix that exists but is not row-stochastic derives no
    number — the value is None either way — but a chip that never measured one
    and a chip whose matrix is broken are different chips. Reading both as
    `missing` made the second one's tile count drop with nothing to explain it
    (measured: Readout Fidelity (GEF) went 20 -> 19 in silence)."""

    BAD = [[0.9, 0.4, 0.3], [0.1, 0.8, 0.2], [0.0, 0.1, 0.9]]

    def test_a_refused_source_is_marked_unphysical(self):
        r = chip_health.make_record("assignment_fidelity_gef", None,
                                    source_rejected=True)
        assert r["value"] is None and r["physical"] is False
        assert r["unresolved"] is False and r["raw"] is None

    def test_a_genuinely_absent_one_still_reads_as_missing(self):
        r = chip_health.make_record("assignment_fidelity_gef", None)
        assert r["value"] is None and r["physical"] is True

    def test_a_dangling_pointer_is_neither(self):
        r = chip_health.make_record("T1", None, unresolved=True)
        assert r["unresolved"] is True and r["physical"] is True

    def test_the_engine_marks_every_metric_the_matrix_feeds(self):
        rej = query._rejected_metric_keys(
            {"gef_confusion_matrix": self.BAD,
             "confusion_matrix": TestConfusionMatrices.GOOD})
        assert rej == {"assignment_fidelity_gef", "ro_fidelity_gef_g",
                       "ro_fidelity_gef_e", "ro_fidelity_gef_f"}

    def test_a_good_matrix_marks_nothing(self):
        assert query._rejected_metric_keys(
            {"gef_confusion_matrix": TestConfusionMatrices.GOOD,
             "confusion_matrix": TestConfusionMatrices.GOOD}) == set()

    def test_an_absent_matrix_marks_nothing(self):
        """Absent is not refused — the whole point of the distinction."""
        assert query._rejected_metric_keys({}) == set()
        assert query._rejected_metric_keys({"gef_confusion_matrix": None}) == set()
        assert query._rejected_metric_keys({"gef_confusion_matrix": []}) == set()
