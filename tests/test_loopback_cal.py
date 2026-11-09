"""Hardware-free pins for loopback math, config, acquisition and subprocess API."""

from copy import deepcopy
import json
from types import SimpleNamespace
import sys

import numpy as np
import pytest

from quam_state_manager.core import loopback_cal as cal
from quam_state_manager.generator import run_loopback_cal as runner


def controllers():
    block = {"type": "LF", "analog_outputs": {
        "3": {"offset": 0.012, "output_mode": "direct", "sampling_rate": 1e9,
              "upsampling_mode": "pulse", "filter": {"feedforward": [1.0, 0.01]}},
        "4": {"offset": 0.123, "delay": 32}},
        "analog_inputs": {"1": {"offset": 0.045, "gain_db": 6, "sampling_rate": 2e9},
                          "2": {"offset": 0.067, "gain_db": 3}},
        "digital_outputs": {"1": {"inverted": True}}, "crosstalk": {"3": {"4": 0.01}}}
    return {"con1": {"type": "opx1000", "fems": {"5": block, "6": deepcopy(block)}},
            "con2": {"type": "opx1000", "fems": {"5": deepcopy(block)}}}


def job(kind="output"):
    return dict(host="simulator.invalid", cluster_name=None, controllers=controllers(),
                con="con1", fem=5, ao=3, ai=1, ao_offset=0.012, ai_offset=0.045,
                output_mode="direct", kind=kind, n_avg=100)


@pytest.mark.parametrize("slope", [-1.7, 2.3])
def test_linear_convergence_and_sign(slope):
    result = cal.calibrate(lambda offset: slope * offset + 0.238, 0.04, 0.5, log=lambda _: None)
    assert result["converged"] is True
    assert result["slope"] == pytest.approx(slope)
    assert result["final_offset"] == pytest.approx(-0.238 / slope, abs=0.0005 / abs(slope))
    assert abs(result["history"][-1]["dc_mV"]) <= 0.5
    assert result["iters"] == len(result["history"])
    assert result["history"][-1]["offset"] == result["final_offset"]


def test_step_bound():
    result = cal.calibrate(lambda offset: offset + 1, 0, 2.5, max_iters=4,
                           max_step_v=0.037, log=lambda _: None)
    offsets = [row["offset"] for row in result["history"]] + [result["final_offset"]]
    assert np.diff(offsets) == pytest.approx([-0.037] * 4)


@pytest.mark.parametrize("mode,limit", [("direct", 0.5), ("amplified", 2.5)])
@pytest.mark.parametrize("sign", [-1, 1])
def test_mode_clamp(mode, limit, sign):
    seen = []
    def measure(offset):
        seen.append(offset)
        return offset - sign * 20
    assert cal.mode_limit(mode) == limit
    result = cal.calibrate(measure, sign * (limit - 0.01), cal.mode_limit(mode), log=lambda _: None)
    assert result["final_offset"] == sign * limit
    assert all(-limit <= value <= limit for value in seen)
    assert result["converged"] is False
    # At the edge the reference still divides by 2*step, not the clipped span.
    assert result["slope"] == pytest.approx(0.6)


def test_nonconverging_and_slope_fallback():
    result = cal.calibrate(lambda _: 0.2, 0, 0.5, log=lambda _: None)
    assert result["converged"] is False
    assert result["iters"] == 6
    assert result["slope"] == 1.0
    assert result["final_offset"] == -0.5


def test_tolerance_and_custom_slope_step():
    seen = []
    result = cal.calibrate(lambda x: seen.append(x) or 0.0005, 0.1, 0.5,
                           slope_step_v=0.02, log=lambda _: None)
    assert seen == pytest.approx([0.08, 0.12, 0.1])
    assert result["converged"] and result["iters"] == 1
    assert result["final_offset"] == 0.1


@pytest.mark.parametrize("stop_after", [0, 1, 2, 3])
def test_stop_between_measurements(stop_after):
    seen = []
    result = cal.calibrate(lambda x: seen.append(x) or x + 1, 0, 0.5,
                           should_stop=lambda: len(seen) >= stop_after, log=lambda _: None)
    assert len(seen) == stop_after
    assert result["converged"] is False
    assert result["iters"] == max(0, stop_after - 2)


def test_config_preserves_other_ports_and_caller():
    original = controllers()
    before = deepcopy(original)
    config = cal.build_loopback_config(original, "con1", 5, 3, 1, -0.2, 0.15, "amplified")
    expected = deepcopy(before)
    block = expected["con1"]["fems"]["5"]
    block["analog_outputs"]["3"].update(offset=-0.2, output_mode="amplified")
    block["analog_inputs"]["1"].update(offset=0.15, gain_db=0, sampling_rate=1e9)
    assert config["controllers"] == expected
    assert original == before
    config["controllers"]["con1"]["fems"]["5"]["analog_outputs"]["3"]["filter"]["feedforward"][0] = 9
    assert original == before


