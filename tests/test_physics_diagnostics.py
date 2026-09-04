"""The Physics domain in Diagnostics — checks that need TWO numbers at once.

Every other physics check in the app judges one value alone
(``chip_health.physicality(key, value)`` is literally that signature), so
``T2 <= 2*T1`` and a frequency collision had nowhere to live. These pins cover
the two rules that landed and, just as importantly, the cases where the linter
must stay QUIET — a check that fires on a healthy chip teaches people to ignore
the page.

The fixtures deliberately carry the shapes a real chip has, not the shapes that
would be convenient: ``operations.x180`` is a ``#./x180_DragCosine`` ALIAS (a
fixture with the length inline cannot fail when the pointer walk breaks — the
docs/166 lesson), and a pair's control/target are ``#/qubits/qX`` pointers.
"""

from __future__ import annotations

from quam_state_manager.core import diagnostics
from quam_state_manager.core.loader import QuamStore


# ---------------------------------------------------------------------------
# Fixtures — two qubits, coupled, with an aliased x180 like a real chip
# ---------------------------------------------------------------------------

def _pulse(length: float = 40) -> dict:
    return {"length": length, "amplitude": 0.2, "axis_angle": 0,
            "__class__": "quam.components.pulses.DragCosinePulse"}


def _state(*, f1=5.000e9, f2=5.200e9, t1=1.0e-5, t2r=1.5e-5, t2e=1.6e-5,
           length1=40, length2=40, coupled=True, alias=True,
           shared_port=False) -> dict:
    """Two qubits. ``alias`` keeps the real chip's ``#./`` operation indirection.

    ``xy.opx_output`` lives HERE rather than in the wiring dict because that is
    where a real chip carries it (measured on the reference 20Q chip, whose
    ``qubits.q1.xy`` holds ``opx_output`` directly) — and because the merge is
    not deep: a state-side ``xy`` shadows the wiring-side one entirely.
    """
    def ops(length):
        if alias:
            # The shape a modern quam_builder chip actually writes: the named
            # gate is a self-ref pointer at the concrete pulse beside it.
            return {"x180_DragCosine": _pulse(length), "x180": "#./x180_DragCosine"}
        return {"x180": _pulse(length)}

    q2_port = "#/ports/mw_outputs/con1/1/3" if shared_port else "#/ports/mw_outputs/con1/1/4"
    state = {
        "qubits": {
            "q1": {"id": "q1", "f_01": f1, "T1": t1, "T2ramsey": t2r,
                   "T2echo": t2e,
                   "xy": {"operations": ops(length1),
                          "opx_output": "#/ports/mw_outputs/con1/1/3"}},
            "q2": {"id": "q2", "f_01": f2, "T1": 1.2e-5, "T2ramsey": 1.0e-5,
                   "T2echo": 1.1e-5,
                   "xy": {"operations": ops(length2), "opx_output": q2_port}},
        },
        "qubit_pairs": {},
        "ports": {"mw_outputs": {"con1": {"1": {
            "1": {"upconverter_frequency": 6.0e9},
            "3": {"upconverter_frequency": 6.0e9},
            "4": {"upconverter_frequency": 6.0e9},
        }}}},
    }
    if coupled:
        state["qubit_pairs"] = {"q1-2": {
            "id": "q1-2",
            "qubit_control": "#/qubits/q1",
            "qubit_target": "#/qubits/q2",
        }}
    return state


def _wiring() -> dict:
    return {"wiring": {"qubits": {"q1": {}, "q2": {}}}}


def _findings(state=None, wiring=None):
    store = QuamStore.from_dicts(state or _state(), wiring or _wiring())
    return diagnostics._relational_findings(store.merged)


def _cats(findings) -> list[str]:
    return [f.category for f in findings]


# ---------------------------------------------------------------------------
# Tier A — T2 <= 2*T1
# ---------------------------------------------------------------------------

