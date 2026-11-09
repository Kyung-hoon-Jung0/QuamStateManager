"""Subprocess API: python run_loopback_cal.py JOB.json [--simulate].

Job fields: host, port (optional), cluster_name, controllers, con, fem, ao, ai, kind (input or
output), ai_offset, ao_offset, output_mode, n_avg (default 100). Optional
result_file/stop_file paths resolve relative to JOB.json; defaults are
JOB.result.json and JOB.stop. CLI --result-file/--stop-file override them.
Progress is ``@@SM-LOOPCAL@@`` followed by one JSON object per line; the final
event and atomic result file contain the same object. Exit codes: 0 success
(including nonconvergence), 1 error, 2 stopped. No state is saved automatically.
Input calibration requires an open input; output calibration requires loopback
and an already calibrated ai_offset. The returned offset is used as is.

--simulate submits exactly one program to the QMM simulator and never opens a
hardware QM. It requires a reachable simulator endpoint and installed QM SDK.
All imports at module load are standard library only.
"""

import argparse
from contextlib import redirect_stdout
import json
from pathlib import Path
import sys


PREFIX = "@@SM-LOOPCAL@@"


def config_for_offset(job, offset):
    """Build one measurement config, moving only the offset selected by kind."""
    from quam_state_manager.core.loopback_cal import build_loopback_config

    if job["kind"] not in ("input", "output"):
        raise ValueError("kind must be input or output")
    return build_loopback_config(
        job["controllers"], job["con"], job["fem"], job["ao"], job["ai"],
        offset if job["kind"] == "output" else job["ao_offset"],
        offset if job["kind"] == "input" else job["ai_offset"], job["output_mode"],
    )


def build_program(qua, ai, n_avg):
    """Build reference ADC acquisition without arithmetic on ADC streams."""
    if int(ai) not in (1, 2) or int(n_avg) < 1:
        raise ValueError("Expected AI 1 or 2 and positive n_avg")
    with qua.program() as prog:
        n = qua.declare(int)
        trace = qua.declare_stream(adc_trace=True)
        with qua.for_(n, 0, n < int(n_avg), n + 1):
            qua.reset_if_phase("sm_loopback")
            qua.measure("readout", "sm_loopback", adc_stream=trace)
        with qua.stream_processing():
            channel = trace.input1 if int(ai) == 1 else trace.input2
            channel().average().save("adc1")
            channel().save("adc1_single_run")
    return prog


def wait_for_results(handles, should_stop, sleep=None):
    """Poll ``is_processing`` so Stop is honoured, then do the reference blocking wait."""
    import time
    sleep = sleep or time.sleep
    while handles.is_processing():
        if should_stop():
            raise InterruptedError("Stopped while waiting for results")
        sleep(0.1)
    handles.wait_for_all_values()


def measure_dc(qmm, config, program, raw2volts, should_stop, np):
    """Open/close one QM per measurement, including execute/fetch failures."""
    if should_stop():
        raise InterruptedError("Stopped before measurement")
    machine = qmm.open_qm(config)  # as the reference: open_qm(cfg)
    running = None
    try:
        running = machine.execute(program)
        handles = running.result_handles
        wait_for_results(handles, should_stop)
        if should_stop():
            raise InterruptedError("Stopped before fetching trace")
        return float(np.mean(np.real(raw2volts(handles.get("adc1").fetch_all()))))
    finally:
        try:
            if running is not None and should_stop():
                running.halt()
        finally:
            machine.close()


def run_calibration(job, measure, should_stop=lambda: False, log=print):
    """Hardware-independent job adapter; measure receives a fresh QUA config."""
    from quam_state_manager.core.loopback_cal import calibrate, mode_limit

    initial = job["ai_offset"] if job["kind"] == "input" else job["ao_offset"]
    # Validate kind/target even if already stopped.
    config_for_offset(job, initial)
    return calibrate(lambda offset: measure(config_for_offset(job, offset)), initial,
                     mode_limit(job["output_mode"]), should_stop=should_stop, log=log)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_file", type=Path)
    parser.add_argument("--simulate", action="store_true")
    parser.add_argument("--result-file", type=Path)
    parser.add_argument("--stop-file", type=Path)
    args = parser.parse_args(argv)
    job_path = args.job_file.resolve()
    result_path = job_path.with_suffix(".result.json")
    stdout = sys.stdout

    def emit(event):
        print(PREFIX + json.dumps(event, allow_nan=False), file=stdout, flush=True)

    try:
        job = json.loads(job_path.read_text(encoding="utf-8-sig"))
        def resolve(value):
            path = Path(value)
            return path if path.is_absolute() else job_path.parent / path
        result_path = resolve(args.result_file or job.get("result_file", result_path))
        stop_path = resolve(args.stop_file or job.get("stop_file", job_path.with_suffix(".stop")))
        should_stop = stop_path.exists
        if should_stop():
            raise InterruptedError("Stopped before connecting")
        # Direct script execution must work without an editable installation.
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        # SDK diagnostics go to stderr, leaving stdout a JSON-line protocol.
        with redirect_stdout(sys.stderr):
            import numpy as np
            from qm import QuantumMachinesManager, SimulationConfig
            from qm import qua
            from qualang_tools.units import unit

            initial = job["ai_offset"] if job["kind"] == "input" else job["ao_offset"]
            config = config_for_offset(job, initial)
            program = build_program(qua, job["ai"], job.get("n_avg", 100))
            qmm = QuantumMachinesManager(host=job["host"], port=job.get("port"),
                                         cluster_name=job.get("cluster_name"))
            if should_stop():
                raise InterruptedError("Stopped before running")
            if args.simulate:
                simulated = qmm.simulate(config, program, SimulationConfig(duration=1000))
                try:
                    wait_for_results(simulated.result_handles, should_stop)
                    if should_stop():
                        raise InterruptedError("Stopped after simulation")
                    result = {"ok": True, "simulated": True, "kind": job["kind"]}
                finally:
                    try:
                        simulated.halt()
                    except Exception:
                        pass
            else:
                result = run_calibration(
                    job, lambda cfg: measure_dc(qmm, cfg, program, unit().raw2volts, should_stop, np),
                    should_stop, lambda message: emit({"event": "progress", "message": message}),
                )
                result.update(ok=True, kind=job["kind"], stopped=should_stop())
        code = 2 if result.get("stopped") else 0
    except InterruptedError as exc:
        result, code = {"ok": False, "stopped": True, "error": str(exc)}, 2
    except Exception as exc:
        result, code = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 1
    result["event"] = "result"
    temporary = result_path.with_suffix(result_path.suffix + ".tmp")
    temporary.write_text(json.dumps(result, allow_nan=False), encoding="utf-8")
    temporary.replace(result_path)
    emit(result)
    return code


if __name__ == "__main__":
    sys.exit(main())
