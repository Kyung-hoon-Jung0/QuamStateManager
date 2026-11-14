"""Tests for the static core budget counter (quam_state_manager.core.core_layout)."""

from __future__ import annotations

import copy

from quam_state_manager.core import core_layout as cl

# --- literals copied from the specification text --------------------------
SPEC_FEM_MESSAGE = "{cores} / {limit} cores{ce} on {con} FEM {fem} ({type}-FEM); routes {routes} / 10{re}"
SPEC_ESTIMATED = " (estimated)"
SPEC_EXAMPLE = "10 / 8 cores on con1 FEM 1 (MW-FEM); routes 4 / 10 (estimated)"
SPEC_PREFIX = "Core layout warning: {message}"
SPEC_GROUPS = "Largest groups: {name} ({cost}), {name} ({cost}), {name} ({cost})."
SPEC_FIX_RECOMMEND = "Fix: press Recommend core layout, or move elements off this FEM."
SPEC_FIX_REVIEW = "Fix: tick Share one core per qubit (xy + readout), or move elements off this FEM."
SPEC_STATIC = ("Static worst case: every declared element is counted as if one program played them all; "
               "measure_accumulated needs more.")
SPEC_ROUTES = "Digital routes: {routes} / 10; limit measured on QOP 3.7.3; an ADC trace uses one more."
SPEC_ESTIMATED_LINE = "Estimated: {terms}."
SPEC_OSC = "{k} oscillators on core {name} on {con} FEM {fem} (limit 6, estimated)"
SPEC_TWO_FEMS = "Core name '{name}' on two FEMs; sharing needs the same FEM"
SPEC_BOTH = "{element}: thread ignored, core used"
SPEC_TERMS = ("element on two FEMs", "LF port at 2 GSa/s", "OPX+ costs", "OPX costs", "MW time tagging",
              "MW readout with 2 processes", "QOP version unknown", "QOP version {v}")
SPEC_OTHER_CAUSE = ("If a program still fails to allocate resources while these counts are within limits, "
                    "another program open on the same cluster may be using cores from the same budget.")


def test_strings_match_spec():
    assert cl.FEM_MESSAGE == SPEC_FEM_MESSAGE
    assert cl.ESTIMATED_LABEL == SPEC_ESTIMATED
    assert cl.LINE_PREFIX == SPEC_PREFIX
    one = cl.DETAIL_GROUP_ITEM.format(name="{name}", cost="{cost}")
    assert cl.DETAIL_GROUPS.format(groups=", ".join([one] * 3)) == SPEC_GROUPS
    assert cl.FIX_RECOMMEND == SPEC_FIX_RECOMMEND
    assert cl.FIX_REVIEW == SPEC_FIX_REVIEW
    assert cl.DETAIL_STATIC == SPEC_STATIC
    assert cl.DETAIL_ROUTES == SPEC_ROUTES
    assert cl.DETAIL_ESTIMATED == SPEC_ESTIMATED_LINE
    assert cl.OSCILLATOR_MESSAGE == SPEC_OSC
    assert cl.TWO_FEMS_MESSAGE == SPEC_TWO_FEMS
    assert cl.BOTH_KEYS_MESSAGE == SPEC_BOTH
    assert cl.ESTIMATED_TERMS == SPEC_TERMS
    assert cl.DETAIL_OTHER_CAUSE == SPEC_OTHER_CAUSE
    assert cl.OWN_CORE_NAME == "own"


def test_constants_match_spec():
    assert (cl.MW_FEM_CORE_LIMIT, cl.LF_FEM_CORE_LIMIT) == (8, 16)
    assert (cl.OPX_PLUS_CORE_LIMIT, cl.OPX_CORE_LIMIT) == (18, 10)
    assert cl.ROUTE_LIMIT == 10 and cl.OSCILLATOR_LIMIT == 6
    assert cl.ROUTE_LIMIT_MEASURED_QOP == "3.7.3"
    assert (cl.MW_OUTPUT_COST, cl.MW_READOUT_PROCESS_COST, cl.MW_DIGITAL_COST) == (1, 0.5, 0.5)
    assert (cl.LF_SINGLE_COST, cl.LF_IQ_COST, cl.LF_READOUT_PROCESS_COST, cl.LF_DIGITAL_COST) == (1, 2, 1, 1)
    assert (cl.LF_FAST_RATE, cl.LF_FAST_FACTOR) == (2e9, 2)
    assert (cl.OPX_PLUS_SINGLE_COST, cl.OPX_PLUS_IQ_COST, cl.OPX_PLUS_READOUT_PROCESS_COST,
            cl.OPX_PLUS_DIGITAL_COST) == (1, 2, 1, 1)
    assert cl.READOUT_PROCESSES == 2