class TestCoherenceBound:
    def test_a_t2_above_twice_t1_is_reported_with_its_margin(self):
        # 2*T1 = 20 µs; T2ramsey = 30 µs is 50% over.
        got = [f for f in _findings(_state(t1=1.0e-5, t2r=3.0e-5))
               if f.category == "physics_coherence_bound"]
        assert len(got) == 1
        f = got[0]
        assert f.severity == "warning"
        assert "50.0%" in f.message, f.message
        # the margin, not a bare verdict — both numbers are named
        assert "30" in f.message and "20" in f.message
        assert f.jump_path == "qubits.q1.T2ramsey"
        assert diagnostics.domain_of(f.category) == "physics"

    def test_a_small_excess_is_info_not_warning(self):
        # 4.6% over — the real chip's only violation, inside fit uncertainty.
        got = [f for f in _findings(_state(t1=1.999e-5, t2r=4.183e-5))
               if f.category == "physics_coherence_bound"]
        assert len(got) == 1
        assert got[0].severity == "info"
        assert "fit uncertainty" in got[0].detail

    def test_exactly_at_the_bound_is_not_a_violation(self):
        # T2 == 2*T1 is the physical best case, not a defect. A mutation from
        # `t2 <= bound` to `t2 < bound` turns this red.
        got = [f for f in _findings(_state(t1=1.0e-5, t2r=2.0e-5, t2e=2.0e-5))
               if f.category == "physics_coherence_bound"]
        assert got == []

    def test_both_t2_flavours_are_checked(self):
        got = [f for f in _findings(_state(t1=1.0e-5, t2r=3.0e-5, t2e=4.0e-5))
               if f.category == "physics_coherence_bound"]
        assert {f.jump_path for f in got} == {"qubits.q1.T2ramsey", "qubits.q1.T2echo"}

    def test_it_reports_without_convicting_either_value(self):
        """SM must not decide which fit broke — Chip Status keeps both values."""
        from quam_state_manager.core import chip_health
        state = _state(t1=1.0e-5, t2r=3.0e-5)
        got = [f for f in _findings(state) if f.category == "physics_coherence_bound"]
        assert got, "precondition: the violation is found"
        # neither leaf is marked unphysical, so neither leaves any average
        assert chip_health.physicality("T1", 1.0e-5) is True
        assert chip_health.physicality("T2ramsey", 3.0e-5) is True
        # and the linter is pure — it did not rewrite the state it read
        assert state["qubits"]["q1"]["T2ramsey"] == 3.0e-5
        assert "does not know WHICH" in got[0].detail

    def test_missing_or_unusable_coherence_values_are_silent(self):
        for kw in ({"t1": None}, {"t1": 0.0}, {"t1": -1.0e-5}, {"t2r": None},
                   {"t2r": "30us"}, {"t2r": 0.0}, {"t1": "10us"}):
            base = {"t1": 1.0e-5, "t2r": 1.5e-5, "t2e": 1.6e-5}
            base.update(kw)
            got = [f for f in _findings(_state(**base))
                   if f.category == "physics_coherence_bound"]
            assert got == [], f"{kw} must be silent, got {[g.message for g in got]}"


# ---------------------------------------------------------------------------
# Tier B — addressability, and the mechanism gate that keeps it quiet
# ---------------------------------------------------------------------------

