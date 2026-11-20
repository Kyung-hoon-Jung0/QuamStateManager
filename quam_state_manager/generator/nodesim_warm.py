"""Lab-side warm node simulator; JSON-lines IPC only (D-12, P2 spec 2 and 4)."""
import json
import os
from pathlib import Path
import queue
import ssl
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nodesim_build as nb
import nodesim_click as cold
import nodesim_proto as proto
from nodesim_warm_guard import WarmGuard


class Worker:
    """Own the once-installed guard, build holder and current lease (P2 spec 2.1-2.5)."""

    def __init__(self, output, inbox):
        """Keep protocol IO separate from untrusted stdout (P2 spec 4.1)."""
        self.output, self.inbox = output, inbox
        self.secrets = []
        self.guard = None
        self.holder = {}
        self.lease = None
        self.qmm = None
        self.sources = {}
        self.click_id = ""
        self.stopped = False
        self.gone = False
        self.phases = []
        self.phase_at = None
        self.phase_name = None
        self.native_threads = []

    def emit(self, ev, **fields):
        """Scrub every protocol field, including scripts and guard lines (D-11)."""
        line = proto.encode(dict(ev=ev, **fields))
        for secret in self.secrets:
            if not secret:
                continue
            line = line.replace(json.dumps(secret)[1:-1], "***")
        self.output.write(line)
        self.output.flush()

    def collect_sources(self):
        """Retain the first observed stat of each imported library module (D-5)."""
        for module_name, module in list(sys.modules.items()):
            if module_name == "_nodesim_node":
                continue
            name = getattr(module, "__file__", None)
            if name and nb.inside(name, self.job["library_root"]) and Path(name).suffix == ".py":
                path = str(Path(name).resolve())
                if path == self.holder["job"].get("node_file"):
                    continue
                if path not in self.sources:
                    stat = Path(path).stat()
                    self.sources[path] = [stat.st_mtime_ns, stat.st_size]
        self.holder["sources"].update(self.sources)
        return dict(self.sources)

    def start(self, hello):
        """Verify pins before importing the selected library, then install once (D-7, D-15)."""
        self.hello = hello
        self.job = dict(scratch=hello["scratch"], chip_copy=hello["chip_dir"], library_root=hello["library"])
        self.guard = WarmGuard(hello["scratch"])
        self.capture = Path(hello["scratch"]) / "_capture.txt"
        capture_fd = os.open(self.capture, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        for fd in (1, 2):
            os.dup2(capture_fd, fd)
        os.close(capture_fd)
        nb.verify_env(self.job, self.guard)
        nb.verify_config_raw(self.guard)
        nb.preimport_trusted()
        self.guard.install_audit()
        import qm
        import qualibrate  # noqa: F401
        import qualang_tools.multi_user  # noqa: F401
        import quam.components  # noqa: F401
        nb.install_once(self.job, self.guard, self.holder)
        self.emit("ready", pid=os.getpid(), proto=proto.PROTO, imports_s=time.monotonic() - self.started,
                  quam_config_file=str(self.holder["library"].__file__),
                  qm_version=str(getattr(qm, "__version__", "")), sources=self.collect_sources())

    def set_lease(self, message):
        """Accept JSON connection parameters without importing a SaaS client (D-11, D-13)."""
        seq = message["lease_seq"]
        try:
            if self.guard.phase != "idle" or self.click_id:
                raise ValueError("not between clicks")
            context = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.set_alpn_protocols(["h2"])
            params = dict(host=message["host"], port=message["port"], connection_headers=message["headers"],
                          credentials=context, follow_gateway_redirections=False)
            endpoints = cold.resolve(params["host"], params["port"])
            self.guard.replace_lease(params, endpoints)
            self.secrets.append(message["headers"]["simulation-auth"])
            self.lease, self.qmm = message, None
            self.emit("lease_ok", lease_seq=seq, connect_s=0.0)
        except (ValueError, OSError) as exc:
            self.emit("lease_failed", lease_seq=seq, message=str(exc))

    def pump(self):
        """Handle stop/EOF while native work runs, refusing mid-click swaps (P2 spec 2.5)."""
        for _ in range(100):
            try:
                message = self.inbox.get_nowait()
            except queue.Empty:
                break
            if message is None or message.get("op") == "exit":
                self.gone = self.stopped = True
            elif message["op"] == "stop":
                if message["click_id"] == self.click_id:
                    self.stopped = True
            elif message["op"] in ("lease", "drop_lease"):
                self.emit("lease_failed", lease_seq=message["lease_seq"], message="not between clicks")
            else:
                self.gone = self.stopped = True
        return self.stopped

    def phase(self, name):
        """Send measured phase durations, never a percentage (D-19)."""
        now = time.monotonic()
        if self.phase_at is not None:
            self.phases.append(dict(name=self.phase_name, s=now - self.phase_at))
        self.phase_at, self.phase_name = now, name
        self.emit("phase", click_id=self.click_id, phase=name, phases=list(self.phases))

    def truncate_capture(self):
        """Discard raw captured output between clicks before worker reuse (D-11)."""
        sys.stdout.flush()
        sys.stderr.flush()
        os.truncate(self.capture, 0)

    def click(self, request):
        """Build under the SM lock, request a matching lease, simulate and report (P2 spec 2.2)."""
        self.click_id = request["click_id"]
        self.stopped = False
        self.phases, self.phase_at = [], None
        result = {key: request[key] for key in proto.SNAPSHOT}
        result.update(ok=False, kind="failed", message="", report=None, script="", script_ok=False,
                      program_chars=0, qop_version="", window_ns=request["window_ns"], last_adc_end_ns=None,
                      lease_seq=self.lease["lease_seq"] if self.lease else None, cluster_hash="",
                      dropped_overrides=[], replaced_hosts=[])
        build_finished = False
        node_started = False
        try:
            self.truncate_capture()
            self.phase("build")
            self.emit("build_started", click_id=self.click_id)
            self.guard.enter_build()
            snapshot = json.loads((Path(self.job["chip_copy"]) / "_snapshot.json").read_text(encoding="utf-8"))
            try:
                proto.check_snapshot(request, snapshot)
            except ValueError:
                field = next((k for k in proto.SNAPSHOT if snapshot.get(k) != request[k]), None)
                if field is None:
                    raise
                result["message"] = f"chip copy {field} {snapshot.get(field)} is not the click's {request[field]}"
                raise ValueError(result["message"])
            if not 0 < request["window_ns"] <= cold.MAX_WINDOW_NS:
                result["kind"] = "too_long"
                raise ValueError("The window is above the 100 us cap.")
            path = (Path(self.job["library_root"]) / request["node"]).resolve()
            if not nb.inside(path, self.job["library_root"]) or path.suffix != ".py":
                raise ValueError("The node file is not in the library.")
            job = dict(self.job, node_file=str(path), overrides={})
            notes = {}
            nb.stub_class = cold.click_stub(cold.STUB_CLASS, request["overrides"], request["targets"], notes)
            node_started = True
            out = nb.build_one(job, self.guard, result, self.holder)
            node = notes["stub"].instances[0]
            built = cold.post_build(out, notes)
            config, script = built["config"], built["script"]
            from qm import QuantumMachinesManager, SimulationConfig
            spec = proto.cluster_spec(config)
            hash_value = proto.cluster_hash(spec)
            result.update(script=script, script_ok=built["script_ok"],
                          program_chars=built["program_chars"], cluster_hash=hash_value,
                          dropped_overrides=notes.get("dropped_overrides", []))
            if notes.get("script_error"):
                result["script_error"] = notes["script_error"]
            self.guard.exit_build()
            build_finished = True
            self.emit("build_done", click_id=self.click_id, s=time.monotonic() - self.phase_at,
                      sources_new=self.collect_sources())
            if "timestamp_stream" in script or "with_timestamps" in script:
                result["kind"] = "refused"
                raise ValueError("it records timestamps, which this simulation does not run.")
            self.phase("lease")
            if not self.lease or self.lease["cluster_hash"] != hash_value:
                self.emit("need_lease", click_id=self.click_id, spec=spec, hash=hash_value,
                          reason="hash_changed" if self.lease else "none")
                deadline = time.monotonic() + 300
                while not self.lease or self.lease["cluster_hash"] != hash_value:
                    if time.monotonic() >= deadline:
                        raise cold.WaitCapHit("lease after 300 s")
                    try:
                        message = self.inbox.get(timeout=0.2)
                    except queue.Empty:
                        continue
                    if message and message["op"] == "stop" and message["click_id"] != self.click_id:
                        continue
                    if message is None or message["op"] in ("exit", "stop"):
                        self.gone = message is None or message["op"] == "exit"
                        raise cold.Stopped()
                    if message["op"] == "lease":
                        active, self.click_id = self.click_id, ""
                        self.set_lease(message)
                        self.click_id = active
                    else:
                        self.gone = True
                        raise cold.Stopped()
            if self.pump():
                raise cold.Stopped()
            result.update(lease_seq=self.lease["lease_seq"], qop_version=self.lease["version"])
            self.guard.enter_sim()
            if self.qmm is None:
                self.phase("connect")
                self.qmm = self.bounded(lambda: QuantumMachinesManager(**self.guard.lease_params),
                                        30, self.pump, "connect")
            if self.pump():
                raise cold.Stopped()
            self.phase("submit")
            job = self.bounded(lambda: self.qmm.simulate(config, node.namespace["qua_program"],
                                                       SimulationConfig(duration=request["window_ns"] // 4)),
                               request["wait_cap_s"], self.pump, "submit")
            self.phase("wait")
            self.bounded(lambda: job.wait_until("Done", timeout=request["wait_cap_s"]),
                         request["wait_cap_s"], self.pump, "wait_until Done")
            self.phase("report")
            report = self.bounded(lambda: job.get_simulated_waveform_report().to_dict(),
                                  30, self.pump, "report")
            ends = [a.get("end_time", 0) for a in report.get("adc_acquisitions", [])]
            if self.pump():
                raise cold.Stopped()
            result.update(ok=True, kind="ok", report=report, last_adc_end_ns=max(ends) if ends else None)
        except BaseException as exc:
            if self.guard.verdict:
                raise
            if isinstance(exc, cold.Stopped):
                result.update(kind="failed", message="Stopped.")
                self.gone = self.gone or any(t.is_alive() for t in self.native_threads)
            elif isinstance(exc, cold.WaitCapHit):
                result.update(kind="timeout", message=str(exc))
                self.gone = True
            else:
                kind, message = cold.classify(exc, self.guard, "")
                if not build_finished and node_started:
                    text = str(exc)
                    kind = "serializer" if text.startswith(cold.SERIALIZATION) else "node_error"
                    message = text.removeprefix(cold.SERIALIZATION) or type(exc).__name__
                elif result["kind"] != "failed":
                    kind, message = result["kind"], str(exc)
                result.update(kind=kind, message=result["message"] or message)

        finally:
            if not self.guard.verdict:
                if self.guard.phase == "build":
                    self.guard.exit_build()
                elif self.guard.phase == "sim":
                    self.guard.exit_sim()
        if self.guard.verdict:
            raise RuntimeError(self.guard.verdict)
        if node_started and not build_finished:
            self.emit("build_done", click_id=self.click_id, s=time.monotonic() - self.phase_at,
                      sources_new=self.collect_sources())
        self.truncate_capture()
        self.emit("result", **result)
        self.click_id = ""
        self.emit("idle")
        if self.gone:
            self.emit("bye", why="exit")

    def bounded(self, fn, cap_s, stopping, what):
        """Track native call threads so Stop retires only a still-running call (P2 spec 2.5)."""
        self.native_threads = [t for t in self.native_threads if t.is_alive()]

        def tracked():
            """Record the actual cold bounded-call thread (P2 spec 2.5)."""
            self.native_threads.append(threading.current_thread())
            return fn()

        return cold.bounded(tracked, cap_s, stopping, what)

    def cluster(self, request):
        """Generate the current copy's FEM spec without running a node (P2 spec 2.6)."""
        self.guard.enter_build()
        try:
            for filename in ("state.json", "wiring.json"):
                path = Path(self.job["chip_copy"]) / filename
                clean, _ = nb.sanitize(json.loads(path.read_text(encoding="utf-8")))
                path.write_text(json.dumps(clean), encoding="utf-8")
            config = self.holder["library"].Quam.load().generate_config()
            spec = proto.cluster_spec(config)
        finally:
            self.guard.exit_build()
        self.emit("cluster", seq=request["seq"], spec=spec, hash=proto.cluster_hash(spec))

    def run(self):
        """Serve bounded idle requests; EOF ends the worker (P2 spec 2.5)."""
        self.started = time.monotonic()
        try:
            hello = self.inbox.get(timeout=60)
            if hello is None:
                return
            if hello.get("proto") != proto.PROTO:
                self.emit("bye", why="proto_mismatch")
                return
            if any(key.startswith("SAAS_") for key in os.environ):
                self.emit("bye", why="saas_env")
                return
            proto.validate(hello)
            if hello["op"] != "hello":
                raise ValueError("expected hello")
            self.start(hello)
            idle_s = min(1200, max(0.001, hello["idle_s"]))
            idle_deadline = time.monotonic() + idle_s
            while not self.gone:
                try:
                    left = idle_deadline - time.monotonic()
                    if left <= 0:
                        raise queue.Empty
                    message = self.inbox.get(timeout=left)
                except queue.Empty:
                    self.emit("bye", why="idle")
                    return
                if message is None or message["op"] == "exit":
                    self.emit("bye", why="exit")
                    return
                proto.validate(message)
                op = message["op"]
                if op == "lease":
                    self.set_lease(message)
                elif op == "drop_lease":
                    self.guard.replace_lease(None, [])
                    self.lease = self.qmm = None
                    self.emit("lease_dropped", lease_seq=message["lease_seq"])
                elif op == "click":
                    try:
                        self.click(message)
                    except BaseException:
                        if not self.guard.verdict:
                            raise
                        self.emit("guard", click_id=self.click_id, line=self.guard.verdict)
                        self.emit("bye", why="guard")
                        return  # Guard retirement: P2 spec 2.3, Q-9.
                    idle_deadline = time.monotonic() + idle_s
                elif op == "cluster":
                    self.cluster(message)
                elif op != "stop":
                    raise ValueError("unexpected operation")
        except BaseException:
            if self.guard and self.guard.verdict:
                self.emit("guard", click_id=self.click_id, line=self.guard.verdict)
                self.emit("bye", why="guard")
            else:
                self.emit("bye", why="protocol_error")
        finally:
            if hasattr(self, "capture"):
                sys.stdout.flush()
                sys.stderr.flush()
                null = os.open(os.devnull, os.O_WRONLY)
                for fd in (1, 2):
                    os.dup2(null, fd)
                os.close(null)
                self.capture.unlink(missing_ok=True)


def main():
    """Duplicate protocol stdout before any library can print (P2 spec 4.1)."""
    output = os.fdopen(os.dup(1), "w", encoding="ascii", buffering=1)
    inbox = queue.Queue()
    stream = cold.detach_stdin()

    def read():
        """One stdin reader; EOF wakes the bounded native-work polls (P2 spec 2.5)."""
        try:
            for line in stream:
                message = json.loads(line)
                inbox.put(message)
        except (ValueError, OSError):
            pass
        finally:
            inbox.put(None)

    threading.Thread(target=read, daemon=True).start()
    Worker(output, inbox).run()
    output.close()
    # Daemon native calls may remain after Stop; SM must retire this worker (P2 spec 2.5).
    os._exit(0)


if __name__ == "__main__":
    main()
