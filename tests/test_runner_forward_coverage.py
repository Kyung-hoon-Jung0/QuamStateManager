"""The forward write must be complete, or say that it is not (docs/78 §23).

Replaying the two vs_power families against the archive showed the forward
path — the one the engine uses when the node wrote nothing itself — computed
two of the six fields node 08b writes, and two of three for node 05.

Two of the four missing ones ARE the node's own numbers and were added:
`anharmonicity_fitted` matched the written `anharmonicity` in 36 of 36
archived writes, and `bare_resonator_frequency` matched `frequency_bare` in
21 of 21. The rest are NOT in the fit output at all (0 of 23) or match only
sometimes (10 of 38), so closing them would mean reverse-engineering the
node's formula, which D-14 forbids.

What is left is therefore declared, not hidden: writing part of what the node
writes and saying nothing is the quiet partial r12 exists to prevent.
"""
from __future__ import annotations

from collections import deque

import pytest

from quam_state_manager.core.autofit import families as fam_mod
from quam_state_manager.core.autofit import power_rows


def _fam(key):
    f = fam_mod.family_for(key)
    assert f is not None, key
    return f


class TestTheAddedWritesAreTheNodesOwnNumbers:
    def test_08b_carries_the_fitted_anharmonicity(self):
        f = _fam("qubit_spectroscopy_vs_power")
        rows = fam_mod.resolve_updates(
            f, "qA1", {"frequency": 5.0756346e9,
                       "anharmonicity_fitted": 213_000_000.0},
            run_parameters={}, current_value_of=lambda p: None)
        by = {r["path"]: r["value"] for r in rows}
        assert by["qubits.qA1.anharmonicity"] == pytest.approx(213_000_000.0)
        assert by["qubits.qA1.f_01"] == pytest.approx(5.0756346e9)

    def test_05_carries_the_bare_resonator_frequency(self):
        f = _fam("resonator_spectroscopy_vs_power")
        rows = fam_mod.resolve_updates(
            f, "qA1", {"resonator_frequency": 7.431242391e9,
                       "bare_resonator_frequency": 7.428642391e9},
            run_parameters={}, current_value_of=lambda p: None)
        by = {r["path"]: r["value"] for r in rows}
        assert by["qubits.qA1.resonator.frequency_bare"] == \
            pytest.approx(7.428642391e9)

    def test_a_missing_key_still_skips_rather_than_guessing(self):
        """The fit_targets doctrine: a value the run does not report is not
        written at all."""
        f = _fam("qubit_spectroscopy_vs_power")
        rows = fam_mod.resolve_updates(f, "qA1", {"frequency": 5e9},
                                       run_parameters={},
                                       current_value_of=lambda p: None)
        assert not any(r["path"].endswith("anharmonicity") for r in rows)


class TestTheGapsAreDeclaredWithReasons:
    def test_08b_declares_the_three_it_cannot_compute(self):
        gaps = _fam("qubit_spectroscopy_vs_power").forward_gaps
        assert set(gaps) == {
            "qubits.{q}.xy.operations.saturation.amplitude",
            "qubits.{q}.xy.operations.x180_DragCosine.amplitude",
            "qubits.{q}.xy.operations.x90_DragCosine.amplitude"}

    def test_every_declared_gap_says_why(self):
        for key, f in fam_mod.FAMILIES.items():
            for path, why in (getattr(f, "forward_gaps", None) or {}).items():
                assert why and len(why) > 20, (key, path)

    def test_the_coupled_resonator_writes_are_not_declared_as_gaps(self):
        """They are not missing — `power_rows` builds them. Declaring them
        would report a hole that does not exist."""
        f = _fam(power_rows.POWER_COUPLED_FAMILY)
        assert "qubits.{q}.resonator.operations.readout.amplitude" \
            not in (f.forward_gaps or {})

    def test_a_family_with_nothing_missing_declares_nothing(self):
        assert not _fam("power_rabi").forward_gaps


class TestTheEngineNeverWritesAQuietPartial:
    def test_a_forward_write_with_gaps_is_ledgered_and_queued(self):
        from quam_state_manager.core.autofit.engine import PlanEngine

        class E:
            _forward_rows = PlanEngine._forward_rows

            def __init__(self):
                self.events, self.state = [], {"review_queue": []}
                import threading
                self._lock = threading.RLock()

                class W:
                    def current_value_of(self, p):
                        return None
                self.writer = W()

            def _ledger(self, ev, **kw):
                self.events.append((ev, kw))

            def _persist(self):
                pass

        eng = E()
        fam = _fam("qubit_spectroscopy_vs_power")
        rows = eng._forward_rows(fam, "qA1", {
            "fit_results": {"qA1": {"frequency": 5e9,
                                    "anharmonicity_fitted": 2.13e8}},
            "parameters": {}})
        assert rows, "the calibrated frequency is still written"
        ev = [e for e in eng.events if e[0] == "forward_partial"]
        assert ev, "a partial write must be ledgered"
        assert len(ev[0][1]["not_written"]) == 3
        q = eng.state["review_queue"]
        assert q and "left unchanged" in q[0]["reason"]
        # the reason names the fields, resolved for THIS target
        assert "qubits.qA1.xy.operations.x180_DragCosine.amplitude" \
            in q[0]["reason"]

    def test_a_family_without_gaps_queues_nothing(self):
        from quam_state_manager.core.autofit.engine import PlanEngine

        class E:
            _forward_rows = PlanEngine._forward_rows

            def __init__(self):
                self.events, self.state = [], {"review_queue": []}
                import threading
                self._lock = threading.RLock()

                class W:
                    def current_value_of(self, p):
                        return None
                self.writer = W()

            def _ledger(self, ev, **kw):
                self.events.append((ev, kw))

            def _persist(self):
                pass

        eng = E()
        eng._forward_rows(_fam("power_rabi"), "qA1",
                          {"fit_results": {"qA1": {"opt_amp": 0.3}},
                           "parameters": {"operation": "x180"}})
        assert not [e for e in eng.events if e[0] == "forward_partial"]
        assert eng.state["review_queue"] == []
