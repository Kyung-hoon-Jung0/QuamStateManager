"""Run one port-test script in the chip's env (spawned by core/port_test_runner.py).

    python run_port_test.py SCRIPT WORK_DIR             (request JSON on stdin)
    python run_port_test.py SCRIPT WORK_DIR --recover         (halt + close OUR QM)
    python run_port_test.py SCRIPT WORK_DIR --close-matching  (close matching open QMs)

The request (the first stdin line, never on disk) is ``{"mode": "saas"|"opx",
"target": {...}, "max_s": float, "sim_ns": int}``; it carries the credentials,
so it is read once and never written or printed. SM keeps stdin OPEN for the
run's lifetime: a later ``stop`` line, or end-of-file (SM exited or was
killed), asks the run to halt and close its QM -- so a run never outlives SM.
A file ``WORK_DIR/stop`` asks the same. Progress, and as soon as they are
known the QM id and job id, go to ``WORK_DIR/_status.json``; the answer to
``WORK_DIR/_result.json``. ``--recover`` reconnects to the recorded QM only
(never another one) and halts + closes it. ``--close-matching`` lists open QMs
on the same target and closes only those whose config fingerprint equals this
port-test config fingerprint. The script module is the same text the user
downloads.
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


#: a trace's first edge is its first sample above this fraction of its own peak
EDGE_FRACTION = 0.1
#: exception class names that mean "the OPX did not answer" (D-20)
CONNECT_ERRORS = ("QmServerDetectionError", "TimeoutError", "ConnectionError", "ConnectionRefusedError",
                  "ConnectTimeout", "ConnectionTimeout", "ConnectionTimeoutError")


def _members(mod) -> set:
    """(con, slot, port) of every output the script plays (``PLAYS`` or ``ELEMENT``)."""
    config = getattr(mod, "CONFIG", None) or {}
    names = [el for el, _op in (getattr(mod, "PLAYS", None) or [])] or [getattr(mod, "ELEMENT", None)]
    out = set()
    for name in names:
        el = (config.get("elements") or {}).get(name) or {}
        port = (el.get("singleInput") or el.get("MWInput") or {}).get("port")
        if port and len(port) == 3:
            out.add((str(port[0]), int(port[1]), int(port[2])))
    return out


def _kind(mod, con, slot) -> str:
    fems = (((getattr(mod, "CONFIG", None) or {}).get("controllers") or {}).get(con) or {}).get("fems") or {}
    fem = fems.get(str(slot)) or fems.get(slot) or {}
    return str(fem.get("type") or "").lower() or "unknown"


def _first_edge_ns(z, rate) -> float | None:
    """ns of the first sample with |x| above EDGE_FRACTION of the trace's peak."""
    import numpy as np
    mag = np.abs(np.asarray(z))
    if not len(mag) or not float(mag.max()):
        return None
    idx = int(np.argmax(mag > EDGE_FRACTION * float(mag.max())))
    try:
        step_ns = 1e9 / float(rate) if rate else 1.0
    except (TypeError, ValueError):
        step_ns = 1.0
    return round(idx * step_ns, 3)