# --- chip builder ----------------------------------------------------------
class Chip:
    def __init__(self):
        self.s = {"qubits": {}, "ports": {}, "wiring": {"qubits": {}}}
        self.n = {}

    def port(self, ptype, con, fem, rate=1e9):
        key = (ptype, con, fem)
        self.n[key] = self.n.get(key, 0) + 1
        p = str(self.n[key])
        self.s["ports"].setdefault(ptype, {}).setdefault(con, {}).setdefault(str(fem), {})[p] = {
            "sampling_rate": rate}
        return f"#/ports/{ptype}/{con}/{fem}/{p}"

    def el(self, q, role, core=None, **ports):
        e = {"core": core, "thread": None, "intermediate_frequency": 1e8, "digital_outputs": {}}
        e.update(ports)
        self.s["qubits"].setdefault(q, {})[role] = e
        return e

    def mw_qubit(self, q, fem=1, core_xy=None, core_rr=None, con="con1"):
        self.el(q, "xy", core_xy, opx_output=self.port("mw_outputs", con, fem))
        self.el(q, "resonator", core_rr, opx_output=self.port("mw_outputs", con, fem),
                opx_input=self.port("mw_inputs", con, fem))

    def digital(self, q, role, con, fem, n=1, core=None):
        e = self.s["qubits"].get(q, {}).get(role) or self.el(q, role, core)
        for i in range(n):
            e["digital_outputs"][f"d{i}"] = {"opx_output": self.port("digital_outputs", con, fem)}
        return e


def fem(rep, con="con1", fem_id=1):
    return next(f for f in rep["fems"] if f["controller"] == con and f["fem"] == fem_id)


def core_findings(state):
    return [f for f in cl.core_budget_findings(state) if f.values.get("kind") == "fem_budget"]


# --- measured cases --------------------------------------------------------
def test_five_qubits_null_cores_over_mw_limit():
    c = Chip()
    for i in range(5):
        c.mw_qubit(f"q{i}")
    f = fem(cl.core_budget(c.s))
    assert f["cores"]["count"] == 10.0 and f["cores"]["limit"] == 8
    assert f["cores"]["estimated"] is False and f["cores"]["firm_count"] == 10.0
    fs = core_findings(c.s)
    assert len(fs) == 1
    assert fs[0].message == "10 / 8 cores on con1 FEM 1 (MW-FEM); routes 0 / 10 (estimated)"
    assert fs[0].severity == "warning" and fs[0].category == "core_layout"
    assert fs[0].detail.split("\n")[-1] == SPEC_OTHER_CAUSE


def test_example_message_with_four_routes():
    c = Chip()
    for i in range(5):
        c.mw_qubit(f"q{i}")
    for i in range(4):
        c.digital(f"q{i}", "xy", "con1", 1)
    f = fem(cl.core_budget(c.s))
    assert f["cores"]["count"] == 10.0  # digital 0.5 under xy 1: max, not sum
    assert cl.fem_message(f) == SPEC_EXAMPLE
    d = cl.fem_detail(f, SPEC_FIX_RECOMMEND).split("\n")
    assert d == ["Largest groups: own (1), own (1), own (1).", SPEC_FIX_RECOMMEND, SPEC_STATIC,
                 SPEC_ROUTES.format(routes=4), "Estimated: QOP version unknown.", SPEC_OTHER_CAUSE]
    assert SPEC_PREFIX.format(message=cl.fem_message(f)) == "Core layout warning: " + SPEC_EXAMPLE


def test_eight_pass_nine_warn():
    c = Chip()
    for i in range(4):
        c.mw_qubit(f"q{i}")
    assert fem(cl.core_budget(c.s))["cores"]["count"] == 8.0
    assert core_findings(c.s) == []
    c.el("qx", "xy", opx_output=c.port("mw_outputs", "con1", 1))
    assert fem(cl.core_budget(c.s))["cores"]["count"] == 9.0
    assert len(core_findings(c.s)) == 1


