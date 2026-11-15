"""Simulate for the New CZ gate dialog: worker, run bookkeeping, routes.

The QM stack is faked: a fake manager whose ``execute`` / ``open_qm`` raise,
and a fake runner process that writes the answer the real worker writes."""
import hashlib
import json
import re
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

from quam_state_manager.core import cz_gate_simulate as sim
from quam_state_manager.core import port_test_runs as ptr

GEN = Path(sim.__file__).resolve().parent.parent / "generator"
sys.path.insert(0, str(GEN))
import run_gate_sim as rgs  # noqa: E402
import run_port_test as rpt  # noqa: E402

START, STATUS, STOP = ("/api/pulse/cz-gate/simulate/start", "/api/pulse/cz-gate/simulate/status",
                       "/api/pulse/cz-gate/simulate/stop")
CREDS = {"email": "a@b.c", "host": "sim.example", "password": "s3cret-pw"}


# --------------------------------------------------------------------------- worker

def _code(path: Path) -> str:
    """Source without comments and docstrings."""
    import ast
    import io
    import tokenize
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    doc_lines = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and ast.get_docstring(node):
            d = node.body[0]
            doc_lines.update(range(d.lineno, d.end_lineno + 1))
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT or tok.start[0] in doc_lines:
            continue
        out.append(tok.string)
    return " ".join(out)


def test_worker_source_only_simulates_and_never_saves():
    code = _code(GEN / "run_gate_sim.py")
    assert "simulate" in code
    for bad in ("execute", "open_qm", "save", "run_on_hardware"):
        assert not re.search(r"\b" + bad + r"\b", code), bad


class FakeJob:
    def wait_until(self, _s):
        pass

    def get_simulated_samples(self):
        class Con:
            analog = {"1-1": [0.0, 0.1, 0.1, 0.0], "1-7": [0.0] * 4}
            analog_sampling_rate = {"1-1": 2e9, "1-7": 2e9}
            digital = {}
        class S:
            con1 = Con()
        return S()


class FakeQmm:
    def __init__(self):
        self.calls = []

    def simulate(self, config, program, simcfg):
        self.calls.append(("simulate", simcfg))
        return FakeJob()

    def execute(self, *a, **k):
        raise AssertionError("execute called")

    def open_qm(self, *a, **k):
        raise AssertionError("open_qm called")


CONFIG = {"controllers": {"con1": {"fems": {"1": {"type": "LF"}}}},
          "elements": {"q1.z": {"singleInput": {"port": ["con1", 1, 1]}},
                       "q9.z": {"singleInput": {"port": ["con1", 1, 7]}}}}


def _fake_qm(monkeypatch, qmm):
    class SimCfg:
        def __init__(self, duration):
            self.duration = duration
    fake_qm = type(sys)("qm")
    fake_qm.SimulationConfig = SimCfg
    monkeypatch.setitem(sys.modules, "qm", fake_qm)
    monkeypatch.setattr(rpt, "connection", lambda mod, t: qmm)


def test_worker_runs_only_simulate_on_the_manager(monkeypatch):
    qmm = FakeQmm()
    _fake_qm(monkeypatch, qmm)
    mod = rgs.GateModule(CONFIG, object(), [("q1.z", "cz")])
    res = rpt.run_sim(mod, {"local": {"host": "h"}}, {"sim_ns": 504}, lambda **k: None, lambda: False)
    assert [c[0] for c in qmm.calls] == ["simulate"]
    assert qmm.calls[0][1].duration == 504 // 4
    ports = rgs._ports(res["traces"], rgs._port_elements(CONFIG, mod.PLAYS))
    # only the port the program drives, with the simulator's own sample step
    assert [(p["port"], p["elements"], p["dt_ns"]) for p in ports] == [(1, ["q1.z"], 0.5)]
    assert ports[0]["volts"] == [0.0, 0.1, 0.1, 0.0] and ports[0]["cut_at_end"] is False


@pytest.mark.parametrize("ends,pad,want", [([300], 200, 500), ([301], 200, 504), ([10, 999], 0, 1000)])
def test_sim_length_rounds_up_to_4_ns(ends, pad, want):
    rows = [type("R", (), {"end_ns": e})() for e in ends]
    assert rgs.sim_length_ns(rows, [], pad) == (want, "timeline")


def test_sim_length_without_a_timeline_is_the_sum_of_plays():
    ops = [{"length": 100, "delay": 0}, {"length": 60, "delay": 20}]
    assert rgs.sim_length_ns([], ops, 200) == (380, "sum")


def _rows(*ends):
    return [type("R", (), {"end_ns": e})() for e in ends]


def _tl_op(el, length, delay, port=("con1", 1, 1)):
    return {"element": el, "operation": "p", "length": length,
            "waveform": {"type": "constant", "sample": 0.1}, "port": list(port),
            "delay": delay, "sampling_rate": 1e9, "filter": False}


