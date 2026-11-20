"""Offline worker protocol peer; no QM imports or network (P2 spec 5, Piece S)."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import time


def main():
    """Drive deterministic failures using a scratch plan file (D-10, P2 spec 5)."""
    module = importlib.util.spec_from_file_location("proto", sys.argv[1])
    proto = importlib.util.module_from_spec(module)
    module.loader.exec_module(proto)
    plan = json.loads(Path(sys.argv[2]).read_text(encoding="ascii"))
    ledger = Path(sys.argv[3])
    lease = None
    current = None

    def emit(ev, **fields):
        """Use the shared serializer so fake messages also obey the contract (spec 4)."""
        print(proto.encode(dict(ev=ev, **fields)), end="", flush=True)

    def result(stopped=False):
        """Return the snapshot identities and the fake module value (D-14, spec 4.3)."""
        source = plan.get("source")
        script = Path(source).read_text(encoding="ascii") if source else "program()"
        kind = "failed" if stopped else plan.get("kind", "ok")
        emit("result", **{k: current[k] for k in proto.SNAPSHOT}, ok=kind == "ok",
             kind=kind,
             message="Stopped." if stopped else plan.get("error", ""), report={}, script=script,
             script_ok=True, program_chars=len(script), qop_version="v3_7_3", window_ns=current["window_ns"],
             last_adc_end_ns=None, lease_seq=1 if lease else None, cluster_hash=proto.cluster_hash(spec),
             dropped_overrides=[], replaced_hosts=[])
        emit("idle")

    spec = dict(mw=[1], lf=[])
    for raw in sys.stdin:
        request = json.loads(raw)
        proto.validate(request)
        op = request["op"]
        with ledger.open("a", encoding="ascii") as stream:
            # Never put lease headers into a test ledger.
            stream.write(json.dumps(dict(op=op, pid=os.getpid(),
                                         saas_keys=[k for k in os.environ if k.startswith("SAAS_")])) + "\n")
        if op == "hello":
            if plan.get("mode") == "no_ready":
                continue
            library = Path(request["library"])
            source = plan.get("source")
            sources = {}
            if source:
                st = Path(source).stat()
                sources[source] = [st.st_mtime_ns, st.st_size]
            emit("ready", pid=os.getpid(), proto=1, imports_s=0,
                 quam_config_file=str(library / "quam_config" / "__init__.py"), qm_version="fake", sources=sources)
        elif op == "lease":
            lease = request
            emit("lease_ok", lease_seq=request["lease_seq"], connect_s=0)
            if current:
                emit("phase", click_id=current["click_id"], phase="wait", phases=[])
                if plan.get("mode") == "die_wait":
                    os._exit(7)
                if plan.get("mode") != "wait_stop":
                    result()
        elif op == "cluster":
            if plan.get("mode") == "cluster_fail":
                emit("cluster_failed", seq=request["seq"], message="fake layout error")
            elif plan.get("mode") != "hang_cluster":
                emit("cluster", seq=request["seq"], spec=spec, hash=proto.cluster_hash(spec))
        elif op == "drop_lease":
            lease = None
            emit("lease_dropped", lease_seq=request["lease_seq"])
        elif op == "click":
            current = request
            emit("build_started", click_id=current["click_id"])
            mode = plan.get("mode")
            if mode == "die_build":
                os._exit(8)
            if mode == "unmarked":
                print("unmarked", flush=True)
                continue
            if mode == "hang_build":
                continue
            emit("build_done", click_id=current["click_id"], s=0, sources_new={})
            if mode == "guard":
                emit("guard", click_id=current["click_id"], line="GUARD FAIL write refused")
                emit("bye", why="guard")
                break
            if mode == "before_submit":
                emit("phase", click_id=current["click_id"], phase="connect", phases=[])
                continue
            if not lease:
                emit("need_lease", click_id=current["click_id"], spec=spec,
                     hash=proto.cluster_hash(spec), reason="none")
            else:
                emit("phase", click_id=current["click_id"], phase="wait", phases=[])
                if mode == "die_wait":
                    os._exit(7)
                if mode != "wait_stop":
                    result()
        elif op == "stop":
            if current:
                result(True)
                current = None
        elif op == "exit":
            emit("bye", why="exit")
            break


def publish(path, data):
    """Write the owner's answer through tmp + replace, so a reader never sees half a file (review S P3-9)."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data), encoding="ascii")
    os.replace(tmp, path)


def owner(args):
    """Run a real second SM pool process to pin scratch isolation (D-14, S-14)."""
    from types import SimpleNamespace
    import subprocess
    import threading
    module_path, proto_path, directory, library, live, after = map(Path, args)
    module_spec = importlib.util.spec_from_file_location("owner_pool", module_path)
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    module.GENERATOR = proto_path.parent
    plan = directory / "owner-plan.json"
    ledger = directory / "owner-ops.jsonl"

    def spawn(argv, **kwargs):
        """Replace the lab-side entry point, retaining all production environment pins (D-7)."""
        kwargs.pop("prepend_lab")
        return subprocess.Popen([sys.executable, "-B", str(Path(__file__)), str(proto_path), str(plan), str(ledger)],
                                **kwargs)

    def worker_factory(*values, **kwargs):
        """Give the owner a real protocol subprocess with a bounded ready wait (spec 7.11)."""
        return module.WorkerClient(*values, **kwargs, spawn=spawn, ready_s=3)

    pool = module.WarmPool(directory / "instance", root=directory / "scratch", worker_factory=worker_factory,
                           known=lambda p: True, probe=lambda p: "1.2.0", directory=directory / "broker")
    result_file = directory / f"owner-{os.getpid()}.json"
    try:
        worker = pool.worker(sys.executable, library, live, dict(host="192.0.2.2"), lambda *a, **k: None)
        identity = worker.snapshot(SimpleNamespace(after=after, seq=1, store_id=str(os.getpid())), str(os.getpid()))
        ready = dict(scratch=str(worker.scratch), worker_pid=worker.proc.pid)
        publish(result_file, ready)
        deadline = time.monotonic() + 8
        while not (directory / "owners-continue").exists() and time.monotonic() < deadline:
            threading.Event().wait(.02)
        ready["identity_kept"] = json.loads((worker.chip / "_snapshot.json").read_text()) == identity
        publish(result_file, ready)
    finally:
        pool.close()


if __name__ == "__main__":
    if sys.argv[1] == "--owner":
        owner(sys.argv[2:])
    else:
        main()