def test_layout_a_five_cores_no_finding():
    c = Chip()
    for i in range(5):
        c.mw_qubit(f"q{i}", core_xy=f"q{i}_core", core_rr=f"q{i}_core")
    f = fem(cl.core_budget(c.s))
    assert f["cores"]["count"] == 5.0
    assert cl.core_budget_findings(c.s) == []
    g = f["groups"][0]
    assert len(g["elements"]) == 2 and g["cost"] == 1.0


def test_thread_only_groups_by_thread():
    c = Chip()
    for i in range(5):
        c.mw_qubit(f"q{i}")
        for r in ("xy", "resonator"):
            c.s["qubits"][f"q{i}"][r]["thread"] = f"q{i}"
            del c.s["qubits"][f"q{i}"][r]["core"]
    assert fem(cl.core_budget(c.s))["cores"]["count"] == 5.0
    assert cl.effective_core_name({"core": None, "thread": "t"}) == ("t", "thread")
    assert cl.effective_core_name({"core": "c", "thread": "t"}) == ("c", "core")


def test_both_keys_warn_and_core_wins():
    c = Chip()
    c.mw_qubit("q0", core_xy="a", core_rr="b")
    c.s["qubits"]["q0"]["xy"]["thread"] = "b"
    c.s["qubits"]["q0"]["resonator"]["thread"] = "b"
    msgs = [f.message for f in cl.core_budget_findings(c.s)]
    assert "qubits.q0.xy: thread ignored, core used" in msgs
    assert "qubits.q0.resonator: thread ignored, core used" in msgs
    assert fem(cl.core_budget(c.s))["cores"]["count"] == 2.0  # a and b, not one thread group


def test_same_name_on_two_fems_not_merged():
    c = Chip()
    c.el("q0", "xy", "shared", opx_output=c.port("mw_outputs", "con1", 1))
    c.el("q1", "xy", "shared", opx_output=c.port("mw_outputs", "con1", 2))
    rep = cl.core_budget(c.s)
    assert fem(rep, fem_id=1)["cores"]["count"] == 1.0
    assert fem(rep, fem_id=2)["cores"]["count"] == 1.0
    msgs = [f.message for f in cl.core_budget_findings(c.s)]
    assert "Core name 'shared' on two FEMs; sharing needs the same FEM" in msgs


def test_element_on_two_fems_charged_to_both_and_estimated():
    c = Chip()
    c.el("q0", "resonator", "x", opx_output=c.port("mw_outputs", "con1", 1),
         opx_input=c.port("mw_inputs", "con1", 2))
    rep = cl.core_budget(c.s)
    f1, f2 = fem(rep, fem_id=1), fem(rep, fem_id=2)
    assert f1["cores"]["count"] == 1.0 and f2["cores"]["count"] == 1.0
    assert f1["cores"]["estimated"] and "element on two FEMs" in f1["estimated_terms"]
    assert f1["cores"]["firm_count"] == 0.0
    assert f1["groups"][0]["core"] is None  # never in a group


def test_malformed_states_do_not_raise():
    assert cl.core_budget_findings({}) == []
    assert cl.core_budget_findings(None) == []
    assert cl.core_budget_findings({"qubits": "x", "ports": 3}) == []
    c = Chip()
    c.mw_qubit("q0")
    c.el("q1", "xy", opx_output="#/wiring/qubits/q1/xy/opx_output")  # dangling
    c.el("q2", "xy", opx_output=42)
    del c.s["ports"]
    rep = cl.core_budget(c.s)
    # without the ports section the refs still parse; FEM kind falls back
    assert rep["fems"] and sum(f["cores"]["count"] for f in rep["fems"]) > 0


def test_dangling_ref_counts_the_rest():
    c = Chip()
    for i in range(5):
        c.mw_qubit(f"q{i}")
    c.el("qz", "xy", opx_output="#/wiring/qubits/qz/xy/opx_output")
    assert fem(cl.core_budget(c.s))["cores"]["count"] == 10.0


# --- cost table ------------------------------------------------------------
def test_mw_resonator_firm_cost_one():
    c = Chip()
    c.el("q0", "resonator", opx_output=c.port("mw_outputs", "con1", 1), opx_input=c.port("mw_inputs", "con1", 1))
    f = fem(cl.core_budget(c.s))
    assert f["cores"]["count"] == 1.0 and not f["cores"]["estimated"]