def _tl_rows(ops):
    from quam_state_manager.core.static_timeline import static_timeline
    body = "".join("    play(\"p\", \"" + o["element"] + "\")\n" for o in ops)
    res = static_timeline("from qm.qua import *\n\nwith program() as prog:\n" + body, ops)
    return res["rows"], res["cannot_draw"]


def test_sim_length_from_the_real_timeline_counts_the_delay_once():
    ops = [_tl_op("q1.z", 160, 116), _tl_op("q2.z", 100, 0, port=("con1", 1, 2))]
    rows, why = _tl_rows(ops)
    assert why is None and max(r.end_ns for r in rows) == 276
    # 276 (end incl. delay) + 300 start + 200 pad = 776
    assert rgs.sim_length_ns(rows, ops, 200, 300) == (776, "timeline")


def test_sim_length_timeline_and_sum_agree_for_one_play():
    ops = [_tl_op("q1.z", 160, 116)]
    rows, _ = _tl_rows(ops)
    assert rgs.sim_length_ns(rows, ops, 200, 300)[0] == rgs.sim_length_ns([], ops, 200, 300)[0] == 776


def test_sim_length_zero_delay_unchanged():
    ops = [_tl_op("q1.z", 300, 0)]
    rows, _ = _tl_rows(ops)
    assert rgs.sim_length_ns(rows, ops, 200, 300) == (800, "timeline")
    assert rgs.sim_length_ns([], ops, 200, 300) == (800, "sum")


def test_sim_length_sum_path_adds_the_largest_delay():
    assert rgs.sim_length_ns([], [{"length": 100, "delay": 160}], 200, 300) == (760, "sum")


def _sum_window(delay):
    ops = [{"element": "q1.z", "operation": "x", "length": 100, "delay": delay}]
    return rgs.sim_length_ns([], ops, 200, 300)


def _np_delays():
    import numpy as np
    return [np.int64(160), np.float32(160.0)]


@pytest.mark.parametrize("delay,want", [
    (160, 760), ("160", 760), (" 160 ", 760), ("160.0", 760), (160.0, 760), (0, 600), (None, 600)])
def test_sim_length_exact_window_for_each_delay_type(delay, want):
    assert _sum_window(delay) == (want, "sum")
    assert rgs._delay_ns({"delay": delay}) == want - 600


def test_sim_length_numpy_delays_count_exactly():
    for d in _np_delays():
        assert _sum_window(d) == (760, "sum") and rgs._delay_ns({"delay": d}) == 160


def test_sim_length_missing_delay_key_is_zero():
    assert rgs.sim_length_ns([], [{"length": 100}], 200, 300) == (600, "sum")
    assert rgs._delay_ns({"length": 100}) == 0 and rgs._delay_ns(None) == 0


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -4, "abc", True, "1e999", [], ""])
def test_sim_length_unreadable_delay_is_refused_naming_the_leaf(bad):
    with pytest.raises(ValueError, match=r"port delay of q1\.z/x"):
        _sum_window(bad)
    with pytest.raises(ValueError):
        rgs.sim_length_ns(_rows(500), [{"element": "q1.z", "length": 1, "delay": bad}], 200, 300)


def test_build_turns_a_refused_delay_into_a_gate_answer(monkeypatch):
    class _Tl:
        @staticmethod
        def static_timeline(script, ops):
            return {"rows": [], "cannot_draw": None}
    monkeypatch.setattr(rgs.rpw, "_chip_side", lambda *a, **k: (object(), {}, {"cfg": 1}))
    monkeypatch.setattr(rgs.rpw, "_apply_in_program", lambda m, macro: object())
    monkeypatch.setattr(rgs.rpw, "_plays", lambda script: ([("q1.z", "x")], [], True))
    monkeypatch.setattr(rgs.rpw, "op_data", lambda cfg, el, op: {
        "element": el, "operation": op, "length": 100, "delay": float("inf")})
    monkeypatch.setattr(rgs, "_static_timeline", lambda: _Tl)
    import types
    qm = types.ModuleType("qm")
    qm.generate_qua_script = lambda prog: ""
    monkeypatch.setitem(sys.modules, "qm", qm)
    out = rgs.build({"folder": "f", "macro": "m", "pad_ns": 200, "start_ns": 300})
    assert out["ok"] is False and out["error_kind"] == "gate"
    assert "port delay of q1.z/x" in out["error"] and "finite" in out["error"]


def _len_window(length, rows=None):
    ops = [{"element": "q1.z", "operation": "x", "length": length}]
    return rgs.sim_length_ns([] if rows is None else rows, ops, 200, 300)


@pytest.mark.parametrize("bad", [[1], {}, True, "abc", float("nan"), float("inf"), -4, -8, 40.9, ""])
def test_sim_length_unreadable_pulse_length_is_refused_naming_the_leaf(bad):
    with pytest.raises(ValueError, match=r"length of q1\.z/x") as ei:
        _len_window(bad)
    assert "invalid literal" not in str(ei.value)
    with pytest.raises(ValueError, match="length of q1"):
        _len_window(bad, rows=_rows(500))


