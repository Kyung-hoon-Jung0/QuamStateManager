"""Run one port-test script in the chip's env (spawned by core/port_test_runner.py).

    python run_port_test.py SCRIPT WORK_DIR   (request JSON on stdin)

The request (stdin, never on disk) is ``{"mode": "saas"|"opx", "target": {...},
"max_s": float, "sim_ns": int}``; it carries the credentials, so it is read
once and never written or printed. Progress goes to ``WORK_DIR/_status.json``,
the answer to ``WORK_DIR/_result.json``. A file ``WORK_DIR/stop`` asks a
hardware run to halt. The script module is the same text the user downloads.
"""
from __future__ import annotations

import importlib.util
import json
import os
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


def main() -> int:
    script, work = Path(sys.argv[1]), Path(sys.argv[2])
    status = work / "_status.json"
    result = {"ok": False}
    req: dict = {}
    try:
        req = json.loads(sys.stdin.read() or "{}")
        mode, t = req.get("mode"), req.get("target") or {}
        mod = _load(script)
        _write(status, {"phase": "connecting", "at": time.time()})
        if mode == "saas":
            from qm_saas import QmSaas, QOPVersion
            client = QmSaas(email=t["email"], password=t["password"], host=t["host"])
            ver = QOPVersion(t["version"]) if t.get("version") else None
            _write(status, {"phase": "starting the simulator (about a minute)", "at": time.time()})
            inst, qmm = mod.saas_qmm(client, ver)
            try:
                if (work / "stop").exists():
                    raise RuntimeError("Stopped before the simulation ran.")
                _write(status, {"phase": "simulating", "at": time.time()})
                samples = mod.simulate(qmm, req.get("sim_ns"))
                result = {"ok": True, "mode": "saas", "traces": _traces(samples),
                          "expected_peak": mod.EXPECTED_PEAK,
                          "version": getattr(ver, "name", None) or str(getattr(inst, "version", "") or "")}
            finally:
                try:
                    inst.close()
                except Exception:  # noqa: BLE001
                    pass
        elif mode == "opx":
            from qm import QuantumMachinesManager
            kw = {"host": t["host"]}
            if t.get("cluster"):
                kw["cluster_name"] = t["cluster"]
            if t.get("port"):
                kw["port"] = int(t["port"])
            qmm = QuantumMachinesManager(**kw)
            _write(status, {"phase": "checking for running jobs", "at": time.time()})
            stop = work / "stop"

            def log(msg):
                _write(status, {"phase": "running", "detail": msg, "at": time.time()})
            res = mod.run_on_hardware(qmm, should_stop=stop.exists,
                                      max_s=float(req.get("max_s") or 600), log=log)
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
    _write(status, {"phase": "done", "at": time.time()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