def test_reference_config():
    cfg = runner.config_for_offset(job(), -0.2)
    assert cfg["version"] == 1
    assert cfg["elements"] == {"sm_loopback": {
        "singleInput": {"port": ("con1", 5, 3)}, "intermediate_frequency": 0.0,
        "operations": {"readout": "readout_pulse"}, "outputs": {"out1": ("con1", 5, 1)},
        "time_of_flight": 28, "smearing": 0}}
    names = {"cos": "cosine_weights", "sin": "sine_weights", "minus_sin": "minus_sine_weights",
             "rotated_cos": "rotated_cosine_weights", "rotated_sin": "rotated_sine_weights",
             "rotated_minus_sin": "rotated_minus_sine_weights", "opt_cos": "opt_cosine_weights",
             "opt_sin": "opt_sine_weights", "opt_minus_sin": "opt_minus_sine_weights"}
    assert cfg["pulses"] == {"readout_pulse": {
        "operation": "measurement", "length": 1728, "waveforms": {"single": "readout_wf"},
        "integration_weights": names, "digital_marker": "ON"}}
    assert cfg["waveforms"] == {"readout_wf": {"type": "constant", "sample": 0.0}}
    assert cfg["digital_waveforms"] == {"ON": {"samples": [(1, 0)]}}
    expected = {}
    for name in ("cosine_weights", "rotated_cosine_weights", "opt_cosine_weights"):
        expected[name] = {"cosine": [(1.0, 5000)], "sine": [(0.0, 5000)]}
    for name in ("sine_weights", "rotated_sine_weights", "opt_sine_weights"):
        expected[name] = {"cosine": [(0.0, 5000)], "sine": [(1.0, 5000)]}
    for name in ("minus_sine_weights", "rotated_minus_sine_weights", "opt_minus_sine_weights"):
        expected[name] = {"cosine": [(0.0, 5000)], "sine": [(-1.0, 5000)]}
    assert cfg["integration_weights"] == expected


def test_integer_keys_and_ai2():
    req = job()
    req["ai"] = 2
    block = req["controllers"]["con1"]["fems"].pop("5")
    req["controllers"]["con1"]["fems"][5] = block
    for key in ("analog_outputs", "analog_inputs"):
        block[key] = {int(k): v for k, v in block[key].items()}
    cfg = runner.config_for_offset(req, 0.1)
    assert cfg["elements"]["sm_loopback"]["outputs"]["out1"] == ("con1", 5, 2)
    assert cfg["controllers"]["con1"]["fems"][5]["analog_inputs"][2]["offset"] == 0.045


@pytest.mark.parametrize("kind", ["input", "output"])
def test_kind_moves_correct_offset(kind):
    req = job(kind)
    before = deepcopy(req)
    seen = []
    def measure(cfg):
        block = cfg["controllers"]["con1"]["fems"]["5"]
        ao, ai = block["analog_outputs"]["3"]["offset"], block["analog_inputs"]["1"]["offset"]
        seen.append((ao, ai))
        assert (ao if kind == "input" else ai) == (0.012 if kind == "input" else 0.045)
        return -(ai if kind == "input" else ao) + 0.18
    result = runner.run_calibration(req, measure, log=lambda _: None)
    initial = 0.045 if kind == "input" else 0.012
    axis = 1 if kind == "input" else 0
    assert seen[0][axis] == pytest.approx(initial - 0.05)
    assert seen[1][axis] == pytest.approx(initial + 0.05)
    assert result["converged"] and result["final_offset"] == pytest.approx(0.18)
    assert req == before


def test_invalid_kind():
    with pytest.raises(ValueError, match="kind"):
        runner.run_calibration(job("invalid"), lambda _: 0)


@pytest.mark.parametrize("failure", [None, "execute", "fetch", "stop"])
def test_measurement_python_conversion_and_cleanup(failure):
    events = []
    def fetch():
        if failure == "fetch":
            raise RuntimeError("fetch")
        return np.array([1 + 4j, 3 - 8j])
    handle = SimpleNamespace(fetch_all=fetch)
    handles = SimpleNamespace(is_processing=lambda: False, wait_for_all_values=lambda: True, get=lambda name: handle if name == "adc1" else None)
    running = SimpleNamespace(result_handles=handles, halt=lambda: events.append("halt"))
    def execute(program):
        assert program == "program"
        events.append("execute")
        if failure == "execute":
            raise RuntimeError("execute")
        return running
    machine = SimpleNamespace(execute=execute, close=lambda: events.append("close"))
    def open_qm(config, **kwargs):
        assert kwargs == {}
        events.append("open")
        return machine
    qmm = SimpleNamespace(open_qm=open_qm)
    def measure():
        return runner.measure_dc(qmm, {}, "program", lambda x: x * 0.01,
                                 lambda: failure == "stop" and "execute" in events, np)
    if failure:
        with pytest.raises(InterruptedError if failure == "stop" else RuntimeError):
            measure()
    else:
        assert measure() == pytest.approx(0.02)
    assert events[-1] == "close"
    assert ("halt" in events) == (failure == "stop")