def test_sim_length_pulse_length_types_and_rounding():
    import numpy as np
    # 300 + 200 + length, rounded up to 4
    assert _len_window(40.0) == _len_window(40) == (540, "sum")
    assert _len_window("40") == _len_window(np.int64(40)) == _len_window(40) == (540, "sum")
    assert _len_window(0) == _len_window(None) == (500, "sum")


def test_bad_delay_keeps_its_own_message_not_the_length_one():
    ops = [{"element": "q1.z", "operation": "x", "length": 100, "delay": "abc"}]
    with pytest.raises(ValueError, match="port delay") as ei:
        rgs.sim_length_ns([], ops, 200, 300)
    assert "length of" not in str(ei.value)


def test_build_turns_a_refused_length_into_a_gate_answer(monkeypatch):
    class _Tl:
        @staticmethod
        def static_timeline(script, ops):
            return {"rows": [], "cannot_draw": None}
    monkeypatch.setattr(rgs.rpw, "_chip_side", lambda *a, **k: (object(), {}, {"cfg": 1}))
    monkeypatch.setattr(rgs.rpw, "_apply_in_program", lambda m, macro: object())
    monkeypatch.setattr(rgs.rpw, "_plays", lambda script: ([("q1.z", "x")], [], True))
    monkeypatch.setattr(rgs.rpw, "op_data", lambda cfg, el, op: {
        "element": el, "operation": op, "length": [1], "delay": 0})
    monkeypatch.setattr(rgs, "_static_timeline", lambda: _Tl)
    import types
    qm = types.ModuleType("qm")
    qm.generate_qua_script = lambda prog: ""
    monkeypatch.setitem(sys.modules, "qm", qm)
    out = rgs.build({"folder": "f", "macro": "m", "pad_ns": 200, "start_ns": 300})
    assert out["ok"] is False and out["error_kind"] == "gate"
    assert "length of q1.z/x" in out["error"]
    shown = sim.from_status({"state": "done", "result": out}, {})
    assert shown["state"] == "not_run" and "ports" not in shown and "figure" not in shown


@pytest.mark.parametrize("field,val", [
    ("t0_ns", "x"), ("elements", 5), ("t0_ns", [1]), ("volts", "12"), ("elements", "ab"),
    ("volts", [0.1, float("nan")]), ("volts", [0.1, float("inf")]), ("volts", [True]), ("volts", ["0.1"]),
    ("elements", ["q1.z", 3]), ("t0_ns", float("nan")), ("t0_ns", True), ("t0_ns", "3"), ("volts", []), ("volts", {0.1: 0}),
    ("t0_ns", False), ("t0_ns", ""), ("t0_ns", {}), ("elements", False), ("elements", 0), ("elements", {}),
    ("fem", True), ("port", 1.9), ("port", "2"), ("controller", None), ("controller", ""), ("fem", None),
    ("fem", 10**400), ("port", 10**400), ("t0_ns", 10**400), ("volts", [0.1, 10**400])])
def test_malformed_port_entry_is_dropped_others_kept(field, val):
    good = {"controller": "con1", "fem": 1, "port": 1, "volts": [0.1], "dt_ns": 1.0}
    bad = dict(good, port=2)
    bad[field] = val
    shaped = sim._shape_ports([bad, good])
    assert [p["port"] for p in shaped] == [1]
    assert shaped[0]["volts"] == [0.1] and shaped[0]["controller"] == "con1"


def test_port_entry_defaults_and_integral_and_numpy_numbers():
    np = pytest.importorskip("numpy")
    base = {"controller": "con1", "fem": 1, "port": 1, "volts": [0.1]}
    a = sim._shape_ports([dict(base, t0_ns=None, elements=None)])[0]
    assert a["t0_ns"] == 0.0 and a["elements"] == [] and a["key"] == "con1/1/1"
    for t0 in (0, 0.0):
        assert sim._shape_ports([dict(base, t0_ns=t0)])[0]["t0_ns"] == 0.0
    b = sim._shape_ports([dict(base, port=2.0, fem=np.int64(5))])[0]
    assert b["port"] == 2 and b["fem"] == 5 and b["key"] == "con1/5/2"
    c = sim._shape_ports([dict(base, volts=[np.float32(0.5), np.int64(2)])])[0]
    assert c["volts"] == [0.5, 2.0]
    assert sim._shape_ports([dict(base, volts=[np.bool_(True)])]) == []


def test_port_entry_that_is_not_a_dict_is_dropped():
    class Lookalike:  # indexable but no .get
        def __getitem__(self, k):
            return {"controller": "con1", "fem": 1, "port": 2, "volts": [0.1]}[k]
    good = {"controller": "con1", "fem": 1, "port": 1, "volts": [0.1], "elements": ("q1.z",), "t0_ns": 4}
    shaped = sim._shape_ports([Lookalike(), "x", good])
    assert [p["port"] for p in shaped] == [1]
    assert shaped[0]["elements"] == ["q1.z"]


