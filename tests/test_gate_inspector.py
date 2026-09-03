"""Tests for the 2Q gate detuning inspector (docs/162).

The review of PR #5 found the original fixtures had invented a convention no
chip in the corpus uses — ``anharmonicity=-250e6`` (negative) and
``flux_point=0.0`` (a float).  Both are wrong, and inventing them is what let
a broken feature ship with a green suite: the sign test passed against the
code's own mistake, and the two route crashes were never exercised at all.

The fixtures here are the REAL shape: ``anharmonicity`` positive (CQT 20Q
stores 135–229 MHz on all twenty qubits, and the lab's own
``chevron_cz/cz_branch.py`` says "anharmonicity A is stored as a positive
magnitude in this state"), ``flux_point`` a mode STRING, and the idle bias in
``z.joint_offset`` / ``z.independent_offset``.
"""

from __future__ import annotations

import json

import pytest

from quam_state_manager.core import gate_inspector
from quam_state_manager.core.gate_inspector import (
    build_plotly_figure,
    compute_detuning_sweep,
    extract_qubit_params,
    plan_switch_moving_qubit,
    validate_params,
)
from quam_state_manager.web.app import create_app


# ── Fixtures ─────────────────────────────────────────────────────────────

def _q(name, f_01=5e9, anharmonicity=200e6, quad_term=-1e9,
       flux_point="joint", joint_offset=0.05, independent_offset=None):
    """A qubit params dict in the shape ``extract_qubit_params`` returns."""
    p = {
        "name": name,
        "f_01": f_01,
        "anharmonicity": anharmonicity,
        "quad_term": quad_term,
        "flux_point": flux_point,
        "joint_offset": joint_offset,
        "independent_offset": independent_offset,
    }
    if flux_point == "independent" and independent_offset is not None:
        p["idle_voltage"], p["idle_source"] = independent_offset, "z.independent_offset"
    elif joint_offset is not None:
        p["idle_voltage"], p["idle_source"] = joint_offset, "z.joint_offset"
    elif independent_offset is not None:
        p["idle_voltage"], p["idle_source"] = independent_offset, "z.independent_offset"
    else:
        p["idle_voltage"], p["idle_source"] = None, None
    return p


# Control ABOVE target, positive anharmonicities, curvature negative (the
# normal "upper sweet spot" transmon) so the crossings BELOW are reachable.
QC = _q("qA1", f_01=5.2e9, anharmonicity=250e6, quad_term=-1e9, joint_offset=0.05)
QT = _q("qA2", f_01=4.8e9, anharmonicity=230e6, quad_term=-0.8e9, joint_offset=-0.02)


WIRING = {"network": {"host": "1.1.1.1", "cluster_name": "C1"}}


def _chip_state():
    """A chip shaped like the real CQT 20Q pairs: pointer refs, cz macros,
    ``moving_qubit``, and CZ pulses parked on the moving qubit's z line."""
    def qubit(name, f01, anh, quad, joint, ops=None):
        return {
            "id": name,
            "f_01": f01,
            "anharmonicity": anh,
            "freq_vs_flux_01_quad_term": quad,
            "z": {"flux_point": "joint", "joint_offset": joint,
                  "independent_offset": None,
                  **({"operations": ops} if ops is not None else {})},
        }

    cz_ops = {
        "cz_unipolar_pulse": {"__class__": "SquarePulse",
                              "amplitude": 0.1, "length": 60},
        "cz_flattop_pulse": {"__class__": "SquarePulse",
                             "amplitude": 0.12, "length": 80},
    }
    return {
        "qubits": {
            "q1": qubit("q1", 5.0e9, 250e6, -1.0e9, 0.05),
            "q2": qubit("q2", 4.8e9, 230e6, -0.8e9, -0.02, ops=cz_ops),
        },
        "qubit_pairs": {
            "q1-2": {
                "id": "q1-2",
                "qubit_control": "#/qubits/q1",
                "qubit_target": "#/qubits/q2",
                "moving_qubit": "target",
                "macros": {
                    "cz_unipolar": {"__class__": "CZGate",
                                    "flux_pulse_qubit": {"amplitude": 0.1,
                                                         "length": 60},
                                    "phase_shift_control": 0.0},
                    "cz_flattop": {"__class__": "CZGate",
                                   "flux_pulse_qubit": {"amplitude": 0.12,
                                                        "length": 80},
                                   "phase_shift_control": 0.0},
                },
            },
        },
        "active_qubit_names": ["q1", "q2"],
    }