def test_mw_input_only_rests_on_estimated_readout():
    c = Chip()
    c.el("q0", "resonator", opx_input=c.port("mw_inputs", "con1", 1))
    f = fem(cl.core_budget(c.s))
    assert f["cores"]["count"] == 1.0
    assert f["cores"]["estimated"] and "MW readout with 2 processes" in f["estimated_terms"]


def test_digital_only_half_on_mw_one_on_lf():
    c = Chip()
    c.digital("q0", "sw", "con1", 1)
    c.s["ports"]["mw_outputs"] = {"con1": {"1": {}}}
    c.digital("q1", "sw", "con1", 5)
    c.s["ports"]["analog_outputs"] = {"con1": {"5": {}}}
    rep = cl.core_budget(c.s)
    assert fem(rep, fem_id=1)["cores"]["count"] == 0.5
    assert fem(rep, fem_id=5)["cores"]["count"] == 1.0


def test_half_cores_summed_mw():
    c = Chip()
    c.s["ports"]["mw_outputs"] = {"con1": {"1": {}}}
    for i in range(7):
        c.el(f"q{i}", "xy", opx_output=c.port("mw_outputs", "con1", 1))
    for i in range(2):
        c.digital(f"d{i}", "sw", "con1", 1)
    assert fem(cl.core_budget(c.s))["cores"]["count"] == 8.0
    assert core_findings(c.s) == []
    c.digital("d2", "sw", "con1", 1)
    assert fem(cl.core_budget(c.s))["cores"]["count"] == 8.5
    assert len(core_findings(c.s)) == 1


def test_lf_single_iq_and_fast():
    c = Chip()
    c.el("q0", "z", opx_output=c.port("analog_outputs", "con1", 5))
    assert fem(cl.core_budget(c.s), fem_id=5)["cores"]["count"] == 1.0
    c = Chip()
    c.el("q0", "iq", opx_output_I=c.port("analog_outputs", "con1", 5),
         opx_output_Q=c.port("analog_outputs", "con1", 5))
    f = fem(cl.core_budget(c.s), fem_id=5)
    assert f["cores"]["count"] == 2.0 and not f["cores"]["estimated"]
    c = Chip()
    c.el("q0", "iq", opx_output_I=c.port("analog_outputs", "con1", 5, rate=2e9),
         opx_output_Q=c.port("analog_outputs", "con1", 5, rate=2e9))
    f = fem(cl.core_budget(c.s), fem_id=5)
    assert f["cores"]["count"] == 4.0 and f["cores"]["estimated"]
    assert f["cores"]["firm_count"] == 0.0 and "LF port at 2 GSa/s" in f["estimated_terms"]


def test_lf_fast_input_doubles_slow_output():
    c = Chip()
    c.el("q0", "rr", opx_output=c.port("analog_outputs", "con1", 5),
         opx_input=c.port("analog_inputs", "con1", 5, rate=2e9))
    assert fem(cl.core_budget(c.s), fem_id=5)["cores"]["count"] == 4.0  # readout 1 x 2 proc x 2


def test_lf_readout_two_processes():
    c = Chip()
    c.el("q0", "rr", opx_output=c.port("analog_outputs", "con1", 5),
         opx_input=c.port("analog_inputs", "con1", 5))
    f = fem(cl.core_budget(c.s), fem_id=5)
    assert f["cores"]["count"] == 2.0 and not f["cores"]["estimated"]


def test_lf_at_limit_and_one_over():
    c = Chip()
    for i in range(8):
        c.el(f"q{i}", "iq", f"g{i}", opx_output_I=c.port("analog_outputs", "con1", 5),
             opx_output_Q=c.port("analog_outputs", "con1", 5))
        c.el(f"q{i}", "z", f"g{i}", opx_output=c.port("analog_outputs", "con1", 5))
    f = fem(cl.core_budget(c.s), fem_id=5)
    assert f["cores"]["count"] == 16.0 and f["cores"]["limit"] == 16  # group = max member
    assert core_findings(c.s) == []
    c.el("qx", "z", opx_output=c.port("analog_outputs", "con1", 5))
    assert len(core_findings(c.s)) == 1