class TestAddressability:
    def test_a_coupled_pair_too_close_in_frequency_is_reported(self):
        # 5 MHz apart with a 40 ns x180 → Δ·T = 0.2
        got = [f for f in _findings(_state(f1=5.000e9, f2=5.005e9))
               if f.category == "physics_addressability"]
        assert len(got) == 1
        f = got[0]
        assert f.severity == "warning"
        assert "0.200" in f.message, f.message
        assert "coupled as q1-2" in f.message
        assert f.jump_path == "qubits.q1.f_01"

    def test_a_well_separated_coupled_pair_is_silent(self):
        # 200 MHz apart → Δ·T = 8. A mutation of the comparison direction
        # (>= to <) turns this red.
        got = [f for f in _findings(_state(f1=5.000e9, f2=5.200e9))
               if f.category == "physics_addressability"]
        assert got == []

    def test_the_boundary_at_delta_t_equals_one_is_not_a_finding(self):
        # 25 MHz with a 40 ns pulse is exactly Δ·T = 1.
        got = [f for f in _findings(_state(f1=5.000e9, f2=5.025e9))
               if f.category == "physics_addressability"]
        assert got == []

    def test_two_qubits_with_no_mechanism_are_never_compared(self):
        """The pin that keeps this check usable.

        On the reference 20-qubit chip 14 of 19 frequency-adjacent pairs sit
        inside Δ·T < 1 while all 30 declared couplings are clear: reusing a
        frequency between qubits that cannot reach each other is deliberate
        design, and flagging it would have made a healthy chip look broken.
        Deleting the mechanism gate turns this red.
        """
        got = [f for f in _findings(_state(f1=5.000e9, f2=5.001e9, coupled=False))
               if f.category == "physics_addressability"]
        assert got == [], [g.message for g in got]

    def test_sharing_one_xy_output_port_is_a_mechanism(self):
        got = [f for f in _findings(
            _state(f1=5.000e9, f2=5.001e9, coupled=False, shared_port=True))
            if f.category == "physics_addressability"]
        assert len(got) == 1
        assert "one xy output port" in got[0].message

    def test_the_scale_is_the_chip_s_own_pulse_length(self):
        """ONE spacing, two pulse lengths, opposite verdicts.

        This is what makes the threshold non-invented: nothing in the check
        knows about MHz. A longer pulse is spectrally NARROWER, so the same
        20 MHz gap that a 4 ns pulse cannot resolve is comfortable for a 40 ns
        one. A mutation hard-coding a Hz constant turns this red because both
        halves would then agree.
        """
        gap = dict(f1=5.0e9, f2=5.030e9)          # 30 MHz apart, fixed
        short = [f for f in _findings(_state(length1=4, length2=4, **gap))
                 if f.category == "physics_addressability"]
        long_ = [f for f in _findings(_state(length1=40, length2=40, **gap))
                 if f.category == "physics_addressability"]
        assert len(short) == 1, "a 4 ns pulse is ~250 MHz wide — 30 MHz is inside it"
        assert "4 ns x180" in short[0].message
        assert long_ == [], "a 40 ns pulse is ~25 MHz wide — 30 MHz clears Δ·T = 1"

    def test_the_shorter_of_the_two_pulses_decides(self):
        """A pair is only as selective as its blunter pulse."""
        got = [f for f in _findings(
            _state(f1=5.0e9, f2=5.030e9, length1=4, length2=400))
            if f.category == "physics_addressability"]
        assert len(got) == 1 and "4 ns x180" in got[0].message

    def test_it_crosses_the_operation_alias_pointer(self):
        """A real chip stores ``x180`` as ``#./x180_DragCosine``.

        With the alias, the length is two hops away and the store's own
        ``resolve_value`` raises on it. Both fixtures must produce the SAME
        finding — if the alias form goes silent, the check is dead on every
        real chip while still passing an inline fixture.
        """
        aliased = [f for f in _findings(_state(f1=5.0e9, f2=5.005e9, alias=True))
                   if f.category == "physics_addressability"]
        inline = [f for f in _findings(_state(f1=5.0e9, f2=5.005e9, alias=False))
                  if f.category == "physics_addressability"]
        assert len(aliased) == 1 and len(inline) == 1
        assert aliased[0].message == inline[0].message

    def test_an_unreadable_pulse_length_is_silent_not_guessed(self):
        """No x180 at all, and a dangling alias. Both must produce nothing —
        a default length would invent the threshold this check exists to derive.
        """
        gone = _state(f1=5.0e9, f2=5.001e9)
        for q in ("q1", "q2"):
            gone["qubits"][q]["xy"]["operations"] = {}
        assert [f for f in _findings(gone) if f.category == "physics_addressability"] == []

        dangling = _state(f1=5.0e9, f2=5.001e9)
        for q in ("q1", "q2"):
            dangling["qubits"][q]["xy"]["operations"] = {"x180": "#./nope"}
        assert [f for f in _findings(dangling)
                if f.category == "physics_addressability"] == []

    def test_a_pair_is_reported_once_not_twice(self):
        # the same two qubits both coupled AND on one port
        got = [f for f in _findings(_state(f1=5.0e9, f2=5.001e9, shared_port=True))
               if f.category == "physics_addressability"]
        assert len(got) == 1
        assert "coupled as q1-2" in got[0].message, "a declared coupling names itself"


# ---------------------------------------------------------------------------
# Wiring into the page
# ---------------------------------------------------------------------------

class TestDomainRegistration:
    def test_the_physics_domain_exists_and_is_named(self):
        assert ("physics", "Physics") in diagnostics.DIAG_DOMAINS

    def test_both_categories_route_to_it(self):
        assert diagnostics.domain_of("physics_coherence_bound") == "physics"
        assert diagnostics.domain_of("physics_addressability") == "physics"

    def test_the_catalogue_documents_both_checks(self):
        """``_CHECK_CATALOG``'s own comment calls itself the single source of
        truth for documented coverage — an undocumented check is a check nobody
        knows ran."""
        entry = dict(diagnostics._CHECK_CATALOG).get("physics")
        assert entry and len(entry) == 2
        text = " ".join(t + " " + d for _, t, d in entry)
        assert "2·T1" in text
        # the honest labelling the red-team round required: the SCALE is the
        # chip's, the CUT is ours, and the catalogue must not blur them
        assert "SM's own guideline" in text

    def test_the_pass_is_registered_in_the_lint_chain(self):
        state = _state(t1=1.0e-5, t2r=3.0e-5)
        store = QuamStore.from_dicts(state, _wiring())
        cats = _cats(diagnostics.lint_state(store))
        assert "physics_coherence_bound" in cats

    def test_a_chip_with_no_qubits_does_not_raise(self):
        store = QuamStore.from_dicts({"qubits": {}, "ports": {}}, {"wiring": {}})
        assert diagnostics._relational_findings(store.merged) == []
        store = QuamStore.from_dicts({"ports": {}}, {"wiring": {}})
        assert diagnostics._relational_findings(store.merged) == []
