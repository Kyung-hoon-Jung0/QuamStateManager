"""Lab-env child for one simulate click: guarded node build, then one cloud simulation.

    python -B nodesim_click.py RUN_DIR        (spawned by SM; see p1 spec section 4)

Stdin line 1 is the connection ``{"saas": {"email", "password", "host"[, "port"][, "version"]}}``;
it carries the password, so it is read once and never written or printed. Stdin line 2
is the request ``{"scratch", "chip_copy", "library", "node", "targets", "window_ns",
"overrides", "saas_host"[, "wait_cap_s", "limit_s"]}``; ``saas_host`` is the host SM saved
for the cloud simulator, and the connection must name exactly that host. Stdin then
stays open: a ``stop`` line, end-of-file (SM is gone) or a file ``RUN_DIR/stop`` asks
the run to end.

Order (D-8): the program is built through ``nodesim_build.build`` with the network
allow-list empty; only after the build returns does this SM-owned code widen the
allow-list once to the cloud login endpoint and the run folder, and after the spawn
set the one QuantumMachinesManager lease to that instance. ``open_qm``, ``execute`` and
``qm_session`` stay blocked throughout. Only the cloud simulator is ever contacted
(D-2): this file has no local-host path at all.

Output: ``RUN_DIR/_status.json`` ``{"phase", "phases"}`` per phase (measured seconds
only) and ``RUN_DIR/_result.json`` ``{"ok", "kind", "message", "report", "script",
"script_ok", "program_chars", "qop_version", "window_ns", "last_adc_end_ns",
"guard_lines"}`` plus the counters ``instances_opened`` / ``instances_closed``.
"""
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
import traceback

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import nodesim_build as nb  # noqa: E402
from nodesim_proto import is_allowed_saas_host  # noqa: E402

KINDS = ("ok", "node_error", "serializer", "guard", "refused", "too_long", "timeout", "failed")
#: the child's phases in order; SM's own ``copy`` comes before them
PHASES = ("build", "login", "spawn", "connect", "submit", "wait", "report", "close")
ONLY_SAAS = "Only the cloud simulator is allowed."
#: D-41: the click window cap
MAX_WINDOW_NS = 100_000
#: D-40: default cap on ``job.wait_until("Done")`` (P0b used 180 s per job)
WAIT_CAP_S = 180.0
#: whole-run watchdog: past this the instance is closed and the child ends
LIMIT_S = 600.0
#: a cloud close that does not return within this is abandoned (the instance expires)
CLOSE_TIMEOUT_S = 30.0
SAAS_PORT = 443
SERIALIZATION = "QUA serialization validation failed: "
#: tries of one ``_status.json`` replace, and the pause between them (a reader holds the file for milliseconds)
STATUS_REPLACE_TRIES = 20
STATUS_REPLACE_WAIT_S = 0.01
# Every class the cold sim step constructs (R3-1); Guard.install_classes captures each one.
COLD_SIM_NAMES = ("qm.QuantumMachinesManager", "qm.SimulationConfig",
                  "qm_saas.QmSaas", "qm_saas.QOPVersion", "qm_saas.ClusterConfig")
#: qm's answer when the cloud simulator refuses a window (P0b item 4, D-41, Q-4)
TOO_LONG_MARKS = ("Internal Server Error", "StatusCode.UNKNOWN")


class Stopped(Exception):
    """The user (or SM going away) asked the run to end."""


class WaitCapHit(Exception):
    """``job.wait_until("Done")`` did not return within its cap."""


def scrub(text, secret):
    text = str(text)
    return text.replace(secret, "***") if secret else text


class Status:
    """``_status.json`` writer: the current phase and the measured finished phases."""

    def __init__(self, run_dir):
        self.path = Path(run_dir) / "_status.json"
        self.phases = []
        self.phase = None
        self.started = None
        self.lock = threading.Lock()

    def begin(self, name, write=True):
        with self.lock:
            self._close_current()
            self.phase, self.started = name, time.perf_counter()
            if write:
                self._write()

    def end(self, write=True):
        with self.lock:
            self._close_current()
            self.phase = None
            if write:
                self._write()

    def _close_current(self):
        if self.phase is not None:
            self.phases.append({"name": self.phase, "s": round(time.perf_counter() - self.started, 3)})
            self.phase = None

    def _write(self):
        """Replace the status file. On Windows the replace fails while SM (or any other reader) holds the
        file open; it is retried a few times and then skipped: a status write never ends the run."""
        tmp = self.path.with_name("_status.tmp")
        tmp.write_text(json.dumps({"phase": self.phase, "phases": self.phases, "at": time.time()}),
                       encoding="utf-8")
        for i in range(STATUS_REPLACE_TRIES):
            try:
                os.replace(tmp, self.path)
                return
            except PermissionError:
                if i + 1 < STATUS_REPLACE_TRIES:
                    time.sleep(STATUS_REPLACE_WAIT_S)