def test_fractional_delay_falls_back_to_sum_and_rounds_up():
    ops = [_tl_op("q1.z", 100, 161.7)]
    rows, why = _tl_rows(ops)
    assert rows == [] and why and "whole number" in why
    assert rgs._delay_ns({"delay": 161.7}) == 162 and rgs._delay_ns({"delay": 160.0}) == 160
    # 100 + 162 + 300 + 200 = 762 -> 764
    assert rgs.sim_length_ns(rows, ops, 200, 300) == (764, "sum")


def test_sim_length_near_the_limit_goes_over_it_not_shortened():
    # 19500 + 300 + 200 = 20000 is allowed; one more 4 ns step is over
    assert rgs.sim_length_ns(_rows(19_500), [], 200, 300)[0] == sim.MAX_SIM_NS
    assert rgs.sim_length_ns(_rows(19_504), [], 200, 300)[0] > sim.MAX_SIM_NS


def test_delayed_program_that_fits_once_is_within_the_limit():
    # end 19500 already holds a 116 ns delay; counted twice it would be refused
    ops = [_tl_op("q1.z", 19_384, 116)]
    rows, _ = _tl_rows(ops)
    assert rgs.sim_length_ns(rows, ops, 200, 300)[0] == sim.MAX_SIM_NS


def _trace(rate, kind="lf", step=1):
    tr = {"con": "con1", "slot": 1, "port": 1, "kind": kind, "step": step, "re": [0.0, 0.1, 0.0]}
    if rate is not ...:
        tr["rate"] = rate
    return {"k": tr}


PE = {"con1/1/1": ["q1.z"]}


def test_lf_trace_with_a_valid_rate_keeps_1_ns():
    [p] = rgs._ports(_trace(1e9), PE, {})
    assert p["dt_ns"] == 1.0 and p["time_step_unknown"] is False


@pytest.mark.parametrize("rate", [..., None, "x", 0, -1, float("nan")])
def test_lf_trace_without_a_rate_takes_the_port_rate_or_the_default(rate):
    cfg = {"controllers": {"con1": {"fems": {"1": {"type": "LF", "analog_outputs": {
        "1": {"sampling_rate": 2e9}}}}}}}
    assert rgs._ports(_trace(rate), PE, cfg)[0]["dt_ns"] == 0.5
    assert rgs._ports(_trace(rate), PE, {})[0]["dt_ns"] == 1.0


@pytest.mark.parametrize("rate", [..., None, "x", 0, float("nan")])
def test_mw_trace_without_any_rate_has_no_time_axis(rate):
    [p] = rgs._ports(_trace(rate, kind="mw"), PE, {})
    assert p["dt_ns"] is None and p["time_step_unknown"] is True
    shaped = sim._shape_ports([p])
    assert shaped[0]["dt_ns"] is None and shaped[0]["time_step_unknown"] is True


def _main(monkeypatch, tmp_path, built, target=None, limit_s=60):
    job = {"folder": str(tmp_path / "chip"), "macro": "qubit_pairs.p.macros.cz",
           "max_ns": sim.MAX_SIM_NS, "pad_ns": sim.PAD_NS, "start_ns": sim.START_LATENCY_NS, "limit_s": limit_s}
    (tmp_path / "job.json").write_text(json.dumps(job), encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setattr(rgs, "build", lambda j: built)
    monkeypatch.setattr(rgs.rpt, "_detach_stdin", lambda: __import__("io").StringIO(""))
    monkeypatch.setattr(rgs.rpt, "watch_stdin", lambda s, e: None)
    import io
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"target": target or {}}) + "\n"))
    monkeypatch.setattr(sys, "argv", ["x", str(tmp_path / "job.json"), str(work)])
    rgs.main()
    return json.loads((work / "_result.json").read_text(encoding="utf-8")), work