def _traces(samples, mod=None) -> dict:
    """Every simulated analog/digital port as decimated real (and imag) arrays,
    labelled ``{key, con, slot, port, kind, member, first_edge_ns}`` (D-17, D-18)."""
    import re
    import numpy as np
    members = _members(mod) if mod is not None else set()
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
            m = re.match(r"^(\d+)-(\d+)", str(key))
            slot, port = (int(m.group(1)), int(m.group(2))) if m else (None, None)
            row.update(key=f"{cname}:{key}", con=cname, slot=slot, port=port,
                       kind=_kind(mod, cname, slot) if mod is not None and slot is not None else "unknown",
                       member=(cname, slot, port) in members,
                       first_edge_ns=_first_edge_ns(z, row.get("rate")))
            out[f"{cname}:{key}"] = row
        digital = getattr(con, "digital", None)
        if isinstance(digital, dict):
            for key, arr in digital.items():
                d = np.asarray(arr).astype(float)
                step = max(1, len(d) // MAX_POINTS)
                out[f"{cname}:dig:{key}"] = {"peak": float(d.max()) if len(d) else 0.0, "n": int(len(d)),
                                             "step": step, "re": d[::step].tolist(), "digital": True,
                                             "key": f"{cname}:dig:{key}", "con": cname, "slot": None,
                                             "port": None, "kind": "digital", "member": False,
                                             "first_edge_ns": _first_edge_ns(d, None)}
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


def close_matching(mod, qmm, ids):
    fp = str(ids.get("qm_config_fingerprint") or "").strip()
    if not fp:
        raise RuntimeError("No config fingerprint was recorded for this run.")
    opened, closed = [], []
    for qm_id in mod.bounded(qmm.list_open_qms):
        qid = str(qm_id)
        qm = mod.bounded(lambda qid=qid: qmm.get_qm(qid))
        cfg = mod.bounded(qm.get_config)
        got = mod.config_fingerprint(cfg)
        opened.append({"qm_id": qid, "fingerprint": got, "matches": got == fp})
        if got == fp:
            mod.bounded(qm.close)
            closed.append(qid)
    return {"target_fingerprint": fp, "open_qms": opened, "closed_qms": closed}


def close_matching_main(script, work):
    out = work / "_close_matching.json"
    try:
        mod = _load(script)
        run = json.loads((work / "_run.json").read_text(encoding="utf-8"))
        ids = json.loads((work / "_status.json").read_text(encoding="utf-8"))
        data = close_matching(mod, connection(mod, run["hardware_target"]), ids)
        _write(out, {"ok": True, **data})
        return 0
    except Exception as exc:
        _write(out, {"ok": False, "error": str(exc)})
        return 1


def classify(exc, target, mode=None) -> dict:
    """The user's sentence for a failure, with the raw text in ``detail`` (D-20)."""
    raw = f"{type(exc).__name__}: {exc}"
    names = {c.__name__ for c in type(exc).__mro__}
    if names & set(CONNECT_ERRORS) or isinstance(exc, (TimeoutError, ConnectionError)):
        t = target or {}
        saas = t.get("saas") if isinstance(t.get("saas"), dict) else (t if mode == "saas" else None)
        if saas is not None and not isinstance(t.get("local"), dict):
            # Only the cloud simulator was tried: no OPX was ever involved.
            where = f" at {saas['host']}" if saas.get("host") else ""
            return {"error_kind": "connect", "error": f"Could not reach the QM-SaaS simulator{where}.", "detail": raw}
        if not t.get("host") and isinstance(t.get("local"), dict):
            t = t["local"]
        where = str(t.get("host") or "the configured host")
        cluster = f" (cluster {t['cluster']})" if t.get("cluster") else ""
        return {"error_kind": "connect", "error": f"Could not reach the OPX at {where}{cluster}.", "detail": raw}
    return {"error_kind": "other", "error": raw, "detail": raw}


def run_saas(mod, t, req, update, stopping, phase="starting the simulator (about a minute)") -> dict:
    """Simulate on a QM-SaaS cloud instance (credentials only from the request)."""
    from qm_saas import QmSaas, QOPVersion
    client = QmSaas(email=t["email"], password=t["password"], host=t["host"])
    ver = QOPVersion(t["version"]) if t.get("version") else None
    update(**{"phase": phase, "at": time.time()})
    inst, qmm = mod.saas_qmm(client, ver)
    try:
        if stopping():
            raise RuntimeError("Stopped before the simulation ran.")
        update(**{"phase": "simulating", "at": time.time()})
        samples, adc = mod.simulate(qmm, req.get("sim_ns"))
        return {"ok": True, "mode": "saas", "traces": _traces(samples, mod),
                "adc": {k: [float(x) for x in list(v)[:MAX_POINTS]] for k, v in (adc or {}).items()},
                "expected_peak": mod.EXPECTED_PEAK,
                "version": getattr(ver, "name", None) or str(getattr(inst, "version", "") or "")}
    finally:
        try:
            inst.close()
        except Exception:  # noqa: BLE001
            pass


def run_sim(mod, t, req, update, stopping) -> dict:
    """Port play simulation: the same program through the QOP simulator of the
    step-2 host when one answers, else a QM-SaaS instance when credentials were
    given. Nothing is played on hardware either way."""
    local, saas = t.get("local") or {}, t.get("saas") or {}
    why = None
    if local.get("host"):
        try:
            update(phase="connecting to the local simulator")
            qmm = connection(mod, local)
            if stopping():
                raise RuntimeError("Stopped before the simulation ran.")
            update(phase="simulating")
            samples, _adc = mod.simulate(qmm, req.get("sim_ns"))
            return {"ok": True, "mode": "sim", "simulated": True, "backend": "local",
                    "backend_label": "QOP simulator at " + str(local["host"]),
                    "traces": _traces(samples, mod), "expected_peak": mod.EXPECTED_PEAK}
        except Exception as exc:  # noqa: BLE001 - fall through to the cloud, or report
            if stopping():
                raise
            why = f"{type(exc).__name__}: {exc}"
    if saas.get("email") and saas.get("host") and saas.get("password"):
        res = run_saas(mod, saas, req, update, stopping, **(
            {"phase": "local simulator did not answer \u2014 starting QM-SaaS (about a minute)"} if why else {}))
        res.update(mode="sim", simulated=True, backend="saas", backend_label="QM-SaaS cloud simulator")
        if why:
            res["local_error"] = why
        return res
    raise RuntimeError(("The local simulator did not answer (" + why + ")") if why else
                       "No simulator: enter a QOP host in step 2 or set the QM-SaaS credentials.")


def main() -> int:
    script, work = Path(sys.argv[1]), Path(sys.argv[2])
    if "--recover" in sys.argv:
        return recover_main(script, work)
    if "--close-matching" in sys.argv:
        return close_matching_main(script, work)
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
    parent_gone = None
    try:
        req = json.loads(sys.stdin.readline() or "{}")
        parent_gone = threading.Event()
        watch_stdin(_detach_stdin(), parent_gone)
        mode, t = req.get("mode"), req.get("target") or {}
        mod = _load(script)
        update(**{"phase": "connecting", "at": time.time()})
        def stopping():
            return (work / "stop").exists() or parent_gone.is_set()
        if mode == "saas":
            result = run_saas(mod, t, req, update, stopping)
        elif mode == "sim":
            result = run_sim(mod, t, req, update, stopping)
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
        result = {"ok": False, **classify(exc, req.get("target"), req.get("mode")),
                  "traceback": traceback.format_exc()[-3000:]}
        if (work / "stop").exists() or (parent_gone is not None and parent_gone.is_set()):
            # The user's stop ended it: whatever the SDK raised on the way out is not the answer.
            result["stopped"] = True
    t = req.get("target") or {}
    secret = str(t.get("password") or (t.get("saas") or {}).get("password") or "")
    if secret:
        for k in ("error", "detail", "traceback"):
            if isinstance(result.get(k), str):
                result[k] = result[k].replace(secret, "***")
    _write(work / "_result.json", result)
    update(**{"phase": "done", "at": time.time()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