class ResultFile:
    """``_result.json``, opened before the audit hook so a guard failure during the
    build (when the run folder is not yet writable) still reaches SM. Written once."""

    def __init__(self, run_dir):
        self.handle = open(Path(run_dir) / "_result.json", "w", encoding="utf-8")
        self.lock = threading.Lock()
        self.done = False

    def write(self, result):
        with self.lock:
            if self.done:
                return False
            self.done = True
            self.handle.write(json.dumps(result, default=str))
            self.handle.flush()
            self.handle.close()
            return True


def detach_stdin():
    """Hand the stdin pipe to the watcher alone and point fd 0 at the null device.
    On Windows a thread blocked on a pipe blocks other calls on that handle."""
    fd = os.dup(sys.stdin.fileno())
    null = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null, 0)
    os.close(null)
    sys.stdin = open(os.devnull, "r")
    return os.fdopen(fd, "r", encoding="utf-8", errors="replace")


def watch_stdin(stream, stop):
    def run():
        try:
            for line in stream:
                if line.strip() == "stop":
                    break
        except (OSError, ValueError):
            pass
        stop.set()
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def check_connection(conn, req):
    """The SaaS login, or None when the connection is not exactly the saved cloud simulator."""
    if not isinstance(conn, dict) or set(conn) != {"saas"} or not isinstance(conn["saas"], dict):
        return None
    saas = conn["saas"]
    host = str(saas.get("host") or "").strip()
    if not host or host != str(req.get("saas_host") or "").strip():
        return None
    if not saas.get("email") or not saas.get("password"):
        return None
    return saas


def resolve(host, port):
    """Q-3: the (ip, port) pairs a host resolves to; the guard sees resolved addresses."""
    pairs = set()
    for info in socket.getaddrinfo(host, int(port), type=socket.SOCK_STREAM):
        pairs.add((info[4][0], int(port)))
    return sorted(pairs)


def cluster_of(config, cluster_class):
    """The cloud instance's cluster: the config's FEM slots by type (as P0b and the gate sim).

    *cluster_class* is the captured ``qm_saas.ClusterConfig`` (R3-1), never a fresh lookup.
    """
    from types import SimpleNamespace
    from nodesim_proto import cluster_config, cluster_spec
    return cluster_config(cluster_spec(config), SimpleNamespace(ClusterConfig=cluster_class))


def target_key(fields, targets):
    if "qubit_pairs" in fields and ("qubits" not in fields or all("-" in str(t) for t in targets)):
        return "qubit_pairs"
    if "qubits" in fields:
        return "qubits"
    return None


def click_stub(original, user_overrides, targets, notes):
    """Wrap ``nodesim_build.stub_class`` so targets and overrides land on the node's own
    parameter fields through its ``model_validate`` (D-4); unknown keys are dropped
    and listed, never set by attribute."""
    def stub_class(guard, overrides):
        live = {}
        base = original(guard, live)

        class ClickNode(base):
            def __init__(self, name, parameters=None, machine=None, **kwargs):
                live.clear()
                if parameters is not None:
                    fields = set(type(parameters).model_fields)
                    wanted = dict(user_overrides)
                    if targets:
                        key = target_key(fields, targets)
                        if key is None:
                            raise RuntimeError("This node takes neither qubits nor qubit_pairs.")
                        wanted[key] = list(targets)
                    live.update({k: v for k, v in wanted.items() if k in fields})
                    notes["dropped_overrides"] = sorted(k for k in wanted if k not in fields)
                super().__init__(name, parameters, machine, **kwargs)

        notes["stub"] = ClickNode
        return ClickNode
    return stub_class


def node_file(req):
    library = Path(req["library"]).resolve()
    path = Path(req["node"])
    path = (path if path.is_absolute() else library / path).resolve()
    return path, library


