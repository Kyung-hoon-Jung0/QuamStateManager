"""Run one port-test script in the chip's env (spawned by core/port_test_runner.py).

    python run_port_test.py SCRIPT WORK_DIR             (request JSON on stdin)
    python run_port_test.py SCRIPT WORK_DIR --recover   (halt + close OUR QM)

The request (the first stdin line, never on disk) is ``{"mode": "saas"|"opx",
"target": {...}, "max_s": float, "sim_ns": int}``; it carries the credentials,
so it is read once and never written or printed. SM keeps stdin OPEN for the
run's lifetime: a later ``stop`` line, or end-of-file (SM exited or was
killed), asks the run to halt and close its QM -- so a run never outlives SM.
A file ``WORK_DIR/stop`` asks the same. Progress, and as soon as they are
known the QM id and job id, go to ``WORK_DIR/_status.json``; the answer to
``WORK_DIR/_result.json``. ``--recover`` reconnects to the recorded QM only
(never another one) and halts + closes it. The script module is the same text
the user downloads.
"""
from __future__ import annotations

import importlib.util
import json
import os
import threading
import sys
import time
import traceback
from pathlib import Path

MAX_POINTS = 20000


def _write(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, default=str), encoding="utf-8")
    os.replace(tmp, path)


def _load(script: Path):
    spec = importlib.util.spec_from_file_location("port_test_script", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _traces(samples) -> dict:
    """Every simulated analog/digital port as decimated real (and imag) arrays."""
    import numpy as np
    out = {}
    for cname in dir(samples):
        if cname.startswith("_"):
            continue
        con = getattr(samples, cname, None)
        analog = getattr(con, "analog", None)
        if not isinstance(analog, dict):
            continue
        for key, arr in analog.items():
            z = np.asarray(arr)
            step = max(1, len(z) // MAX_POINTS)
            row = {"peak": float(np.max(np.abs(z))) if len(z) else 0.0, "n": int(len(z)),
                   "step": step, "re": np.real(z[::step]).round(6).tolist()}
            if np.iscomplexobj(z):
                row["im"] = np.imag(z[::step]).round(6).tolist()
            rate = getattr(con, "analog_sampling_rate", None)
            if isinstance(rate, dict):
                row["rate"] = rate.get(key)
            out[f"{cname}:{key}"] = row
        digital = getattr(con, "digital", None)
        if isinstance(digital, dict):
            for key, arr in digital.items():
                d = np.asarray(arr).astype(float)
                step = max(1, len(d) // MAX_POINTS)
                out[f"{cname}:dig:{key}"] = {"peak": float(d.max()) if len(d) else 0.0, "n": int(len(d)),
                                             "step": step, "re": d[::step].tolist(), "digital": True}
    return out


def watch_stdin(stream, stop: threading.Event) -> threading.Thread:
    """A ``stop`` line or end-of-file on stdin (the parent SM is gone) sets *stop*."""
    def run():
        try:
            for line in stream:
                if line.strip() == "stop":
                    break
        except (OSError, ValueError):
            pass
        stop.set()
    th = threading.Thread(target=run, daemon=True)
    th.start()
    return th


def _detach_stdin():
    """Hand the stdin pipe to the watcher alone and point fd 0 / sys.stdin at
    the null device. On Windows a thread blocked reading a pipe blocks every
    other call on that handle (an import that asks isatty(stdin) hangs)."""
    fd = os.dup(sys.stdin.fileno())
    null = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null, 0)
    os.close(null)
    sys.stdin = open(os.devnull, "r")
    return os.fdopen(fd, "r", encoding="utf-8", errors="replace")


def connection(mod, target):
    from qm import QuantumMachinesManager
    kw = {"host": target["host"], "timeout": mod.CALL_TIMEOUT_S}
    if target.get("cluster"):
        kw["cluster_name"] = target["cluster"]
    if target.get("port"):
        kw["port"] = int(target["port"])
    return mod.bounded(lambda: QuantumMachinesManager(**kw))


def recover(mod, qmm, ids):
    """Reconnect only to our recorded QM; never open or close a peer QM."""
    if not ids.get("qm_id"):
        if ids.get("phase") == "opening a QM":
            raise RuntimeError("Our QM id is not known yet; cleanup cannot be verified.")
        return  # no QM was ever opened by this run
    if str(ids["qm_id"]) not in [str(x) for x in mod.bounded(qmm.list_open_qms)]:
        return  # Already closed by the runner.
    qm = mod.bounded(lambda: qmm.get_qm(ids["qm_id"]))
    job = None
    try:
        if ids.get("job_id"):
            if hasattr(qm, "get_job"):
                job = mod.bounded(lambda: qm.get_job(ids["job_id"]))
            else:
                candidate = mod.bounded(qm.get_running_job)
                if candidate and str(candidate.id) == str(ids["job_id"]):
                    job = candidate
    finally:
        mod.halt_close(qm, job)


def recover_main(script, work):
    try:
        mod = _load(script)
        run = json.loads((work / "_run.json").read_text(encoding="utf-8"))
        ids = json.loads((work / "_status.json").read_text(encoding="utf-8"))
        recover(mod, connection(mod, run["hardware_target"]), ids)
        _write(work / "_recovery.json", {"ok": True})
        return 0
    except Exception as exc:
        _write(work / "_recovery.json", {"ok": False, "error": str(exc)})
        return 1


def main() -> int:
    script, work = Path(sys.argv[1]), Path(sys.argv[2])
    if "--recover" in sys.argv:
        return recover_main(script, work)
    status = work / "_status.json"
    status_data = {}
    status_lock = threading.Lock()
    def update(**data):
        with status_lock:
            status_data.update(data)
            status_data["at"] = time.time()
            _write(status, status_data)
    result = {"ok": False}
    req: dict = {}
    try:
        req = json.loads(sys.stdin.readline() or "{}")
        parent_gone = threading.Event()
        watch_stdin(_detach_stdin(), parent_gone)
        mode, t = req.get("mode"), req.get("target") or {}
        mod = _load(script)
        update(**{"phase": "connecting", "at": time.time()})
        if mode == "saas":
            from qm_saas import QmSaas, QOPVersion
            client = QmSaas(email=t["email"], password=t["password"], host=t["host"])
            ver = QOPVersion(t["version"]) if t.get("version") else None
            update(**{"phase": "starting the simulator (about a minute)", "at": time.time()})
            inst, qmm = mod.saas_qmm(client, ver)
            try:
                if (work / "stop").exists() or parent_gone.is_set():
                    raise RuntimeError("Stopped before the simulation ran.")
                update(**{"phase": "simulating", "at": time.time()})
                samples, adc = mod.simulate(qmm, req.get("sim_ns"))
                result = {"ok": True, "mode": "saas", "traces": _traces(samples),
                          "adc": {k: [float(x) for x in list(v)[:MAX_POINTS]] for k, v in (adc or {}).items()},
                          "expected_peak": mod.EXPECTED_PEAK,
                          "version": getattr(ver, "name", None) or str(getattr(inst, "version", "") or "")}
            finally:
                try:
                    inst.close()
                except Exception:  # noqa: BLE001
                    pass
        elif mode == "opx":
            qmm = connection(mod, t)
            update(phase="checking for running jobs")
            stop = work / "stop"
            def should_stop():
                return stop.exists() or parent_gone.is_set()
            def log(msg):
                update(phase="running", detail=msg)
            res = mod.run_on_hardware(qmm, should_stop=should_stop,
                                      max_s=float(req.get("max_s") or 120), log=log, record=update)
            result = {"ok": True, "mode": "opx", "adc": {k: [float(x) for x in list(v)[:MAX_POINTS]]
                                                         for k, v in (res or {}).items()}}
        else:
            result = {"ok": False, "error": f"unknown mode {mode!r}"}
    except Exception as exc:  # noqa: BLE001 - every failure is the user's answer
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                  "traceback": traceback.format_exc()[-3000:]}
    secret = str((req.get("target") or {}).get("password") or "")
    if secret:
        for k in ("error", "traceback"):
            if isinstance(result.get(k), str):
                result[k] = result[k].replace(secret, "***")
    _write(work / "_result.json", result)
    update(**{"phase": "done", "at": time.time()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
