"""Pins for core/loopback_script.render_script (the exported LF-FEM offset calibration script)."""

import csv
import re
import sys
import types

import numpy as np
import pytest

from quam_state_manager.core import loopback_script as ls

ALT = dict(con="con2", fem=7, ao_port=8, ai_port=2, output_mode="amplified", ai_gain_db=6,
           qop_ip="10.0.0.5", cluster_name="cl_x", n_avg=17, tol_mV=0.25, max_iters=9,
           max_step_V=0.2, slope_step_V=0.03, time_of_flight=32, readout_len=1000,
           ao_offset_start=0.01, ai_offset_start=-0.002)


def _split(text):
    a = text.index(ls.SETTINGS_BEGIN)
    b = text.index(ls.SETTINGS_END)
    return text[:a], text[a:b], text[b:]


@pytest.mark.parametrize("params", [{}, ALT, dict(fem=5, ao_port=7, ai_port=1),
                                    dict(other_controllers={"con1": {"type": "opx1000", "fems": {
                                        1: {"type": "LF", "analog_outputs": {"3": {"offset": 0.1}}}}}})])
def test_compiles(params):
    compile(ls.render_script(params), "<rendered>", "exec")


def test_settings_block_holds_every_value():
    _, settings, _ = _split(ls.render_script(ALT))
    for name, val in [("CON", "con2"), ("FEM", 7), ("AO_PORT", 8), ("AI_PORT", 2),
                      ("OUTPUT_MODE", "amplified"), ("AI_GAIN_DB", 6), ("QOP_IP", "10.0.0.5"),
                      ("CLUSTER_NAME", "cl_x"), ("QOP_PORT", None), ("N_AVG", 17), ("TOL_MV", 0.25),
                      ("MAX_ITERS", 9), ("MAX_STEP_V", 0.2), ("SLOPE_STEP_V", 0.03),
                      ("TIME_OF_FLIGHT", 32), ("READOUT_LEN", 1000), ("AO_OFFSET_START", 0.01),
                      ("AI_OFFSET_START", -0.002), ("AI_OFFSET", -0.002), ("RUN_INPUT_CAL", True),
                      ("RUN_OUTPUT_CAL", True), ("OTHER_CONTROLLERS", {})]:
        assert re.search(rf"^{name} = {re.escape(repr(val))}(\s|$)", settings, re.M), name


def test_nothing_below_settings_depends_on_params():
    h1, _, b1 = _split(ls.render_script({}))
    h2, _, b2 = _split(ls.render_script(ALT))
    assert (h1, b1) == (h2, b2)
    assert "con2" not in b2 and "amplified\"" not in b2.replace('"amplified")', "")


def test_ai2_selects_input2():
    ns = _run_fake(dict(ALT, ai_port=2, pause_for_cable=False))
    assert ns["__fake"].selected == "input2"
    ns = _run_fake(dict(ALT, ai_port=1, pause_for_cable=False))
    assert ns["__fake"].selected == "input1"


@pytest.mark.parametrize("bad", [dict(ai_port=3), dict(fem=0), dict(ao_port=9), dict(output_mode="hi"),
                                 dict(n_avg=0), dict(fem="5"), dict(fem=True), dict(readout_len=1001),
                                 dict(time_of_flight=20), dict(tol_mV=0), dict(ao_offset_start=0.6),
                                 dict(con=""), dict(bogus=1), dict(run_input_cal="yes"),
                                 dict(other_controllers=[1]), dict(max_step_V=float("nan"))])
def test_bad_params_raise(bad):
    with pytest.raises(ValueError):
        ls.render_script(bad)


def test_amplified_allows_larger_start():
    ls.render_script(dict(output_mode="amplified", ao_offset_start=2.0))


# ---------- fake qm / qualang_tools ----------
class _Var:
    def __lt__(self, o): return self
    def __add__(self, o): return self


class _Ctx:
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _Stream:
    def __init__(self, fake): self.fake = fake
    def input1(self): self.fake.selected = "input1"; return self
    def input2(self): self.fake.selected = "input2"; return self
    def average(self): return self
    def save(self, name): return self


class _Fake:
    # synthetic port: dc = S_IN*ai + B_IN (+ S_OUT*ao + B_OUT with the loopback in)
    S_IN, B_IN, S_OUT, B_OUT = -1.0, 0.0123, -0.9, -0.0456

    def __init__(self, ai_port):
        self.selected = None
        self.loopback = False
        self.ai_port = ai_port
        self.runs = 0

    def dc(self, cfg):
        (con, fem, ao), = [cfg["elements"]["lf_offset_probe"]["singleInput"]["port"]]
        _, _, ai = cfg["elements"]["lf_offset_probe"]["outputs"]["out1"]
        assert ai == self.ai_port
        f = cfg["controllers"][con]["fems"][fem]
        v = self.S_IN * f["analog_inputs"][str(ai)]["offset"] + self.B_IN
        if self.loopback:
            v += self.S_OUT * f["analog_outputs"][str(ao)]["offset"] + self.B_OUT
        return v