def test_wait_polls_then_blocks_and_honours_stop():
    states, calls = [True, True, False], []
    handles = SimpleNamespace(is_processing=lambda: states.pop(0),
                              wait_for_all_values=lambda: calls.append("wait"))
    runner.wait_for_results(handles, lambda: False, sleep=lambda _: calls.append("sleep"))
    assert calls == ["sleep", "sleep", "wait"]
    with pytest.raises(InterruptedError):
        polls = []
        def busy():
            polls.append(1)
            if len(polls) > 5:
                raise AssertionError("stop was ignored")
            return True
        runner.wait_for_results(SimpleNamespace(is_processing=busy,
                                                wait_for_all_values=lambda: None),
                                lambda: True, sleep=lambda _: None)


@pytest.mark.parametrize("ai", [1, 2])
def test_qua_program(ai):
    # Real SDK builds the program locally; no QMM is constructed.
    qua = pytest.importorskip("qm.qua")
    program = runner.build_program(qua, ai, 100)
    text = str(program.qua_program)
    assert "sm_loopback" in text and "readout" in text
    assert "adc1" in text and "adc1_single_run" in text
    from google.protobuf.json_format import MessageToDict

    message = MessageToDict(program.qua_program)
    loop = message["script"]["body"]["statements"][0]["for"]
    assert loop["condition"]["binaryOperation"]["right"]["literal"]["value"] == "100"
    statements = loop["body"]["statements"]
    assert statements[0]["resetPhase"]["qe"]["name"] == "sm_loopback"
    assert statements[1]["measure"]["streamAs"]
    models = message["resultAnalysis"]["model"]
    assert models[0][:2] == ["save", "adc1"]
    assert models[0][2][:1] == ["average"]
    assert models[0][2][1][:1] == ["@macro_adc_trace"]
    assert models[0][2][1][1][:2] == ["@macro_input", str(ai)]
    assert models[1][:2] == ["save", "adc1_single_run"]
    assert models[1][2][1][:2] == ["@macro_input", str(ai)]


@pytest.mark.parametrize("simulate", [False, True])
def test_main_protocol_and_simulation(tmp_path, monkeypatch, capsys, simulate):
    request = job()
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request), encoding="utf-8")
    calls = []
    simulation = SimpleNamespace(result_handles=SimpleNamespace(is_processing=lambda: False, wait_for_all_values=lambda: True),
                                 halt=lambda: calls.append("halt"))
    class QMM:
        def __init__(self, **kwargs):
            calls.append(kwargs)
        def simulate(self, config, program, options):
            calls.append("simulate")
            return simulation
    fake_qm = SimpleNamespace(QuantumMachinesManager=QMM, SimulationConfig=lambda **kw: kw, qua=object())
    monkeypatch.setitem(sys.modules, "qm", fake_qm)
    monkeypatch.setitem(sys.modules, "qualang_tools.units",
                        SimpleNamespace(unit=lambda: SimpleNamespace(raw2volts=lambda x: x)))
    monkeypatch.setattr(runner, "build_program", lambda *args: "program")
    monkeypatch.setattr(runner, "measure_dc", lambda *args: 0.0)
    code = runner.main([str(path)] + (["--simulate"] if simulate else []))
    assert code == 0
    result = json.loads(path.with_suffix(".result.json").read_text())
    events = [json.loads(line.removeprefix(runner.PREFIX)) for line in capsys.readouterr().out.splitlines()]
    assert events[-1] == result
    assert result["ok"] is True
    if simulate:
        assert calls[1:] == ["simulate", "halt"] and result["simulated"] is True
    else:
        assert result["converged"] and any(event["event"] == "progress" for event in events)


def test_main_stop_file_and_result_override(tmp_path, capsys):
    path = tmp_path / "request.json"
    path.write_text(json.dumps({**job(), "stop_file": "stop", "result_file": "answer.json"}))
    (tmp_path / "stop").touch()
    assert runner.main([str(path)]) == 2
    result = json.loads((tmp_path / "answer.json").read_text())
    assert result["stopped"] and result["ok"] is False
    assert capsys.readouterr().out.startswith("@@SM-LOOPCAL@@")


def test_readout_pulse_is_a_measurement_pulse_like_the_exported_script():
    """measure() needs a measurement pulse; the in-app runner and the exported
    script must use the same operation."""
    from quam_state_manager.core.loopback_script import render_script
    cfg = runner.config_for_offset(job(), 0.0)
    assert cfg["pulses"]["readout_pulse"]["operation"] == "measurement"
    assert '"operation": "measurement"' in render_script({})