@pytest.fixture()
def chip(tmp_path):
    live = tmp_path / "chips" / "c1"
    live.mkdir(parents=True)
    (live / "state.json").write_text(json.dumps(_chip_state()), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps(WIRING), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    client = app.test_client()
    client.post("/load", data={"folder": str(live)})
    return app, client


def _ctx(app):
    return list(app.config["contexts"].values())[0]


# ── Validation ───────────────────────────────────────────────────────────

class TestValidation:
    def test_both_ok(self):
        assert validate_params(QC, QT) == []

    def test_missing_f01(self):
        errs = validate_params({**QC, "f_01": None}, QT)
        assert len(errs) == 1 and "f_01" in errs[0]

    def test_missing_anharmonicity(self):
        errs = validate_params(QC, {**QT, "anharmonicity": None})
        assert len(errs) == 1 and "anharmonicity" in errs[0]


# ── The two conventions ──────────────────────────────────────────────────

class TestAnharmonicitySign:
    """A is stored POSITIVE, so |20> = 2*f_c - A_c. The original code used
    ``f_t - f_c - A_c``, which puts the CZ interaction point 2*A (≈400-460 MHz
    on a real chip) away from where it is — on a plot the user can click to
    stage a z voltage."""

    def test_the_crossings_are_cz_branchs_own_branch_conditions(self):
        # calibration_utils/chevron_cz/cz_branch.py:
        #   "20": detuning = f_c - f_t - A_c   -> zero at f_c = f_t + A_c
        #   "02": detuning = f_c - f_t + A_t   -> zero at f_c = f_t - A_t
        zeros = {z["label"]: z["frequency"] for z in
                 compute_detuning_sweep(QC, QT, "control")["zeros"]}
        assert zeros["Δ(11−20)=0"] == pytest.approx(QT["f_01"] + QC["anharmonicity"])
        assert zeros["Δ(11−02)=0"] == pytest.approx(QT["f_01"] - QT["anharmonicity"])
        assert zeros["Δ(10−01)=0"] == pytest.approx(QT["f_01"])

    def test_sweeping_the_target_solves_the_same_equations_for_f_t(self):
        # All three crossings sit ABOVE this target, so it needs the upward
        # (lower-sweet-spot) curvature to reach them — the case the lab's
        # node 09 warns about, and a real one. With QT's own downward
        # curvature the honest answer is "unreachable", which the next test
        # pins; it is not a place to assert crossings into existence.
        qt_up = _q("qA2", f_01=4.8e9, anharmonicity=230e6, quad_term=+0.8e9,
                   joint_offset=-0.02)
        zeros = {z["label"]: z["frequency"] for z in
                 compute_detuning_sweep(QC, qt_up, "target")["zeros"]}
        assert zeros["Δ(11−20)=0"] == pytest.approx(QC["f_01"] - QC["anharmonicity"])
        assert zeros["Δ(11−02)=0"] == pytest.approx(QC["f_01"] + qt_up["anharmonicity"])
        assert zeros["Δ(10−01)=0"] == pytest.approx(QC["f_01"])

    def test_a_crossing_on_the_side_flux_cannot_reach_is_not_invented(self):
        # QT bends DOWN and every crossing is above it: zero markers, and the
        # plot says why rather than drawing a hairline around the bias.
        sw = compute_detuning_sweep(QC, QT, "target")
        assert sw["zeros"] == []
        assert any("No reachable crossing" in n for n in sw["notes"])

    def test_the_curves_hit_zero_where_the_markers_say_they_do(self):
        """Not a restatement of the solver — read the plotted curve itself."""
        sw = compute_detuning_sweep(QC, QT, "control")
        xs = sw["x"]
        for key, label in (("delta_11_20", "Δ(11−20)=0"),
                           ("delta_11_02", "Δ(11−02)=0"),
                           ("delta_10_01", "Δ(10−01)=0")):
            vs = [z["voltage"] for z in sw["zeros"] if z["label"] == label]
            assert vs, label
            for v in vs:
                i = min(range(len(xs)), key=lambda j: abs(xs[j] - v))
                assert abs(sw[key][i]) < 5e6, (label, sw[key][i])

    def test_formulas_at_the_operating_point(self):
        sw = compute_detuning_sweep(QC, QT, "control")
        i = min(range(len(sw["x"])),
                key=lambda j: abs(sw["x"][j] - sw["operating_point"]["x"]))
        assert sw["delta_11_20"][i] == pytest.approx(
            QT["f_01"] - QC["f_01"] + QC["anharmonicity"], abs=1e4)
        assert sw["delta_11_02"][i] == pytest.approx(
            QC["f_01"] - QT["f_01"] + QT["anharmonicity"], abs=1e4)
        assert sw["delta_10_01"][i] == pytest.approx(
            QC["f_01"] - QT["f_01"], abs=1e4)


class TestFluxPointIsAMode:
    """``z.flux_point`` is "joint"/"independent", never a voltage. Reading it
    as one made ``float("joint")`` fail, silently re-centring the parabola on
    0 V while the qubit really idles at ``joint_offset`` — on the real CQT
    chip, 62.7 mV away, with the click-to-stage target computed from there."""

    def test_the_vertex_and_the_operating_point_are_the_idle_bias(self):
        sw = compute_detuning_sweep(QC, QT, "control")
        assert sw["idle_voltage"] == QC["joint_offset"] == 0.05
        assert sw["operating_point"]["voltage"] == 0.05
        # f at the vertex is the stored f_01 — that is where it was measured
        i = min(range(len(sw["x"])), key=lambda j: abs(sw["x"][j] - 0.05))
        assert sw["frequencies"][i] == pytest.approx(QC["f_01"], rel=1e-6)

    def test_independent_flux_point_reads_the_independent_offset(self, chip):
        app, _ = chip
        eng = _ctx(app)["engine"]
        st = eng.store.merged["qubits"]["q1"]["z"]
        st["flux_point"] = "independent"
        st["independent_offset"] = 0.123
        p = extract_qubit_params(eng, "q1")
        assert p["flux_point"] == "independent"
        assert p["idle_voltage"] == 0.123
        assert p["idle_source"] == "z.independent_offset"

    def test_a_mode_string_never_becomes_a_number(self, chip):
        app, _ = chip
        p = extract_qubit_params(_ctx(app)["engine"], "q1")
        assert p["flux_point"] == "joint"
        assert p["idle_voltage"] == 0.05 and p["idle_source"] == "z.joint_offset"

    def test_the_click_target_is_the_field_the_bias_came_from(self):
        q = _q("qX", flux_point="independent", joint_offset=None,
               independent_offset=0.2)
        fig = build_plotly_figure(compute_detuning_sweep(q, QT, "control"),
                                  moving_role="control")
        assert fig["clickable"]["targets"][0]["path"] == "qubits.qX.z.independent_offset"


# ── Honest degradation ───────────────────────────────────────────────────

class TestHonestAxes:
    def test_no_quad_term_is_a_frequency_axis_not_a_flat_line(self):
        """The old fallback kept x = voltage while holding it constant, so all
        500 points shared one x — a single vertical line billed as a sweep."""
        sw = compute_detuning_sweep(_q("q1", quad_term=None), QT, "control")
        assert sw["has_flux"] is False and sw["x_kind"] == "frequency"
        assert len(set(sw["x"])) > 400
        fig = build_plotly_figure(sw, moving_role="control")
        assert fig["clickable"] is None
        assert "MHz" in fig["layout"]["xaxis"]["title"]["text"]
        assert fig["notes"] and "quad" in fig["notes"][0].lower()

    def test_a_curvature_with_no_bias_to_anchor_it_says_so(self):
        sw = compute_detuning_sweep(
            _q("q1", joint_offset=None, independent_offset=None), QT, "control")
        assert sw["has_flux"] is False and sw["x_kind"] == "frequency"
        assert any("anchor" in n for n in sw["notes"])
        assert build_plotly_figure(sw, moving_role="control")["clickable"] is None

    def test_unreachable_crossings_are_named_and_the_window_stays_physical(self):
        """Positive curvature = the lower sweet spot the lab's node warns
        about: the crossings below are unreachable. The old code answered with
        zero markers and a ±10 mV window around the wrong centre."""
        q = _q("q1", f_01=5.5e9, quad_term=+3.8e8, joint_offset=0.0627)
        sw = compute_detuning_sweep(q, QT, "control")
        assert sw["zeros"] == []
        assert any("No reachable crossing" in n for n in sw["notes"])
        assert max(sw["x"]) - min(sw["x"]) > 0.5          # not a hairline
        assert min(sw["x"]) < 0.0627 < max(sw["x"])       # centred on the bias

    def test_error_passthrough(self):
        fig = build_plotly_figure(
            compute_detuning_sweep({**QC, "f_01": None}, QT, "control"),
            moving_role="control")
        assert "error" in fig


class TestPlotlyFigure:
    def test_structure(self):
        fig = build_plotly_figure(compute_detuning_sweep(QC, QT, "control"),
                                  moving_role="control")
        assert len(fig["data"]) >= 3 and "layout" in fig

    def test_clickable_with_flux(self):
        fig = build_plotly_figure(compute_detuning_sweep(QC, QT, "control"),
                                  moving_role="control")
        assert fig["clickable"]["axis"] == "x"
        assert fig["clickable"]["targets"][0]["path"] == "qubits.qA1.z.joint_offset"

    def test_the_legend_is_asked_for_explicitly(self):
        """`PlotTheme.houseLayout` merges a base carrying `showlegend: false`,
        so a figure that does not ASK for a legend silently does not get one.
        This figure is three colour-coded curves whose colours are its only
        key, and it shipped without one — measured in real Chrome, the
        `.legend` node was absent from the rendered SVG."""
        fig = build_plotly_figure(compute_detuning_sweep(QC, QT, "control"),
                                  moving_role="control")
        assert fig["layout"]["showlegend"] is True
        assert fig["layout"]["legend"]["orientation"] == "h"
        # and every curve has to be nameable in it
        named = [t["name"] for t in fig["data"] if t.get("name")]
        for lbl in ("Δ(|11⟩−|20⟩)", "Δ(|11⟩−|02⟩)", "Δ(|10⟩−|01⟩)"):
            assert lbl in named

    def test_the_top_margin_makes_room_for_the_second_axis(self):
        """The plot title and xaxis2's own title both live in the TOP margin.
        At t=60 they were drawn over each other — measured in real Chrome as
        two centred strings overlapping by 4 px. The title is anchored to the
        container and the margin grows only when the second axis exists."""
        dual = build_plotly_figure(compute_detuning_sweep(QC, QT, "control"),
                                   moving_role="control")["layout"]
        assert "xaxis2" in dual
        assert dual["margin"]["t"] >= 100
        assert dual["title"]["yref"] == "container"
        # a frequency-axis figure has no second axis and keeps the tight margin
        single = build_plotly_figure(
            compute_detuning_sweep(_q("q1", quad_term=None), QT, "control"),
            moving_role="control")["layout"]
        assert "xaxis2" not in single
        assert single["margin"]["t"] == 60

    def test_the_plot_is_tall_enough_to_read(self):
        """t=104 + b=80 out of a 300 px box leaves a 116 px data area for three
        curves and six markers. The mount height and the CSS floor agree."""
        import re
        root = __import__("pathlib").Path(__file__).resolve().parent.parent
        css = (root / "quam_state_manager" / "web" / "static" / "style.css").read_text(encoding="utf-8")
        js = (root / "quam_state_manager" / "web" / "static" / "app.js").read_text(encoding="utf-8")
        for sel in (".gi-plot-area", ".gi-plot"):
            m = re.search(re.escape(sel) + r"\s*\{[^}]*min-height:\s*(\d+)px", css)
            assert m and int(m.group(1)) >= 400, sel
        assert re.search(r"inner\.style\.minHeight = '(\d+)px'", js).group(1) == "420"

    def test_the_second_axis_is_measured_from_the_idle_bias(self):
        fig = build_plotly_figure(compute_detuning_sweep(QC, QT, "control"),
                                  moving_role="control")
        ax2 = fig["layout"]["xaxis2"]
        assert "idle bias" in ax2["title"]["text"]
        # the tick at the idle voltage reads zero detuning
        i = min(range(len(ax2["tickvals"])),
                key=lambda j: abs(ax2["tickvals"][j] - 0.05))
        assert ax2["ticktext"][i] == "0"


# ── The switch planner + its route ───────────────────────────────────────

class TestSwitchPlan:
    def test_it_moves_only_this_gates_pulses(self, chip):
        app, _ = chip
        store = _ctx(app)["store"]
        store.merged["qubits"]["q2"]["z"]["operations"]["cz_flattop_2_pulse"] = {
            "__class__": "SquarePulse", "amplitude": 0.9}
        plan = plan_switch_moving_qubit(store, "q1-2", "control")
        moved = {p.rsplit(".", 1)[-1] for p in plan["deletes"]}
        # `startswith("cz_flattop")` would have dragged cz_flattop_2 along
        assert moved == {"cz_unipolar_pulse", "cz_flattop_pulse"}

    def test_the_role_flip_and_the_new_home_are_both_planned(self, chip):
        app, _ = chip
        plan = plan_switch_moving_qubit(_ctx(app)["store"], "q1-2", "control")
        assert ("qubit_pairs.q1-2.moving_qubit", "control") in plan["sets"]
        created = dict(plan["creates"])
        # q1 has no z.operations at all -> the parent dict is created whole
        assert "qubits.q1.z.operations" in created
        assert set(created["qubits.q1.z.operations"]) == {
            "cz_unipolar_pulse", "cz_flattop_pulse"}

    def test_same_role_is_refused(self, chip):
        app, _ = chip
        plan = plan_switch_moving_qubit(_ctx(app)["store"], "q1-2", "target")
        assert "error" in plan and not plan["creates"] and not plan["deletes"]

    def test_an_unknown_role_is_refused(self, chip):
        app, _ = chip
        assert "error" in plan_switch_moving_qubit(_ctx(app)["store"], "q1-2", "nope")


class TestRoutes:
    """The gap that let both crashes ship: the PR had no route test at all."""

    def test_the_plot_route_answers(self, chip):
        _, c = chip
        r = c.get("/pair/q1-2/gate-inspector/plot")
        assert r.status_code == 200
        fig = r.get_json()
        assert fig["clickable"]["targets"][0]["path"] == "qubits.q2.z.joint_offset"
        op = [t for t in fig["data"] if t.get("name") == "Operating point"][0]
        assert op["x"] == [-0.02]          # the real idle bias, not 0 V

    def test_switch_moving_with_the_token_the_browser_sends(self, chip):
        """`_chip_token` does not exist — this call used to raise NameError
        and 500 on every press, because app.js always sends expect_chip."""
        app, c = chip
        token = c.get("/chip/active-token").get_json()["token"]
        assert token
        r = c.post("/pair/q1-2/gate-inspector/switch-moving",
                   data={"role": "control", "expect_chip": token})
        assert r.status_code == 200, r.get_data(as_text=True)[:300]

        merged = _ctx(app)["store"].merged
        assert merged["qubit_pairs"]["q1-2"]["moving_qubit"] == "control"
        assert set(merged["qubits"]["q1"]["z"]["operations"]) == {
            "cz_unipolar_pulse", "cz_flattop_pulse"}
        assert merged["qubits"]["q2"]["z"].get("operations") == {}

    def test_the_whole_rewire_is_one_undo_group(self, chip):
        app, c = chip
        c.post("/pair/q1-2/gate-inspector/switch-moving", data={"role": "control"})
        log = _ctx(app)["modifier"].get_change_log()
        assert len(log) >= 4
        assert len({e.group_id for e in log}) == 1
        assert all(e.group_id for e in log)

    def test_a_stale_chip_token_is_refused_before_anything_is_staged(self, chip):
        app, c = chip
        r = c.post("/pair/q1-2/gate-inspector/switch-moving",
                   data={"role": "control", "expect_chip": "not-this-chip"})
        assert r.status_code == 409
        assert _ctx(app)["modifier"].get_change_log() == []

    def test_switching_to_the_current_role_is_a_409_not_a_500(self, chip):
        _, c = chip
        r = c.post("/pair/q1-2/gate-inspector/switch-moving", data={"role": "target"})
        assert r.status_code == 409

    def test_the_section_renders_on_a_cz_pair_and_not_on_a_bare_one(self, chip):
        app, c = chip
        html = c.get("/pair/q1-2").get_data(as_text=True)
        assert "gi-inspector" in html and "2Q Gate Detuning Inspector" in html
        _ctx(app)["store"].merged["qubit_pairs"]["q1-2"]["macros"] = {}
        assert "gi-inspector" not in c.get("/pair/q1-2").get_data(as_text=True)


class TestTheOperatingPointFollowsThePulse:
    """docs/162: opening a flux pulse shows the pair's detuning curve, because
    a flux pulse's whole meaning is where it puts the moving qubit.

    The mapping is READ from the pulse, never inferred from its name. On the
    corpus 20-qubit chip q2 is the moving qubit of BOTH q1-2 and q2-5, so
    matching `cz_unipolar_pulse` against the pairs that declare that macro
    returns two answers, and "take the first" would caption the pulse with the
    wrong pair's operating point. The pulse's own fields are pointers into the
    macro that owns it, and that is the answer.
    """

    @staticmethod
    def _store(app):
        return _ctx(app)["store"]

    def test_a_pair_macros_own_pulse_needs_no_inference(self, chip):
        app, _ = chip
        assert gate_inspector.pair_for_pulse(
            self._store(app),
            "qubit_pairs.q1-2.macros.cz_unipolar.flux_pulse_qubit") == "q1-2"

    def test_a_z_pulse_is_read_from_its_pointer_not_its_name(self, chip):
        app, _ = chip
        st = self._store(app)
        # q2 is the moving qubit of q1-2 here; point the pulse at a DIFFERENT
        # pair and the answer must follow the pointer, not the name.
        st.merged["qubit_pairs"]["q9-9"] = {
            "id": "q9-9", "qubit_control": "#/qubits/q1", "qubit_target": "#/qubits/q2",
            "moving_qubit": "target", "macros": {"cz_unipolar": {
                "__class__": "CZGate", "flux_pulse_qubit": {"amplitude": 0.1, "length": 60},
                "phase_shift_control": 0.0}}}
        op = st.merged["qubits"]["q2"]["z"]["operations"]["cz_unipolar_pulse"]
        op["amplitude"] = "#/qubit_pairs/q9-9/macros/cz_unipolar/flux_pulse_qubit/amplitude"
        assert gate_inspector.pair_for_pulse(
            st, "qubits.q2.z.operations.cz_unipolar_pulse") == "q9-9"

    def test_two_pairs_named_by_one_pulse_is_no_answer(self, chip):
        app, _ = chip
        st = self._store(app)
        op = st.merged["qubits"]["q2"]["z"]["operations"]["cz_unipolar_pulse"]
        op["amplitude"] = "#/qubit_pairs/q1-2/macros/cz_unipolar/flux_pulse_qubit/amplitude"
        op["length"] = "#/qubit_pairs/q2-5/macros/cz_unipolar/flux_pulse_qubit/length"
        assert gate_inspector.pair_for_pulse(
            st, "qubits.q2.z.operations.cz_unipolar_pulse") is None

    def test_a_pulse_with_no_pair_pointer_gets_nothing(self, chip):
        app, _ = chip
        st = self._store(app)
        st.merged["qubits"]["q1"]["z"] = {"flux_point": "joint", "joint_offset": 0.05,
                                          "operations": {"const": {"amplitude": 0.1}}}
        assert gate_inspector.pair_for_pulse(st, "qubits.q1.z.operations.const") is None

    def test_a_pair_the_chip_does_not_have_is_refused(self, chip):
        app, _ = chip
        assert gate_inspector.pair_for_pulse(
            self._store(app),
            "qubit_pairs.nosuch.macros.cz_unipolar.flux_pulse_qubit") is None

    def test_a_non_flux_pulse_is_not_a_flux_pulse(self, chip):
        """The xy pulse here CARRIES a pair pointer, so only the z-line path
        gate can refuse it. Without one that pointed anywhere, this passed for
        the wrong reason -- the fixture had no xy operation at all, so the
        lookup raised and returned None however the gate was written."""
        app, _ = chip
        st = self._store(app)
        st.merged["qubits"]["q1"]["xy"] = {"operations": {"x180": {
            "__class__": "SquarePulse",
            "amplitude": "#/qubit_pairs/q1-2/macros/cz_unipolar/flux_pulse_qubit/amplitude"}}}
        assert gate_inspector.pair_for_pulse(
            st, "qubits.q1.xy.operations.x180") is None

    def test_a_z_pulse_pointing_at_a_pair_the_chip_lacks_is_refused(self, chip):
        """The existence check on the POINTER branch. A dangling macro pointer
        (a pair renamed or removed out of band) must not caption the pulse
        with a pair that is not there."""
        app, _ = chip
        st = self._store(app)
        op = st.merged["qubits"]["q2"]["z"]["operations"]["cz_unipolar_pulse"]
        op["amplitude"] = "#/qubit_pairs/ghost-pair/macros/cz_unipolar/flux_pulse_qubit/amplitude"
        assert gate_inspector.pair_for_pulse(
            st, "qubits.q2.z.operations.cz_unipolar_pulse") is None

    def test_the_pulse_inspector_renders_it_read_only(self, chip):
        _, c = chip
        html = c.get("/pulse/detail",
                     query_string={"path": "qubit_pairs.q1-2.macros.cz_unipolar.flux_pulse_qubit"}
                     ).get_data(as_text=True)
        assert "Operating point · q1-2" in html
        assert "gi-in-pulse" in html and 'class="gi-plot' in html
        # the moving-qubit SWITCH is a structural rewire -- it stays on the
        # pair's own page, never in a pulse inspector
        assert "gi-switch-btn" not in html
        assert "gi-chip" in html          # the read-only sweep toggle does come

    def test_a_pulse_with_no_pair_renders_no_section(self, chip):
        _, c = chip
        html = c.get("/pulse/detail",
                     query_string={"path": "qubits.q1.xy.operations.x180"}).get_data(as_text=True)
        assert "gi-in-pulse" not in html and "Operating point ·" not in html

    def test_the_pair_page_still_offers_the_switch(self, chip):
        _, c = chip
        html = c.get("/pair/q1-2").get_data(as_text=True)
        assert "gi-switch-btn" in html