def _install(fake, monkeypatch):
    qm = types.ModuleType("qm")
    qua = types.ModuleType("qm.qua")

    class QMM:
        def __init__(self, **kw): pass
        def close(self): pass
        def open_qm(self, cfg):
            fake.runs += 1
            val = fake.dc(cfg)
            handle = types.SimpleNamespace(fetch_all=lambda: np.full(16, val))
            res = types.SimpleNamespace(wait_for_all_values=lambda: None, get=lambda k: handle)
            job = types.SimpleNamespace(result_handles=res)
            return types.SimpleNamespace(execute=lambda p: job, close=lambda: None)

    qm.QuantumMachinesManager = QMM
    qua.program = _Ctx
    qua.for_ = lambda *a: _Ctx()
    qua.stream_processing = _Ctx
    qua.declare = lambda *a, **k: _Var()
    qua.declare_stream = lambda **k: _Stream(fake)
    qua.reset_if_phase = lambda *a: None
    qua.measure = lambda *a, **k: None
    qua.__all__ = ["program", "for_", "stream_processing", "declare", "declare_stream",
                   "reset_if_phase", "measure"]
    qt = types.ModuleType("qualang_tools")
    units = types.ModuleType("qualang_tools.units")

    class unit:
        def __init__(self, **k): pass
        def raw2volts(self, x): return np.asarray(x)

    units.unit = unit
    for name, mod in [("qm", qm), ("qm.qua", qua), ("qualang_tools", qt), ("qualang_tools.units", units)]:
        monkeypatch.setitem(sys.modules, name, mod)


@pytest.fixture
def run_fake(monkeypatch, tmp_path):
    def go(params):
        v = ls._validate(params)
        fake = _Fake(v["ai_port"])
        _install(fake, monkeypatch)

        def _enter(prompt=""):
            fake.loopback = True
            return ""
        monkeypatch.setattr("builtins.input", _enter)
        path = tmp_path / "cal.py"
        path.write_text(ls.render_script(params), encoding="utf-8")
        ns = {"__file__": str(path), "__name__": "__main__"}
        if not v["pause_for_cable"]:
            fake.loopback = False
        exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), ns)
        ns["__fake"] = fake
        ns["__csv"] = tmp_path / "lf_offsets.csv"
        return ns
    return go


_run_fake = None


@pytest.fixture(autouse=True)
def _bind(run_fake):
    global _run_fake
    _run_fake = run_fake


def test_converges_and_writes_csv():
    ns = _run_fake(dict(ALT))
    f = ns["__fake"]
    ai_true = -f.B_IN / f.S_IN
    ai = ns["final_ai"]
    ao_true = -(f.S_IN * ai + f.B_IN + f.B_OUT) / f.S_OUT
    assert ns["ai_result"]["converged"] and ns["ao_result"]["converged"]
    assert abs(ai - ai_true) * abs(f.S_IN) * 1e3 <= ALT["tol_mV"]
    assert abs(ns["final_ao"] - ao_true) * abs(f.S_OUT) * 1e3 <= ALT["tol_mV"] + 1e-9
    rows = list(csv.reader(ns["__csv"].open(encoding="utf-8")))
    assert rows[0][0] == "timestamp" and len(rows) == 2
    r = rows[1]
    assert r[1:4] == ["con2", "7", "8"] and r[5] == "2" and r[7] == "amplified" and r[8] == "6"
    assert float(r[4]) == pytest.approx(ns["final_ao"], abs=1e-6)
    assert float(r[6]) == pytest.approx(ai, abs=1e-6)
    assert r[9:] == ["True", "True"]
    assert sorted(p.name for p in ns["__csv"].parent.iterdir()) == ["cal.py", "lf_offsets.csv"]


def test_skip_input_uses_ai_offset():
    ns = _run_fake(dict(ALT, run_input_cal=False, ai_offset=0.004))
    assert ns["ai_result"] is None and ns["final_ai"] == 0.004
    f = ns["__fake"]
    ao_true = -(f.S_IN * 0.004 + f.B_IN + f.B_OUT) / f.S_OUT
    assert abs(ns["final_ao"] - ao_true) * abs(f.S_OUT) * 1e3 <= ALT["tol_mV"] + 1e-9


def test_offset_clamped_to_mode_limit():
    # a port whose zero lies past the direct-mode limit: output offset must stop at 0.5 V
    ns_cls = _Fake
    old = ns_cls.B_OUT
    ns_cls.B_OUT = 0.9
    try:
        ns = _run_fake(dict(output_mode="direct", max_iters=40, pause_for_cable=True))
    finally:
        ns_cls.B_OUT = old
    assert ns["final_ao"] == pytest.approx(0.5) and not ns["ao_result"]["converged"]


def test_real_qua_program_builds():
    qua = pytest.importorskip("qm.qua")
    text = ls.render_script(ALT)
    block = text[text.index("with program() as prog:"):text.index("# =========================\n# Measurement")]
    ns = {"N_AVG": 5, "ELEM": "e", "AI_PORT": 2}
    exec("from qm.qua import *\n" + block, ns)
    assert ns["prog"] is not None