def test_worker_refuses_above_20_us_before_connecting(monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(rgs.rpt, "run_sim", lambda *a, **k: called.append(1))
    res, _ = _main(monkeypatch, tmp_path, {"ok": True, "sim_ns": 20004, "config": {}, "program": None,
                                           "plays": [], "length_from": "timeline", "cannot_draw": None})
    assert res == {"ok": False, "too_long": True, "sim_ns": 20004, "max_ns": 20000}
    assert not called


def test_worker_at_20_us_is_simulated(monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(rgs.rpt, "run_sim", lambda *a, **k: called.append(1) or {"traces": {}})
    res, _ = _main(monkeypatch, tmp_path, {"ok": True, "sim_ns": 20000, "config": {}, "program": None,
                                           "plays": [], "length_from": "timeline", "cannot_draw": "cannot draw: x"})
    assert called and res["ok"] and res["cannot_draw"] == "cannot draw: x"


def test_worker_never_writes_the_password(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise RuntimeError("login failed for s3cret-pw")
    monkeypatch.setattr(rgs.rpt, "run_sim", boom)
    res, work = _main(monkeypatch, tmp_path, {"ok": True, "sim_ns": 100, "config": {}, "program": None,
                                              "plays": [], "length_from": "timeline", "cannot_draw": None},
                      target={"local": {}, "saas": dict(CREDS)})
    assert not res["ok"]
    for f in work.iterdir():
        assert "s3cret-pw" not in f.read_text(encoding="utf-8"), f.name
    assert "***" in res["error"]


def test_worker_watchdog_writes_timeout_and_closes_the_instance(monkeypatch, tmp_path):
    closed = []

    class Inst:
        def close(self):
            closed.append(1)

    class Mod:
        instance = Inst()

    class Exit(Exception):
        pass

    def fake_exit(code):
        raise Exit(code)
    monkeypatch.setattr(rgs.os, "_exit", fake_exit)
    with pytest.raises(Exit):
        rgs._watchdog(0.05, tmp_path, [Mod()], threading.Event(), "")
    assert json.loads((tmp_path / "_result.json").read_text())["timed_out"] is True
    assert closed == [1]


# --------------------------------------------------------------------------- pure SM side

def test_target_of_names_the_limits():
    none = sim.target_of({"target": "saas", "opx": {"host": "h"}}, None)
    assert none[0] is None
    t, label, limit = sim.target_of({"target": "opx", "opx": {"host": "10.0.0.2"}}, None)
    assert t["local"]["host"] == "10.0.0.2" and limit == sim.LIMIT_LOCAL_S == 60
    t, label, limit = sim.target_of({"target": "saas", "opx": {}}, CREDS)
    assert t["saas"]["password"] and limit == sim.LIMIT_CLOUD_S == 240 and "QM-SaaS" in label


@pytest.mark.parametrize("st,res,state", [
    ({"state": "none"}, None, "none"),
    ({"state": "running", "phase": "connecting"}, None, "starting"),
    ({"state": "running", "phase": "simulating"}, None, "running"),
    ({"state": "done"}, {"ok": False, "timed_out": True}, "timeout"),
    ({"state": "done"}, {"ok": False, "stopped": True}, "stopped"),
    ({"state": "done"}, {"ok": False, "too_long": True, "sim_ns": 20100}, "too_long"),
    ({"state": "done"}, {"ok": False, "error_kind": "gate", "error": "KeyError: 'x'"}, "not_run"),
    ({"state": "done"}, {"ok": False, "error_kind": "connect", "error": "Could not reach x."}, "failed"),
    ({"state": "done"}, {"ok": True, "ports": []}, "failed"),
])
def test_every_state_has_its_line_and_no_invented_waveform(st, res, state):
    if res is not None:
        st = dict(st, result=res)
    out = sim.from_status(st, {"where": "w", "limit_s": 60})
    assert out["state"] == state
    assert out["line"] and " -- " not in out["line"] and "CAS" not in out["line"]
    assert "patch" not in out["line"].lower()
    assert "ports" not in out


def test_done_shapes_ports_and_drops_malformed():
    res = {"ok": True, "sim_ns": 504, "backend_label": "QM-SaaS cloud simulator", "ports": [
        {"controller": "con1", "fem": 5, "port": 1, "elements": ["q1.z"], "t0_ns": 0, "dt_ns": 0.5,
         "volts": [0, 0.1]},
        {"controller": "con1", "fem": "x", "port": 2, "volts": [1]},
        {"controller": "con1", "fem": 5, "port": 3, "volts": []}]}
    out = sim.from_status({"state": "done", "result": res}, {})
    assert out["state"] == "done" and out["line"] == "Simulated on QM-SaaS cloud simulator: 504 ns."
    assert out["ports"] == [{"key": "con1/5/1", "port_text": "con1 / 5 / 1", "controller": "con1",
                             "fem": 5, "port": 1, "elements": ["q1.z"], "kind": None, "t0_ns": 0.0,
                             "dt_ns": 0.5, "time_step_unknown": False, "volts": [0.0, 0.1],
                             "decimated": False, "cut_at_end": False}]
    assert "cut_note" not in out and "time_step_note" not in out
    res["ports"][0]["cut_at_end"] = True
    cut = sim.from_status({"state": "done", "result": res}, {})
    assert cut["cut_note"] == ("The simulation window ended while these ports were still playing: "
                               "con1 / 5 / 1.")


@pytest.mark.parametrize("dt", [None, "x", 0, float("nan")])
def test_done_names_ports_whose_time_step_is_unknown(dt):
    res = {"ok": True, "sim_ns": 504, "ports": [
        {"controller": "con1", "fem": 1, "port": 1, "dt_ns": dt, "volts": [0, 0.1]}]}
    out = sim.from_status({"state": "done", "result": res}, {})
    assert out["ports"][0]["dt_ns"] is None
    assert "not known" in out["time_step_note"] and "con1 / 1 / 1" in out["time_step_note"]


def test_static_lines():
    assert sim.answer("not_configured")["line"] == (
        "Not simulated: no simulator is set up. Set the OPX host or the QM-SaaS account in "
        "Settings, Port test run targets.")
    assert "20 us" in sim.line("too_long", sim_ns=20100)
    assert "60 s" in sim.line("timeout", limit_s=60)


class FakeProc:
    def __init__(self, argv, result, delay=0.0):
        self.pid = None
        self.argv = argv
        self.returncode = None
        self.stdin = self
        self.lines = []
        self.work = Path(argv[3])
        self._done = threading.Event()
        self.result = result

        def finish():
            time.sleep(delay)
            if (self.work / "stop").exists():
                self.result = {"ok": False, "stopped": True}
            (self.work / "_result.json").write_text(json.dumps(self.result), encoding="utf-8")
            self.returncode = 0
            self._done.set()
        threading.Thread(target=finish, daemon=True).start()

    def write(self, s):
        self.lines.append(s)

    def flush(self):
        pass

    def close(self):
        pass

    def poll(self):
        return self.returncode

    def kill(self):
        self.returncode = -9
        self._done.set()


def test_reap_asks_a_stop_past_the_limit(monkeypatch, tmp_path):
    monkeypatch.setattr(sim, "SM_SLACK_S", 0.0)
    m = sim.Manager(tmp_path)
    procs = []

    def popen_for(folder):
        def p(argv, **kw):
            procs.append(FakeProc(argv, {"ok": True}, delay=1.0))
            return procs[-1]
        return p
    removed = []
    copy = type("C", (), {"after": tmp_path / "after", "root_class": None})()
    ans, code = m.start("w1", python=sys.executable, target={"saas": CREDS}, label="L", limit_s=0.1,
                        make_copy=lambda: copy, remove_copy=removed.append, macro="m",
                        popen_for=popen_for)
    assert code == 200
    procs[0]._done.wait(5)
    n = 0
    while not removed and n < 50:
        n += 1
        time.sleep(0.1)
    assert (procs[0].work / "stop").exists()
    assert removed == [copy]


# --------------------------------------------------------------------------- routes

from tests.test_cz_gate_routes import App, _body  # noqa: E402


def _md5_tree(folder: Path) -> dict:
    return {str(p.relative_to(folder)): hashlib.md5(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob("*.json")) if p.is_file()}


@pytest.fixture
def app(monkeypatch):
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        a = App(tmp)
        a.tmp = Path(tmp)
        from quam_state_manager.web import cz_gate_routes as czr
        from quam_state_manager.web import port_test_routes as ptroutes
        monkeypatch.setattr(czr, "_env_python", lambda: sys.executable)
        monkeypatch.setattr(ptroutes, "_saas_credentials", lambda: dict(CREDS))
        a.procs = []
        a.result = {"ok": True, "sim_ns": 504, "backend_label": "QM-SaaS cloud simulator",
                    "ports": [{"controller": "con1", "fem": 1, "port": 1, "elements": ["q1.z"],
                               "t0_ns": 0, "dt_ns": 1.0, "volts": [0.0, 0.2, 0.0]}]}
        a.delay = 0.0

        def lab_popen(env):
            def p(argv, **kw):
                assert Path(env["QUAM_STATE_PATH"]).is_dir()
                a.env = env
                a.procs.append(FakeProc(argv, a.result, a.delay))
                return a.procs[-1]
            return p
        monkeypatch.setattr(sim, "lab_popen", lab_popen)
        yield a
        from quam_state_manager.core.hub_index import close_readers
        close_readers()


def _wait(c, rid, window="w1"):
    n = 0
    while n < 100:
        n += 1
        r = c.get(STATUS + f"?id={rid}", headers={"X-SM-Window-ID": window}).get_json()
        if r["state"] not in ("starting", "running"):
            return r
        time.sleep(0.05)
    return r


def test_start_runs_on_a_copy_and_writes_nothing(app):
    live = app.tmp / "chips" / "live"
    wc = app.tmp / "_inst" / "working_state"
    assert _md5_tree(wc) and _md5_tree(live)
    before_live, before_wc = _md5_tree(live), _md5_tree(wc)
    snap = app.snap()
    r = app.c.post(START, json=_body(), headers={"X-SM-Window-ID": "w1"})
    j = r.get_json()
    assert r.status_code == 200 and j["state"] == "starting", j
    job = json.loads(Path(app.procs[0].argv[2]).read_text(encoding="utf-8"))
    assert job["macro"] == "qubit_pairs.q1-2.macros.cz_sq"
    assert job["pad_ns"] == 200 and job["start_ns"] == 300 and job["max_ns"] == 20_000
    assert job["folder"] == app.env["QUAM_STATE_PATH"]
    assert Path(job["folder"]).resolve() != live.resolve()
    # the password rides stdin only, never the job file
    assert "s3cret-pw" not in json.dumps(job) and "s3cret-pw" in app.procs[0].lines[0]
    done = _wait(app.c, j["id"])
    assert done["state"] == "done" and done["ports"][0]["elements"] == ["q1.z"]
    assert _md5_tree(live) == before_live and _md5_tree(wc) == before_wc
    assert app.snap() == snap
    n = 0
    while Path(job["folder"]).exists() and n < 50:
        n += 1
        time.sleep(0.1)
    assert not Path(job["folder"]).exists()      # the copy is removed after the run


def test_one_run_per_window_and_stop(app):
    app.delay = 1.5
    h1, h2 = {"X-SM-Window-ID": "w1"}, {"X-SM-Window-ID": "w2"}
    r1 = app.c.post(START, json=_body(), headers=h1).get_json()
    busy = app.c.post(START, json=_body(), headers=h1)
    assert busy.status_code == 409 and busy.get_json()["state"] == "busy"
    other = app.c.post(START, json=_body(), headers=h2)
    assert other.status_code == 200
    s = app.c.post(STOP, json={"id": r1["id"]}, headers=h1).get_json()
    assert s["state"] == "running" and s["phase"] == "stopping"
    assert _wait(app.c, r1["id"])["state"] == "stopped"
    assert _wait(app.c, other.get_json()["id"], "w2")["state"] == "done"


def test_stop_body_goes_through_request_body(app):
    r = app.c.post(STOP, data="[1]", content_type="application/json",
                   headers={"X-SM-Window-ID": "w1"})
    assert r.status_code == 400 and "JSON object" in r.get_json()["error"]


def test_not_configured_and_no_env(app, monkeypatch):
    from quam_state_manager.web import cz_gate_routes as czr
    from quam_state_manager.web import port_test_routes as ptroutes
    monkeypatch.setattr(ptroutes, "_saas_credentials", lambda: None)
    r = app.c.post(START, json=_body())
    assert r.status_code == 200 and r.get_json()["state"] == "not_configured"
    assert r.get_json()["ok"] is False
    assert "Port test run targets" in r.get_json()["line"]
    monkeypatch.setattr(czr, "_env_python", lambda: None)
    r = app.c.post(START, json=_body())
    assert r.status_code == 200 and r.get_json()["state"] == "no_env"
    assert r.get_json()["ok"] is False
    assert r.get_json()["line"] == sim.answer("no_env")["line"]
    assert app.c.post(START, data="[1]", content_type="application/json").status_code == 400
    assert not app.procs


def test_bad_body_and_unknown_pair(app):
    r = app.c.post(START, data="[1]", content_type="application/json")
    assert r.status_code == 400
    r = app.c.post(START, json=dict(_body(), pair="q9-9"))
    assert r.status_code == 400 and r.get_json()["state"] == "not_run"
    assert not app.procs


def test_failed_run_reports_the_classified_reason(app):
    app.result = {"ok": False, "error_kind": "connect", "error": "Could not reach the QM-SaaS simulator at x."}
    j = app.c.post(START, json=_body(), headers={"X-SM-Window-ID": "w1"}).get_json()
    out = _wait(app.c, j["id"])
    assert out["state"] == "failed"
    assert out["line"] == "Not simulated: Could not reach the QM-SaaS simulator at x."
    assert "ports" not in out


def test_busy_window_refuses_before_making_a_copy(tmp_path):
    m = sim.Manager(tmp_path)
    m.runs("w1").busy = lambda: True

    def no_copy():
        raise AssertionError("a busy window must not copy the chip")
    ans, code = m.start("w1", python=sys.executable, target={"saas": CREDS}, label="L",
                        limit_s=1, make_copy=no_copy, remove_copy=lambda c: None, macro="m",
                        popen_for=lambda folder: None)
    assert code == 409 and ans["state"] == "busy"


@pytest.mark.parametrize("dt", [10**400, -10**400])
def test_time_step_too_large_for_a_float_is_unknown_and_the_entry_is_kept(dt):
    base = {"controller": "con1", "fem": 1, "port": 1, "volts": [0.1]}
    [p] = sim._shape_ports([dict(base, dt_ns=dt)])
    assert p["dt_ns"] is None and p["time_step_unknown"] is True and p["volts"] == [0.1]


def test_a_number_whose_float_conversion_fails_drops_its_entry():
    import numbers

    class Odd:
        def __float__(self):
            raise TypeError("no float")
    numbers.Real.register(Odd)
    base = {"controller": "con1", "fem": 1, "port": 1, "volts": [0.1]}
    for field, val in (("fem", Odd()), ("port", Odd()), ("t0_ns", Odd()), ("volts", [Odd()])):
        bad = dict(base, port=2)
        bad[field] = val
        assert [p["port"] for p in sim._shape_ports([bad, base])] == [1]


def test_a_whole_number_whose_int_conversion_fails_drops_its_entry():
    class NoInt(float):
        def __int__(self):
            raise TypeError("no int")
    base = {"controller": "con1", "fem": 1, "port": 1, "volts": [0.1]}
    for field in ("fem", "port"):
        bad = dict(base, port=2)
        bad[field] = NoInt(2.0)
        assert [p["port"] for p in sim._shape_ports([bad, base])] == [1]


def test_the_two_flags_are_set_only_by_a_stored_true():
    class NoBool:
        def __bool__(self):
            raise TypeError("no bool")
    base = {"controller": "con1", "fem": 1, "port": 1, "volts": [0.1]}
    for val in (NoBool(), [1, 2], 1, "yes", None, False):
        [p] = sim._shape_ports([dict(base, decimated=val, cut_at_end=val)])
        assert p["decimated"] is False and p["cut_at_end"] is False
    [p] = sim._shape_ports([dict(base, decimated=True, cut_at_end=True)])
    assert p["decimated"] is True and p["cut_at_end"] is True


def _delayed_run(second_latency=72, *, missing=False, shaped=False):
    # An arbitrary per-run latency; the real delay between ports is independent.
    ports, intended = [], []
    shape = [0, .025, .05, .075, .1, .1, .075, .05, .025, 0] if shaped else [.1] * 100
    for n, (delay, latency) in enumerate([(0, 72), (161, second_latency)], 1):
        intended.append({"element": "line" + str(n), "port": ["con", 1, n], "start_ns": delay,
                         "samples": shape, "sampling_rate": 1e9})
        if missing and n == 2:
            continue
        wave = [v for v in shape for _ in range(2)]
        ports.append({"controller": "con", "fem": 1, "port": n, "elements": ["line" + str(n)],
                      "kind": "lf", "dt_ns": .5, "t0_ns": 0,
                      "volts": [0.] * int((delay + latency) * 2) + wave + [0.] * 20})
    return {"state": "done", "result": {"ok": True, "sim_ns": 600, "ports": ports, "intended": intended}}


@pytest.mark.parametrize("shaped", [False, True])
def test_simulator_alignment_uses_one_latency_and_keeps_port_delays(shaped):
    out = sim.from_status(_delayed_run(shaped=shaped), {})
    assert out["start_latency_ns"] == 72
    assert "aligned by 72 ns (simulator start latency)" in out["note"]
    assert [p["t0_ns"] for p in out["ports"]] == [-72, -72]
    starts = [p["t0_ns"] + next(i for i,v in enumerate(p["volts"]) if v) * p["dt_ns"] for p in out["ports"]]
    assert starts[1] - starts[0] == 161


@pytest.mark.parametrize("kwargs", [{"second_latency": 75}, {"missing": True}])
def test_simulator_disagreement_or_missing_output_keeps_raw_axis(kwargs):
    out = sim.from_status(_delayed_run(**kwargs), {})
    assert out["start_latency_ns"] is None
    assert all(p["t0_ns"] == 0 for p in out["ports"])
    assert "raw simulator time axis" in out["note"] and "start at 0 ns" in out["note"]


def test_runner_preserves_intended_edges_from_its_config():
    ops = [_tl_op("line1", 100, 0), _tl_op("line2", 100, 161, port=("con1", 1, 2))]
    rows, _ = _tl_rows(ops)
    intended = rgs._intended(rows, ops)
    assert [r["start_ns"] for r in intended] == [0, 161]
    assert [r["constant"] for r in intended] == [.1, .1]


def test_simulator_one_sample_slack_does_not_fit_each_lane_separately():
    out = sim.from_status(_delayed_run(second_latency=72.5), {})
    assert out["start_latency_ns"] == 72
    assert [p["t0_ns"] for p in out["ports"]] == [-72, -72]
    starts = [p["t0_ns"] + next(i for i,v in enumerate(p["volts"]) if v) * p["dt_ns"] for p in out["ports"]]
    assert starts[1] - starts[0] == 161.5


@pytest.mark.parametrize("shaped", [False, True])
def test_simulator_offset_lane_aligns_above_idle(shaped):
    run = _delayed_run(shaped=shaped)
    for port in run["result"]["ports"]:
        port["volts"] = [v + .2 for v in port["volts"]]
    out = sim.from_status(run, {})
    assert out["start_latency_ns"] == 72
    assert [p["t0_ns"] for p in out["ports"]] == [-72, -72]


def test_simulator_early_edges_keep_raw_axis():
    run = _delayed_run()
    for row in run["result"]["intended"]:
        row["start_ns"] += 80
    out = sim.from_status(run, {})
    assert out["start_latency_ns"] is None
    assert [p["t0_ns"] for p in out["ports"]] == [0, 0]
    assert "raw simulator time axis" in out["note"]
