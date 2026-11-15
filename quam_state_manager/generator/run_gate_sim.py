"""Simulate one gate's ``apply()`` program in the lab env (spawned by core/cz_gate_simulate.py).

    python run_gate_sim.py JOB_JSON WORK_DIR        (connection on stdin)

``JOB_JSON`` holds no secret: ``{"folder", "root_class", "macro", "max_ns",
"pad_ns", "start_ns", "limit_s"}`` (``folder`` is a temporary chip copy SM made). The
connection is the first stdin line, ``{"target": {"local": {...}, "saas":
{...}}}``; it carries the password, so it is read once and never written or
printed. Stdin then stays open; end-of-file or a ``stop`` line (or a file
``WORK_DIR/stop``) asks the run to end.

The worker loads the chip copy with its own root class, builds the config
with the lab's ``generate_config()``, runs the macro's ``apply()`` inside a
QUA ``program()`` (the same helper the gate check uses), places the static
timeline to size the simulation, and calls ``qmm.simulate`` (never
``execute``, never ``open_qm``). Connecting, the cloud instance and the
failure words are the port test's (``run_port_test``). Progress goes to
``WORK_DIR/_status.json``, the answer to ``WORK_DIR/_result.json``.
"""
from __future__ import annotations

import importlib.util
import json
import math
import os
import queue
import sys
import threading
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_port_test as rpt  # noqa: E402
import run_pulse_waveform as rpw  # noqa: E402

#: a port still above this (V) at the last simulated sample was cut by the window
CUT_V = 1e-4
#: the cloud instance is stopped at most this long after a time-out
CLOSE_TIMEOUT_S = 30.0