def test_mixed_fems_counted_separately():
    c = Chip()
    for i in range(4):
        c.mw_qubit(f"q{i}")
        c.el(f"q{i}", "z", opx_output=c.port("analog_outputs", "con1", 5))
    rep = cl.core_budget(c.s)
    assert fem(rep, fem_id=1)["cores"]["count"] == 8.0
    assert fem(rep, fem_id=5)["cores"]["count"] == 4.0
    assert core_findings(c.s) == []


def test_opx_plus_costs_estimated():
    c = Chip()
    c.s["ports"] = {"analog_outputs": {"con1": {"1": {}, "2": {}}}}
    c.el("q0", "xy", opx_output_I="#/ports/analog_outputs/con1/1", opx_output_Q="#/ports/analog_outputs/con1/2")
    f = cl.core_budget(c.s)["fems"][0]
    assert f["fem"] is None and f["fem_type"] == "OPX+"
    assert f["cores"]["count"] == 2.0 and f["cores"]["limit"] == 18
    assert f["cores"]["estimated"] and "OPX+ costs" in f["estimated_terms"]


# --- routes ----------------------------------------------------------------
def test_routes_ten_pass_eleven_warn():
    c = Chip()
    c.s["ports"]["mw_outputs"] = {"con1": {"1": {}}}
    for i in range(10):
        c.digital(f"d{i}", "sw", "con1", 1, core="one")
    assert core_findings(c.s) == []
    c.digital("d10", "sw", "con1", 1, core="one")
    fs = core_findings(c.s)
    assert len(fs) == 1 and fs[0].values["over_routes"] and not fs[0].values["over_cores"]
    assert fs[0].message.endswith("routes 11 / 10 (estimated)")


def test_two_digital_entries_two_routes_and_per_fem():
    c = Chip()
    c.s["ports"]["mw_outputs"] = {"con1": {"1": {}, "2": {}}}
    for i in range(5):
        c.digital(f"a{i}", "sw", "con1", 1, n=2)
        c.digital(f"b{i}", "sw", "con1", 2, n=2)
    rep = cl.core_budget(c.s)
    assert fem(rep, fem_id=1)["routes"]["count"] == 10
    assert fem(rep, fem_id=2)["routes"]["count"] == 10
    assert core_findings(c.s) == []


def test_route_firm_only_with_measured_qop():
    c = Chip()
    c.mw_qubit("q0")
    c.s["extras"] = {"qop_version": "3.7.3"}
    f = fem(cl.core_budget(c.s))
    assert f["routes"]["estimated"] is False and f["estimated_terms"] == []
    c.s["extras"] = {"qop_version": "3.7.4"}
    f = fem(cl.core_budget(c.s))
    assert f["routes"]["estimated"] and f["estimated_terms"] == ["QOP version 3.7.4"]


# --- oscillators -----------------------------------------------------------
def test_oscillator_cap():
    c = Chip()
    for i in range(7):
        c.el(f"q{i}", "xy", "big", opx_output=c.port("mw_outputs", "con1", 1))
    msgs = [f.message for f in cl.core_budget_findings(c.s)]
    assert "7 oscillators on core big on con1 FEM 1 (limit 6, estimated)" in msgs
    c = Chip()
    for i in range(6):
        c.el(f"q{i}", "xy", "big", opx_output=c.port("mw_outputs", "con1", 1))
    assert [f for f in cl.core_budget_findings(c.s) if f.values["kind"] == "oscillators"] == []


def test_oscillator_null_and_absent_if_count_one_ref_counts():
    c = Chip()
    for i in range(5):
        c.el(f"q{i}", "xy", "g", opx_output=c.port("mw_outputs", "con1", 1))
    c.el("n0", "xy", "g", opx_output=c.port("mw_outputs", "con1", 1))["intermediate_frequency"] = None
    del c.el("n1", "xy", "g", opx_output=c.port("mw_outputs", "con1", 1))["intermediate_frequency"]
    g = fem(cl.core_budget(c.s))["groups"][0]
    assert g["oscillators"] == 6
    c.el("r0", "xy", "g", opx_output=c.port("mw_outputs", "con1", 1))["intermediate_frequency"] = \
        "#./inferred_intermediate_frequency"
    assert fem(cl.core_budget(c.s))["groups"][0]["oscillators"] == 7


def test_findings_never_errors():
    c = Chip()
    for i in range(20):
        c.mw_qubit(f"q{i}")
    assert all(f.severity == "warning" for f in cl.core_budget_findings(copy.deepcopy(c.s)))
