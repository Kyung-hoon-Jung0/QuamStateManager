"""In-memory fake broker transport for SM verdict tests (D-10, P2 spec 5)."""
import threading
from datetime import datetime, timedelta, timezone


class FakeBroker:
    """Record protocol operations and model lease adoption without a SaaS client (spec 4.5)."""

    def __init__(self, directory=None, python=None, credentials=None):
        """Keep tokens in memory only (D-11)."""
        self.calls, self.pushes = [], []
        self.disconnected = threading.Event()
        self.host, self.state = "192.0.2.1", "lease"
        self.spawning = threading.Event()
        self.gate = None
        self.closed = False
        self.lease = dict(instance_id="instance-a", host=self.host, port=443,
                          headers={"simulation-id": "instance-a", "simulation-auth": "token-sentinel-93"},
                          expires_at=(datetime.now(timezone.utc) + timedelta(minutes=14)).isoformat(),
                          version="v3_7_3", cluster_hash="")

    def send(self, message):
        """Record JSON operations, never pickle (spec 4.5)."""
        self.calls.append(message)

    def request(self, message, *, stop=None, on_event=lambda event: None, cap=300):
        """Answer deterministic acquisition and status requests (spec 4.5)."""
        from quam_state_manager.core.nodesim_pool import PoolError
        self.calls.append(message)
        if message["op"] == "status":
            return dict(ev="status", rows=[], in_use_here=1, limit=3)
        self.spawning.set()
        on_event(dict(ev="spawning", req=message["req"], in_use_here=1, limit=3))
        if self.gate:
            for _ in range(200):
                if stop and stop.is_set():
                    self.send(dict(op="cancel", req=message["req"]))
                    raise PoolError("stopped_spawning")
                if self.gate.wait(.01):
                    break
            else:
                raise PoolError("broker_failed", message="Fake spawn cap hit.")
        if self.state == "no_slot":
            return dict(ev="no_slot", req=message["req"], in_use_here=3, earliest_close=self.lease["expires_at"])
        if self.state != "lease":
            return dict(ev=self.state, req=message["req"], line="fake refusal")
        self.lease.update(host=self.host, cluster_hash=message["hash"])
        return dict(ev="lease", req=message["req"], spawned=True, spawn_s=0, **self.lease)

    def close(self):
        """Track connection closure without ending leases (D-17)."""
        self.closed = True


def serve(directory):
    """Serve actual authenticated JSON bytes with token-free disk rows (P2 spec 4.5, S-9)."""
    import json
    import os
    from pathlib import Path
    import secrets
    import sys
    import time
    import uuid
    from multiprocessing.connection import Listener
    import psutil
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    endpoint = r"\\.\pipe\nodesim-test-" + uuid.uuid4().hex
    key = secrets.token_bytes(32)
    listener = Listener(endpoint, authkey=key)
    (directory / "broker.key").write_bytes(key)
    record = dict(proto=1, pid=os.getpid(), created=psutil.Process().create_time(), pipe=endpoint,
                  env=sys.executable, qm_saas="fake", account="fake")
    (directory / "broker.json").write_text(json.dumps(record), encoding="ascii")
    fake = FakeBroker()
    lifetime = threading.Timer(60, lambda: os._exit(0))
    lifetime.daemon = True
    lifetime.start()

    def handle(connection):
        """Bound each test connection and support an injected idle tick (spec 7.11)."""
        deadline = time.monotonic() + 60
        try:
            while time.monotonic() < deadline:
                if (directory / "idle_tick").exists():
                    (directory / "idle_tick").unlink()
                    connection.send_bytes(json.dumps(dict(ev="closed", instance_id="instance-a",
                                                          why="idle")).encode())
                if not connection.poll(.05):
                    continue
                request = json.loads(connection.recv_bytes(1048576))
                op = request["op"]
                with (directory / "ops.jsonl").open("a", encoding="ascii") as stream:
                    stream.write(json.dumps({k: request[k] for k in ("op", "why") if k in request}) + "\n")
                if op == "hello":
                    event = dict(ev="hello_ok", account="fake", qm_saas_file=str(Path(__file__)))
                elif op in ("acquire", "status"):
                    event = fake.request(request)
                elif op == "bye":
                    break
                else:
                    continue
                connection.send_bytes((json.dumps(event) + "\n").encode())
        except (EOFError, OSError):
            pass
        finally:
            connection.close()
    accept_deadline = time.monotonic() + 60
    while time.monotonic() < accept_deadline:
        connection = listener.accept()
        threading.Thread(target=handle, args=(connection,), daemon=True).start()


if __name__ == "__main__":
    import sys
    serve(sys.argv[1])