def _static_timeline():
    """``core/static_timeline.py`` loaded by path (pure standard library), so
    the lab env needs no SM install."""
    path = HERE.parent / "core" / "static_timeline.py"
    spec = importlib.util.spec_from_file_location("sm_static_timeline", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _op_where(op) -> str:
    return "/".join(str(op[k]) for k in ("element", "operation") if op.get(k))


def _whole_ns(d, leaf: str) -> int:
    """*d* as whole ns, rounded up. Any real number type counts (Python or
    numpy), and so does a string that parses to a number. A boolean, text that
    is not a number, a non-finite or a negative value is not readable:
    ``ValueError`` naming *leaf*, never a silent 0."""
    try:
        if isinstance(d, bool):
            raise TypeError("boolean")
        v = float(d.strip()) if isinstance(d, str) else float(d)
    except (TypeError, ValueError, AttributeError):
        raise ValueError(f"{leaf} ({d!r}) is not a number") from None
    if not math.isfinite(v):
        raise ValueError(f"{leaf} ({d!r}) is not a finite number")
    if v < 0:
        raise ValueError(f"{leaf} ({d!r}) is negative")
    return math.ceil(v)


def _delay_ns(op) -> int:
    """The op's port delay in ns, rounded up to a whole ns (a non-whole delay is
    refused by the timeline, so the sum path is used, and the window must never
    be shorter than the program). A missing delay (no key, or None) is 0; an
    unreadable one raises ``ValueError`` (see ``_whole_ns``)."""
    if not isinstance(op, dict):
        return 0
    d = op.get("delay")
    if d is None:
        return 0
    where = _op_where(op)
    return _whole_ns(d, "the port delay" + (f" of {where}" if where else ""))


def _length_ns(op) -> int:
    """The op's pulse length in whole ns; missing (or None) is 0. A whole float
    (40.0) counts. A list, text that is not a number, a non-whole, non-finite
    or negative length raises ``ValueError`` naming the leaf, never a silent
    truncation."""
    if not isinstance(op, dict):
        return 0
    v = op.get("length")
    if v is None:
        return 0
    where = _op_where(op)
    leaf = "the length of " + where if where else "the pulse length"
    n = _whole_ns(v, leaf)
    if n != (float(v.strip()) if isinstance(v, str) else float(v)):
        raise ValueError(f"{leaf} ({v!r}) is not a whole number of ns")
    return n


def sim_length_ns(rows, ops, pad_ns: int, start_ns: int = 0) -> tuple[int, str]:
    """``(ns, how)``: the program end plus *start_ns* (the simulator's start
    latency) and *pad_ns*, rounded up to 4 ns. With timeline rows the program
    end is the latest row end, which already holds each port delay. Without
    rows (the timeline cannot place the program) it is the sum of every played
    length plus the largest port delay, an upper bound for a program without
    loops. An unreadable delay raises ``ValueError`` (see ``_delay_ns``)."""
    ends = [int(r.end_ns) for r in rows or []]
    max_delay = max([_delay_ns(o) for o in ops or []] or [0])
    lengths = [_length_ns(o) for o in ops or []]  # an unreadable length is refused on both paths
    if ends:
        # each timeline row's end already holds its port delay
        total, how = max(ends), "timeline"
    else:
        # the sum of lengths holds no delay, so the largest one is added here
        total, how = sum(lengths) + max_delay, "sum"
    ns = total + int(start_ns) + int(pad_ns)
    return -(-ns // 4) * 4, how


class GateModule:
    """What ``run_port_test.run_sim`` / ``run_saas`` need from a script module,
    for one gate program: ``CONFIG``, ``PLAYS``, ``CALL_TIMEOUT_S``,
    ``bounded``, ``saas_qmm`` and ``simulate``. Only ``qmm.simulate`` is
    called on a manager."""

    CALL_TIMEOUT_S = 15.0
    EXPECTED_PEAK = None

    def __init__(self, config, program, plays):
        self.CONFIG = config
        self.PLAYS = list(plays)
        self.program = program
        self.instance = None        # the cloud instance, closed on a time-out

    def bounded(self, call, timeout=None):
        timeout = self.CALL_TIMEOUT_S if timeout is None else timeout
        box: queue.Queue = queue.Queue()

        def worker():
            try:
                box.put((True, call()))
            except BaseException as exc:  # noqa: BLE001
                box.put((False, exc))
        threading.Thread(target=worker, daemon=True).start()
        try:
            ok, value = box.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError("QM API call timed out.") from None
        if not ok:
            raise value
        return value

    def saas_qmm(self, client, version=None):
        """A QM-SaaS simulator with the config's FEM slots; ``(instance, qmm)``."""
        from qm import QuantumMachinesManager
        from qm_saas import ClusterConfig
        cluster = ClusterConfig()
        ctl = cluster.controller()
        slots: dict = {}
        for con in (self.CONFIG.get("controllers") or {}).values():
            for slot, fem in (con.get("fems") or {}).items():
                slots.setdefault(int(slot), fem.get("type"))
        for slot, kind in sorted(slots.items()):
            (ctl.mw_fems if kind == "MW" else ctl.lf_fems)(slot)
        inst = client.simulator(version or client.latest_version(), cluster)
        self.instance = inst
        inst.spawn()
        if hasattr(type(inst), "qmm_connection_params"):
            params = inst.qmm_connection_params
        else:   # older qm-saas: the TLS connection by hand (as the port test does)
            import ssl
            params = dict(host=inst.host, port=inst.port,
                          connection_headers=inst.default_connection_headers,
                          credentials=ssl.create_default_context(),
                          follow_gateway_redirections=False)
        return inst, QuantumMachinesManager(**params)

    def simulate(self, qmm, duration_ns=None):
        from qm import SimulationConfig
        job = qmm.simulate(self.CONFIG, self.program,
                           SimulationConfig(duration=int(duration_ns) // 4))
        if hasattr(job, "wait_until"):
            job.wait_until("Done")
        return job.get_simulated_samples(), {}


def _port_elements(config, plays) -> dict:
    """``"con/slot/port" -> [element]`` for every analog input port of the
    elements the program plays."""
    played = {el for el, _op in plays}
    out: dict = {}
    for name, el in sorted((config.get("elements") or {}).items()):
        if name not in played:
            continue
        ports = []
        for key in ("singleInput", "MWInput"):
            p = (el.get(key) or {}).get("port")
            if p:
                ports.append(p)
        for p in (el.get("mixInputs") or {}).get("I"), (el.get("mixInputs") or {}).get("Q"):
            if p:
                ports.append(p)
        for p in ports:
            if isinstance(p, (list, tuple)) and len(p) == 3:
                out.setdefault(f"{p[0]}/{int(p[1])}/{int(p[2])}", []).append(name)
    return out


def _intended(rows, ops):
    """Config leading edges, including port delays, for this exact run."""
    out = []
    by_op = {(o["element"], o["operation"]): o for o in ops}
    for row in rows:
        d = row._asdict() if hasattr(row, "_asdict") else dict(row)
        op = by_op.get((d["element"], d["operation"]), {})
        wf = op.get("waveform") or {}
        d.update(samples=wf.get("samples"), constant=wf.get("sample"),
                 sampling_rate=op.get("sampling_rate"), truncated=wf.get("truncated", False))
        out.append(d)
    return out


def build(job: dict) -> dict:
    """Load the copy, build the config and the program, size the simulation."""
    folder = job["folder"]
    machine, side, cfg = rpw._chip_side(folder, job.get("root_class"), [folder])
    if machine is None or cfg is None:
        return {"ok": False, "error_kind": "chip",
                "error": side.get("load_error") or side.get("config_error")}
    from qm import generate_qua_script
    try:
        prog = rpw._apply_in_program(machine, job["macro"])
        script = generate_qua_script(prog)
    except BaseException as exc:  # noqa: BLE001 -- the lab's own words
        return {"ok": False, "error_kind": "gate", "error": rpw._err_text(exc, [folder])}
    plays, _unres, _readable = rpw._plays(script)
    ops = [rpw.op_data(cfg, el, op) for el, op in plays]
    tl = _static_timeline().static_timeline(script, ops)
    try:
        ns, how = sim_length_ns(tl.get("rows"), ops, int(job.get("pad_ns") or 0),
                                int(job.get("start_ns") or 0))
    except ValueError as exc:
        return {"ok": False, "error_kind": "gate", "error": str(exc)}
    return {"ok": True, "config": cfg, "program": prog, "plays": plays, "sim_ns": ns,
            "length_from": how, "cannot_draw": tl.get("cannot_draw"),
            "intended": _intended(tl.get("rows") or [], ops)}


#: an LF-FEM output port without a stated ``sampling_rate`` runs at 1 GS/s (the config default)
LF_DEFAULT_RATE = 1e9


def _rate_ok(rate) -> float | None:
    try:
        r = float(rate)
    except (TypeError, ValueError):
        return None
    return r if math.isfinite(r) and r > 0 and not isinstance(rate, bool) else None


def _port_rate(config, con, slot, port, kind) -> float | None:
    """The output port's sampling rate from the config; an LF port without one
    takes the config default; otherwise None (not known)."""
    try:
        fem = ((config.get("controllers") or {}).get(con) or {}).get("fems") or {}
        fem = fem.get(slot) or fem.get(str(slot)) or {}
        outs = fem.get("analog_outputs") or {}
        entry = outs.get(port) or outs.get(str(port)) or {}
        rate = _rate_ok(entry.get("sampling_rate"))
    except AttributeError:
        rate = None
    if rate is None and str(kind or "").lower() == "lf":
        return LF_DEFAULT_RATE
    return rate


def _ports(traces: dict, port_elements: dict, config: dict | None = None) -> list:
    """Each driven port's trace. The sample step comes from the simulator's
    rate, else from the config's port; when neither says, ``dt_ns`` is None
    and ``time_step_unknown`` is set, never a guessed step."""
    out = []
    for tr in traces.values():
        if tr.get("digital") or tr.get("slot") is None:
            continue
        key = f"{tr['con']}/{tr['slot']}/{tr['port']}"
        if key not in port_elements:
            continue
        rate = _rate_ok(tr.get("rate")) or _port_rate(config or {}, tr["con"], tr["slot"],
                                                       tr["port"], tr.get("kind"))
        step = int(tr.get("step") or 1)
        out.append({"controller": tr["con"], "fem": tr["slot"], "port": tr["port"],
                    "elements": port_elements[key], "kind": tr.get("kind"),
                    "t0_ns": 0.0, "dt_ns": 1e9 / rate * step if rate else None,
                    "time_step_unknown": rate is None,
                    "volts": tr.get("re") or [], "imag": tr.get("im"),
                    "decimated": int(tr.get("step") or 1) > 1,
                    "cut_at_end": bool(tr.get("re")) and abs(float(tr["re"][-1])) > CUT_V})
    return sorted(out, key=lambda p: (str(p["controller"]), p["fem"], p["port"]))


def _watchdog(limit_s: float, work: Path, mod_box: list, done: threading.Event, secret: str):
    """At *limit_s*: write the time-out answer, stop our cloud instance, end."""
    if done.wait(limit_s):
        return
    rpt._write(work / "_result.json", {"ok": False, "timed_out": True, "limit_s": limit_s})
    mod = mod_box[0] if mod_box else None
    inst = getattr(mod, "instance", None)
    if inst is not None:
        t = threading.Thread(target=lambda: inst.close(), daemon=True)
        t.start()
        t.join(CLOSE_TIMEOUT_S)
    os._exit(3)


def main() -> int:
    job = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    work = Path(sys.argv[2])
    status: dict = {}
    lock = threading.Lock()

    def update(**data):
        with lock:
            status.update(data)
            status["at"] = time.time()
            rpt._write(work / "_status.json", status)
    req: dict = {}
    result: dict = {"ok": False}
    done = threading.Event()
    mod_box: list = []
    parent_gone = threading.Event()
    try:
        req = json.loads(sys.stdin.readline() or "{}")
        rpt.watch_stdin(rpt._detach_stdin(), parent_gone)
        target = req.get("target") or {}
        threading.Thread(target=_watchdog, daemon=True, args=(
            float(job.get("limit_s") or 60), work, mod_box, done, "")).start()

        def stopping():
            return (work / "stop").exists() or parent_gone.is_set()
        os.environ["QUAM_STATE_PATH"] = job["folder"]
        update(phase="building the gate program")
        built = build(job)
        if not built["ok"]:
            result = built
        elif built["sim_ns"] > int(job.get("max_ns") or 20000):
            result = {"ok": False, "too_long": True, "sim_ns": built["sim_ns"],
                      "max_ns": int(job.get("max_ns") or 20000)}
        elif stopping():
            result = {"ok": False, "stopped": True}
        else:
            mod = GateModule(built["config"], built["program"], built["plays"])
            mod_box.append(mod)
            res = rpt.run_sim(mod, target, {"sim_ns": built["sim_ns"]}, update, stopping)
            result = {"ok": True, "backend": res.get("backend"),
                      "backend_label": res.get("backend_label"), "sim_ns": built["sim_ns"],
                      "length_from": built["length_from"], "cannot_draw": built["cannot_draw"],
                      "local_error": res.get("local_error"),
                      "intended": built.get("intended"),
                      "ports": _ports(res.get("traces") or {},
                                      _port_elements(built["config"], built["plays"]),
                                      built["config"])}
    except Exception as exc:  # noqa: BLE001 -- every failure is the user's answer
        result = {"ok": False, **rpt.classify(exc, req.get("target"), "sim"),
                  "traceback": traceback.format_exc()[-3000:]}
        if (work / "stop").exists() or parent_gone.is_set():
            result["stopped"] = True
    done.set()
    t = req.get("target") or {}
    secret = str((t.get("saas") or {}).get("password") or "")
    if secret:
        for k in ("error", "detail", "traceback", "local_error"):
            if isinstance(result.get(k), str):
                result[k] = result[k].replace(secret, "***")
    rpt._write(work / "_result.json", result)
    update(phase="done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