def build_program(req, guard, result, notes):
    """The guarded build plus the config and the full script (D-24)."""
    path, library = node_file(req)
    job = {"node_file": str(path), "library_root": str(library), "chip_copy": req["chip_copy"],
           "scratch": req["scratch"], "overrides": {}}
    nb.stub_class = click_stub(STUB_CLASS, req.get("overrides") or {}, req.get("targets") or [], notes)
    out = nb.build(job, guard, result)
    return post_build(out, notes)


def post_build(out, notes):
    """Share the cold config refusal and best-effort full serialization (D-24, contract 10)."""
    node = notes["stub"].instances[0]
    program = node.namespace["qua_program"]
    if node.machine is None:
        raise RuntimeError("The node built no machine to make a config from.")
    config = node.machine.generate_config()
    from qm import generate_qua_script
    script, script_ok = out["program_script"], False
    try:
        full = generate_qua_script(program, config)
    except Exception as exc:  # noqa: BLE001 -- an unserializable program still simulates
        notes["script_error"] = f"{type(exc).__name__}: {exc}"
    else:
        if "SERIALIZATION VALIDATION ERROR" in full:
            detail = full.split("SERIALIZATION VALIDATION ERROR", 1)[1].strip().replace("\n", " ")[:300]
            raise RuntimeError(SERIALIZATION + detail)
        script, script_ok = full, "SERIALIZATION WAS NOT COMPLETE" not in full
    return {"program": program, "config": config, "script": script, "script_ok": script_ok,
            "program_chars": len(out["program_script"] or ""), "built": out}


def bounded(fn, cap_s, stopping, what):
    """Run *fn* in a thread; Stopped on a stop request, WaitCapHit past *cap_s*."""
    box = {}

    def work():
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 -- re-raised in the caller
            box["error"] = exc
    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    deadline = time.monotonic() + cap_s
    while thread.is_alive():
        thread.join(0.2)
        if not thread.is_alive():
            break
        if stopping():
            raise Stopped()
        if time.monotonic() > deadline:
            raise WaitCapHit(f"{what} after {cap_s:g} s")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def close_instance(inst, counters):
    """Close our one cloud instance, bounded; counted once per instance."""
    if inst is None or counters.get("closed_ids", set()) & {id(inst)}:
        return
    counters.setdefault("closed_ids", set()).add(id(inst))
    box = {}

    def work():
        try:
            inst.close()
            box["ok"] = True
        except Exception as exc:  # noqa: BLE001
            box["error"] = f"{type(exc).__name__}: {exc}"
    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    thread.join(CLOSE_TIMEOUT_S)
    if box.get("ok"):
        counters["closed"] = counters.get("closed", 0) + 1
    else:
        counters["close_error"] = box.get("error") or f"close did not return within {CLOSE_TIMEOUT_S:g} s"


def simulate(saas, built, req, guard, status, stopping, counters, box):
    """One cloud instance: login, spawn, connect, submit, wait (capped), report, close."""
    # FI4-1, R3-1: refuse any rebound sim-step name; construct only the objects captured before node code.
    classes = guard.check_sim_bindings()
    missing = [name for name in COLD_SIM_NAMES if name not in classes]
    if missing:
        raise ImportError("not captured before the build: " + ", ".join(missing))
    QuantumMachinesManager, SimulationConfig = classes["qm.QuantumMachinesManager"], classes["qm.SimulationConfig"]
    QmSaas, QOPVersion = classes["qm_saas.QmSaas"], classes["qm_saas.QOPVersion"]
    ClusterConfig = classes["qm_saas.ClusterConfig"]
    window_ns = int(req["window_ns"])
    wait_cap = float(req.get("wait_cap_s") or WAIT_CAP_S)
    status.begin("login")
    port = int(saas.get("port") or SAAS_PORT)
    client = QmSaas(email=saas["email"], password=saas["password"], host=saas["host"], port=port)
    version = QOPVersion(saas["version"]) if saas.get("version") else client.latest_version()
    if stopping():
        raise Stopped()
    status.begin("spawn")
    inst = client.simulator(version, cluster_of(built["config"], ClusterConfig))
    box["inst"] = inst
    counters["opened"] = counters.get("opened", 0) + 1
    try:
        inst.spawn()
        if stopping():
            raise Stopped()
        status.begin("connect")
        params = inst.qmm_connection_params
        if not is_allowed_saas_host(params["host"], params["port"]):
            guard.fail("lease", ONLY_SAAS)
        guard.set_lease(params, resolve(params["host"], params["port"]))
        qmm = QuantumMachinesManager(**params)
        status.begin("submit")
        job = qmm.simulate(built["config"], built["program"], SimulationConfig(duration=window_ns // 4))
        status.begin("wait")
        bounded(lambda: job.wait_until("Done", timeout=wait_cap), wait_cap, stopping, "wait_until Done")
        status.begin("report")
        report = job.get_simulated_waveform_report().to_dict()
    finally:
        status.begin("close")
        close_instance(inst, counters)
        status.end()
    ends = [int(a.get("end_time") or 0) for a in report.get("adc_acquisitions") or []]
    return {"report": report, "qop_version": str(getattr(version, "value", None) or version),
            "last_adc_end_ns": max(ends) if ends else None}


def watchdog(limit_s, done, box, counters, result_file, base, secret):
    """At *limit_s*: answer timeout, close our cloud instance, end the process."""
    if done.wait(limit_s):
        return
    close_instance(box.get("inst"), counters)
    result_file.write({**base, "kind": "timeout", "message": f"No answer within {limit_s:g} s.",
                       "limit_s": limit_s, "instances_opened": counters.get("opened", 0),
                       "instances_closed": counters.get("closed", 0)})
    os._exit(3)


def classify(exc, guard, secret):
    """(kind, message) for an exception that ended the run."""
    if guard is not None and guard.verdict:
        return "guard", guard.verdict
    if isinstance(exc, Stopped):
        return "failed", "Stopped."
    if isinstance(exc, WaitCapHit):
        return "timeout", scrub(exc, secret)
    text = scrub(f"{type(exc).__name__}: {exc}", secret)
    if type(exc).__name__ == "QMConnectionError" and all(mark in text for mark in TOO_LONG_MARKS):
        return "too_long", text
    return "failed", text


STUB_CLASS = nb.stub_class


def main():
    run_dir = Path(sys.argv[1]).resolve()
    started = time.monotonic()
    result_file = ResultFile(run_dir)
    status = Status(run_dir)
    base = {"ok": False, "kind": "failed", "message": "", "report": None, "script": None,
            "script_ok": False, "program_chars": 0, "qop_version": None, "window_ns": None,
            "last_adc_end_ns": None, "guard_lines": []}
    counters = {}
    box = {}
    secret = ""
    result = dict(base)
    done = threading.Event()
    parent_gone = threading.Event()
    guard = None
    try:
        conn_line = sys.stdin.readline()
        req = json.loads(sys.stdin.readline() or "{}")
        try:
            conn = json.loads(conn_line or "{}")
        except ValueError:
            conn = None
        if isinstance(conn, dict) and isinstance(conn.get("saas"), dict):
            secret = str(conn["saas"].get("password") or "")
        del conn_line
        watch_stdin(detach_stdin(), parent_gone)
        result["window_ns"] = req.get("window_ns")
        base["window_ns"] = req.get("window_ns")
        saas = check_connection(conn, req)
        conn = None
        if saas is None:
            result.update(kind="failed", message=ONLY_SAAS)
            return finish(result_file, result, counters, secret, done)
        window = req.get("window_ns")
        if not isinstance(window, int) or isinstance(window, bool) or window <= 0:
            result.update(kind="failed", message="The simulation window is missing.")
            return finish(result_file, result, counters, secret, done)
        if window > MAX_WINDOW_NS:
            result.update(kind="too_long", message="The window is above the 100 us cap.")
            return finish(result_file, result, counters, secret, done)
        path, library = node_file(req)
        if not nb.inside(path, library) or path.suffix != ".py" or not path.is_file():
            result.update(kind="failed", message="The node file is not in the library.")
            return finish(result_file, result, counters, secret, done)
        targets = req.get("targets")
        if not isinstance(targets, list) or not all(isinstance(t, str) for t in targets):
            result.update(kind="failed", message="The targets must be a list of names.")
            return finish(result_file, result, counters, secret, done)
        if path.stem.startswith("15p"):
            # The same refusal nodesim_build makes, reported as a refusal, not a breach.
            result.update(kind="refused", message="unsupported execute path")
            return finish(result_file, result, counters, secret, done)

        def stopping():
            return parent_gone.is_set() or (run_dir / "stop").exists()

        threading.Thread(target=watchdog, daemon=True, args=(
            float(req.get("limit_s") or LIMIT_S) - (time.monotonic() - started), done, box, counters,
            result_file, base, secret)).start()
        os.chdir(req["scratch"])
        guard = nb.Guard(req["scratch"])  # empty network allow-list during the build (D-8)
        status.begin("build")
        job = {"node_file": str(path), "library_root": req["library"], "chip_copy": req["chip_copy"],
               "scratch": req["scratch"]}
        nb.verify_env(job, guard)
        nb.verify_config_raw(guard)
        nb.preimport_trusted()  # D-33 / D-39: the one shared pre-import list
        try:
            # R3-1: loaded before node code so install_classes captures its classes.
            import qm_saas  # noqa: F401
        except ImportError:
            pass  # the sim step then refuses with "not captured", before any login
        guard.install_audit()
        import qm  # noqa: F401
        import qualibrate  # noqa: F401
        import qualang_tools.multi_user  # noqa: F401
        import quam.components  # noqa: F401
        notes = {}
        build_out = {"replaced_hosts": []}
        try:
            built = build_program(req, guard, build_out, notes)
        except Exception as exc:  # noqa: BLE001 -- every build failure is the user's answer
            if guard.verdict:
                raise
            text = str(exc)
            if text.startswith(SERIALIZATION):
                result.update(kind="serializer", message=scrub(text[len(SERIALIZATION):], secret))
            else:
                result.update(kind="node_error", message=scrub(text or type(exc).__name__, secret))
            result["logs"] = build_out.get("logs") or []
            result["traceback"] = scrub(traceback.format_exc()[-3000:], secret)
            status.end(write=False)
            return finish(result_file, result, counters, secret, done, guard, status)
        if guard.verdict:
            # A node that caught the guard's exception still never reaches the cloud step.
            raise RuntimeError(guard.verdict)
        result.update(script=built["script"], script_ok=built["script_ok"],
                      program_chars=built["program_chars"],
                      replaced_hosts=built["built"].get("replaced_hosts") or [],
                      dropped_overrides=notes.get("dropped_overrides") or [])
        if notes.get("script_error"):
            result["script_error"] = scrub(notes["script_error"], secret)
        if "timestamp_stream" in built["script"] or "with_timestamps" in built["script"]:
            result.update(kind="refused", message="it records timestamps, which this simulation does not run.")
            return finish(result_file, result, counters, secret, done, guard, status)
        guard.end_build()
        port = int(saas.get("port") or SAAS_PORT)
        guard.allow(resolve(saas["host"], port), write_roots=[run_dir])
        if stopping():
            raise Stopped()
        sim = simulate(saas, built, req, guard, status, stopping, counters, box)
        result.update(sim, ok=True, kind="ok", message="")
    except BaseException as exc:  # noqa: BLE001 -- every failure is the user's answer
        kind, message = classify(exc, guard, secret)
        result.update(kind=kind, message=message, ok=False,
                      traceback=scrub(traceback.format_exc()[-3000:], secret))
        if parent_gone.is_set() or (run_dir / "stop").exists():
            result["stopped"] = True
    finally:
        try:
            close_instance(box.get("inst"), counters)
        except Exception:  # noqa: BLE001
            pass
    if guard is not None and guard.verdict:
        result.update(ok=False, kind="guard", message=guard.verdict)
    return finish(result_file, result, counters, secret, done, guard, status)


def finish(result_file, result, counters, secret, done, guard=None, status=None):
    done.set()
    if status is not None:
        status.end(write=False)
        result["phases"] = list(status.phases)
    if guard is not None and guard.verdict:
        result["guard_lines"] = [guard.verdict]
    result["instances_opened"] = counters.get("opened", 0)
    result["instances_closed"] = counters.get("closed", 0)
    if counters.get("close_error"):
        result["close_error"] = scrub(counters["close_error"], secret)
    if result.get("kind") not in KINDS:
        result["kind"] = "failed"
    for key in ("message", "traceback", "script_error"):
        if isinstance(result.get(key), str):
            result[key] = scrub(result[key], secret)
    result_file.write(result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
