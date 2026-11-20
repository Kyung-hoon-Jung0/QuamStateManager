"""Isolated account broker (D-11, D-17; P2 spec 3 and 4.5)."""

import ctypes
import datetime as dt
import hashlib
import importlib.metadata
import importlib.util
import json
import logging
from multiprocessing import AuthenticationError
from multiprocessing.connection import Listener, answer_challenge, deliver_challenge
import os
from pathlib import Path
import sys
import threading
import time
import uuid


def load_file(name, path):
    """Load the two shared stdlib modules under isolation (P2 spec 3.1)."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HERE = Path(__file__).resolve().parent
proto = load_file("broker_proto", HERE / "nodesim_proto.py")
xlock = load_file("broker_xlock", HERE.parent / "core" / "xlock.py")
clock = time.time
SPAWN_CAP = 240
REAP_S = 30
RENEW_S = 5
EMPTY_S = 60
HANDSHAKE_S = 5


def broker_argv(python, directory):
    """Return the production command consumed by SM (P2 spec 3.1)."""
    return [str(python), "-I", "-B", str(HERE / "nodesim_broker.py"), str(directory)]


def process_created(pid):
    """Read process identity without importing SM or a lab package (D-17(b))."""
    if os.name == "nt":
        from ctypes import wintypes
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenProcess.restype = wintypes.HANDLE
        api.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        api.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = api.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            code = wintypes.DWORD()
            if not api.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:
                return None
            values = [wintypes.FILETIME() for _ in range(4)]
            if not api.GetProcessTimes(handle, *(ctypes.byref(value) for value in values)):
                return None
            value = values[0]
            return ((value.dwHighDateTime << 32) | value.dwLowDateTime) / 1e7 - 11644473600
        finally:
            api.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        boot = next(line.split()[1] for line in Path("/proc/stat").read_text().splitlines()
                    if line.startswith("btime "))
        return int(boot) + int(fields[19]) / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, StopIteration):
        return None


def alive(pid, created):
    """Reject PID reuse as well as process exit (D-17(b))."""
    current = process_created(pid)
    return current is not None and abs(current - created) < 0.01


def user_identity():
    """Get the SID used to scope the pipe (P2 spec 3.1)."""
    if os.name != "nt":
        return str(os.getuid())
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                         wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        size = wintypes.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        text = wintypes.LPWSTR()
        if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(text)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return text.value
        finally:
            kernel.LocalFree(text)
    finally:
        kernel.CloseHandle(token)


def atomic(path, value):
    """Replace one identity or table file (P2 spec 3.1-3.2)."""
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        tmp.write_bytes(value if isinstance(value, bytes) else json.dumps(value).encode("ascii"))
        for attempt in range(50):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 49:
                    raise
                time.sleep(0.01)
    finally:
        tmp.unlink(missing_ok=True)


def epoch(value):
    """Read the server's expiry, never infer its lifetime (D-17)."""
    return dt.datetime.fromisoformat(value).timestamp() if isinstance(value, str) else value.timestamp()


class Peer:
    """Serialize bounded byte-only writes to one SM connection (P2 spec 4.5)."""

    def __init__(self, connection):
        """Bind this authenticated pipe (P2 spec 3.1)."""
        self.connection = connection
        self.lock = threading.Lock()
        self.owner = None

    def send(self, event, **fields):
        """Send UTF-8 JSON without pickle or protocol markers (P2 spec 4.1)."""
        message = dict(ev=event, **fields)
        proto.validate(message)
        with self.lock:
            timer = threading.Timer(HANDSHAKE_S, self.connection.close)
            timer.daemon = True
            timer.start()
            try:
                self.connection.send_bytes((json.dumps(message, ensure_ascii=True) + "\n").encode("utf-8"))
            except (OSError, EOFError):
                pass
            finally:
                timer.cancel()


class Broker:
    """Own durable leases and memory-only credentials (D-11, D-17)."""

    def __init__(self, directory, module):
        """Initialize only after the election lock is held (P2 spec 3.1)."""
        self.directory, self.module = Path(directory), module
        self.lock = threading.RLock()
        self.login_lock = threading.RLock()
        self.secrets = set()
        self.credentials = None
        self.client = None
        previous = self.directory / "broker.json"
        self.account = json.loads(previous.read_text(encoding="ascii")).get("account", "") if previous.exists() else ""
        self.peers = set()
        self.leases = {}
        self.requests = {}
        self.swaps = {}
        self._specs = {}
        self.closing = set()
        self.tasks = threading.BoundedSemaphore(8)
        self.last_home = clock()
        self.created = process_created(os.getpid())
        self.log_path = self.directory / "broker.log"
        self.log_path.write_text("", encoding="ascii")
        self.sdk_log = logging.Logger("nodesim_saas", level=logging.CRITICAL + 1)
        self.sdk_log.addHandler(logging.NullHandler())
        self.sdk_log.propagate = False
        table = self.directory / "leases.json"
        self.rows = json.loads(table.read_text(encoding="ascii"))["rows"] if table.exists() else []
        self.key = os.urandom(32)
        digest = hashlib.sha256((user_identity() + os.path.normcase(str(self.directory.resolve())))
                                .encode("utf-8")).hexdigest()[:24]
        self.address = ("\\\\.\\pipe\\quam-sm-saas-" + digest if os.name == "nt"
                        else str(self.directory / (digest + ".sock")))
        atomic(self.directory / "broker.key", self.key)
        self.identity()

    def identity(self):
        """Publish process, account and interpreter provenance (P2 spec 3.1)."""
        version = getattr(self.module, "__version__", None)
        if version is None:
            try:
                version = importlib.metadata.version("qm-saas")
            except importlib.metadata.PackageNotFoundError:
                version = "unknown"
        atomic(self.directory / "broker.json", dict(proto=proto.PROTO, pid=os.getpid(), created=self.created,
               pipe=self.address, env=sys.executable, qm_saas=version,
               account=self.account))

    def scrub(self, value):
        """Remove all known password, session and token values (D-11)."""
        text = str(value)
        for secret in sorted(self.secrets, key=len, reverse=True):
            if secret:
                text = text.replace(secret, "***")
        return text

    def log(self, value):
        """Keep the sole bounded scrubbed log after election (P2 spec 3.1)."""
        with self.lock:
            line = self.scrub(value).splitlines()[0] if str(value) else "error"
            data = (line + "\n").encode("ascii", "backslashreplace")[:1048500]
            if self.log_path.stat().st_size + len(data) > 1048576:
                self.log_path.write_text("log truncated\n", encoding="ascii")
            with self.log_path.open("ab") as handle:
                handle.write(data)

    def save(self):
        """Persist token-free rows under the shared file lock (D-17(a))."""
        with xlock.held(self.directory / "leases.lock", timeout=5):
            atomic(self.directory / "leases.json", dict(format=1, rows=self.rows))

    def task(self, function, *args):
        """Bound SDK activity without blocking the reaper or pipe loop (P2 spec 3.3)."""
        if not self.tasks.acquire(blocking=False):
            return False

        def run():
            """Release the SDK task slot even on failure (P2 spec 3.3)."""
            try:
                function(*args)
            except Exception as exc:
                self.log(exc)
            finally:
                self.tasks.release()

        threading.Thread(target=run, daemon=True).start()
        return True

    def login(self, credentials=None):
        """Replace credentials only after a successful login (P2 spec 3.5)."""
        with self.login_lock:
            candidate = dict(credentials or self.credentials or {})
            with self.lock:
                self.secrets.add(candidate.get("password", ""))
            fresh = self.module.QmSaas(**candidate, auto_cleanup=False, log=self.sdk_log)
            if not callable(getattr(fresh._client, "close_simulator", None)):
                raise RuntimeError("qm-saas close-by-id capability is unavailable")
            with self.lock:
                self.secrets.add(getattr(fresh._client, "_jwt", ""))
                self.credentials, self.client = candidate, fresh
            return fresh

    def retry(self, operation):
        """Retry an untyped SDK failure once after fresh login (P2 spec 3.3)."""
        try:
            return operation(self.client)
        except Exception as exc:
            self.log(exc)
            return operation(self.login())

    def push(self, row, event, **fields):
        """Deliver a lease event to every connection of its owner (P2 spec 4.5)."""
        with self.lock:
            peers = [peer for peer in self.peers if peer.owner == (row["owner_pid"], row["owner_created"])]
        for peer in peers:
            peer.send(event, **fields)

    def close(self, row, why):
        """Claim a close once and retain failures for reaping (D-17(c))."""
        with self.lock:
            if row not in self.rows or row["row"] in self.closing or not row["instance_id"]:
                return
            if self.client is None:
                return
            self.closing.add(row["row"])
            row["state"] = "closing"
            self.save()
        try:
            self.retry(lambda client: client._client.close_simulator(row["instance_id"]))
        except Exception as exc:
            self.log(exc)
        else:
            with self.lock:
                if row in self.rows:
                    self.rows.remove(row)
                self.leases.pop(row["instance_id"], None)
                self.swaps.pop(row["instance_id"], None)
                self.save()
            self.push(row, "closed", instance_id=row["instance_id"], why=why)
        finally:
            with self.lock:
                self.closing.discard(row["row"])

    def acquire(self, peer, message, old=None):
        """Reserve a durable slot before any SDK spawn (D-17(a), P2 spec 3.3)."""
        with self.lock:
            if message["owner"]["sm_pid"] != peer.owner[0]:
                raise ValueError("owner mismatch")
            if old is None and message["reason"] == "renew":
                old = next((row for row in self.rows if row["state"] == "live"
                            and (row["owner_pid"], row["owner_created"]) == peer.owner
                            and row["cluster_hash"] == message["hash"]), None)
            if not old:
                for row in self.rows:
                    lease = self.leases.get(row["instance_id"])
                    if (row["state"] == "live" and row["owner_pid"] == peer.owner[0]
                            and row["owner_created"] == peer.owner[1] and row["cluster_hash"] == message["hash"]
                            and row["version"] == message["version"] and lease):
                        peer.send("lease", req=message["req"], **lease, spawned=False, spawn_s=0)
                        return
            if len(self.rows) >= 3:
                expires = [row["expires_at"] for row in self.rows if row["expires_at"]]
                peer.send("no_slot", in_use_here=len(self.rows), earliest_close=min(expires, default=None))
                return
            row = dict(row=uuid.uuid4().hex, state="pending", instance_id=None, pending_at=clock(),
                       spawned_at=None, expires_at=None, version=message["version"], cluster_hash=message["hash"],
                       owner_pid=peer.owner[0], owner_created=peer.owner[1], worker_pid=message["owner"]["worker_pid"],
                       broker_pid=os.getpid(), broker_created=self.created, purpose="nodesim", last_click_at=None,
                       in_job=None, close_on_ready=False, spec=message["spec"])
            self.rows.append(row)
            self.requests[(peer.owner, message["req"])] = row
            self.save()
            peer.send("spawning", req=message["req"], in_use_here=len(self.rows), limit=3)
        if not self.task(self.spawn, row, peer, message, old):
            with self.lock:
                self.rows.remove(row)
                self.save()
            peer.send("spawn_failed", line="The cloud simulator is busy.")

    def spawn(self, row, peer, message, old):
        """Record an id immediately on return and close cancelled or late returns (D-17(b))."""
        started = time.monotonic()

        def launch(client):
            """Use the shared cluster mapping and the SDK's spawn surface (P2 spec 4.4)."""
            instance = client.simulator(message["version"], proto.cluster_config(message["spec"], self.module))
            instance.spawn()
            return instance

        try:
            instance = self.retry(launch)
            with self.lock:
                late = row["state"] == "ghost" or row not in self.rows or clock() - row["pending_at"] > SPAWN_CAP
                row.update(instance_id=instance.id, expires_at=instance.expires_at.isoformat(), spawned_at=clock(),
                           state="live")
                if row not in self.rows:
                    self.rows.append(row)
                self.save()
                self.secrets.add(instance.token)
            if late or row["close_on_ready"]:
                self.close(row, "release")
                return
            if not proto.is_allowed_saas_host(instance.host, instance.port):
                self.close(row, "release")
                peer.send("spawn_failed", line="The cloud simulator returned a forbidden host or port.")
                return
            lease = dict(instance_id=instance.id, host=instance.host, port=instance.port,
                         headers={"simulation-id": instance.id, "simulation-auth": instance.token},
                         expires_at=row["expires_at"], version=row["version"], cluster_hash=row["cluster_hash"])
            with self.lock:
                self.leases[instance.id] = lease
                if old:
                    self.swaps[instance.id] = (old["instance_id"], clock())
            if old:
                self.push(row, "replaced", old=old["instance_id"], lease=lease)
            else:
                peer.send("lease", req=message["req"], **lease, spawned=True, spawn_s=time.monotonic() - started)
        except Exception as exc:
            with self.lock:
                if row in self.rows and not row["instance_id"]:
                    self.rows.remove(row)
                    self.save()
            self.log(exc)
            peer.send("spawn_failed", line=self.scrub(exc).splitlines()[0])

    def hello(self, peer, message):
        """Authenticate account and owner identity before accepting operations (P2 spec 3.5)."""
        if message.get("proto") != proto.PROTO:
            peer.send("proto_mismatch", proto=proto.PROTO)
            return False
        proto.validate(message)
        account = hashlib.sha256(message["creds"]["email"].encode("utf-8")).hexdigest()[:12]
        with self.login_lock:
            if self.account and account != self.account:
                peer.send("account_mismatch")
                return False
            if not alive(message["sm_pid"], message["sm_created"]):
                peer.send("login_failed", line="The owner process is unavailable.")
                return False
            try:
                if self.credentials != message["creds"]:
                    self.login(message["creds"])
            except Exception as exc:
                self.log(exc)
                peer.send("login_failed", line=self.scrub(exc).splitlines()[0])
                return False
            with self.lock:
                self.account = account
                self.identity()
                peer.owner = (message["sm_pid"], message["sm_created"])
                self.peers.add(peer)
        peer.send("hello_ok", account=account, qm_saas_file=str(Path(self.module.__file__).resolve()))
        self.reap()
        return True

    def dispatch(self, peer, message):
        """Apply owner-scoped byte protocol operations (P2 spec 4.5)."""
        proto.validate(message)
        op = message["op"]
        if op == "hello":
            self.hello(peer, message)
            return
        if peer.owner is None:
            raise ValueError("hello required")
        if op == "acquire":
            self.acquire(peer, message)
            return
        with self.lock:
            owned = [row for row in self.rows
                     if (row["owner_pid"], row["owner_created"]) == peer.owner]
            if op == "status":
                peer.send("status", rows=[dict(row) for row in self.rows], in_use_here=len(self.rows), limit=3)
            elif op == "cancel":
                row = self.requests.get((peer.owner, message["req"]))
                if row in owned:
                    row["close_on_ready"] = True
                    self.save()
                    if row["instance_id"]:
                        self.task(self.close, row, "release")
                peer.send("cancelled", req=message["req"])
            elif op == "swapped":
                swap = self.swaps.get(message["new"])
                if swap and swap[0] == message["old"]:
                    self.swaps.pop(message["new"], None)
                    for row in owned:
                        if row["instance_id"] == message["old"]:
                            self.task(self.close, row, "release")
            else:
                for row in owned:
                    if row["instance_id"] != message.get("instance_id"):
                        continue
                    if op == "touch":
                        row["last_click_at"] = min(clock(), message["click_at"])
                    elif op == "in_job":
                        row["in_job"] = message["click_id"]
                    elif op == "release":
                        self.task(self.close, row, "release")
                self.save()

    def disconnected(self, peer):
        """Pipe loss alone never closes a living owner's lease (D-17(b), review_r2 P2-C)."""
        with self.lock:
            self.peers.discard(peer)
            self.last_home = clock()
            rows = [row for row in self.rows
                    if peer.owner == (row["owner_pid"], row["owner_created"])]
        for row in rows:
            if not alive(row["owner_pid"], row["owner_created"]):
                self.task(self.close, row, "release")

    def reap(self):
        """Handle each durable row independently at startup and every 30 s (D-17(b-c))."""
        with self.lock:
            rows = list(self.rows)
        for row in rows:
            try:
                with self.lock:
                    now = clock()
                    if row not in self.rows:
                        continue
                    if row["state"] == "pending" and now - row["pending_at"] > SPAWN_CAP:
                        row.update(state="ghost", expires_at=dt.datetime.fromtimestamp(
                            row["pending_at"] + 960, dt.timezone.utc).isoformat())
                    if row["expires_at"] and now >= epoch(row["expires_at"]):
                        self.rows.remove(row)
                        self.leases.pop(row["instance_id"], None)
                        self.save()
                        if row["instance_id"]:
                            self.push(row, "closed", instance_id=row["instance_id"], why="expired")
                        continue
                    self.save()
                    if row["state"] == "ghost" or row["state"] == "pending":
                        continue
                    owner_alive = alive(row["owner_pid"], row["owner_created"])
                    if row["state"] == "live" and owner_alive:
                        if not alive(row["broker_pid"], row["broker_created"]):
                            row.update(broker_pid=os.getpid(), broker_created=self.created)
                            self.save()
                    basis = row["last_click_at"] if row["last_click_at"] is not None else row["spawned_at"]
                    why = "reaper" if not owner_alive else "idle"
                    if row["state"] == "closing" or not owner_alive or (
                            not row["in_job"] and basis is not None and now - basis > 600):
                        self.task(self.close, row, why)
            except Exception as exc:
                self.log(exc)

    def renew(self):
        """Offer or prepare a replacement; defer the old close until acknowledgement (D-17(d))."""
        with self.lock:
            for new, (old, at) in list(self.swaps.items()):
                if clock() - at >= 120:
                    for row in self.rows:
                        if row["instance_id"] == new:
                            self.task(self.close, row, "release")
            for row in list(self.rows):
                if row["state"] != "live" or not 0 < epoch(row["expires_at"]) - clock() < 150:
                    continue
                if row["instance_id"] in self.swaps or any(
                        item[0] == row["instance_id"] for item in self.swaps.values()):
                    continue
                # A replacement still in spawn has no id to enter in swaps yet.
                if any(other is not row and other["owner_pid"] == row["owner_pid"]
                       and other["cluster_hash"] == row["cluster_hash"] for other in self.rows):
                    continue
                peers = [peer for peer in self.peers if peer.owner == (row["owner_pid"], row["owner_created"])]
                if (len(self.rows) <= 1 and row["last_click_at"] is not None
                        and clock() - row["last_click_at"] <= 120 and peers):
                    request = self._specs.get((row["owner_pid"], row["cluster_hash"]))
                    if request is None and row.get("spec") is not None:
                        request = dict(owner=dict(sm_pid=row["owner_pid"], worker_pid=row["worker_pid"] or 0),
                                       version=row["version"], spec=row["spec"], hash=row["cluster_hash"])
                    if request:
                        self.acquire(peers[0], dict(request, req=uuid.uuid4().hex, reason="renew"), row)
                        continue
                self.push(row, "renew_offer", instance_id=row["instance_id"], expires_at=row["expires_at"])

    def serve_peer(self, connection, slots):
        """Authenticate away from accept, with a cap for silent clients (P2 spec 3.1)."""
        peer = Peer(connection)
        timer = threading.Timer(HANDSHAKE_S, connection.close)
        timer.daemon = True
        timer.start()
        try:
            deliver_challenge(connection, self.key)
            answer_challenge(connection, self.key)
            timer.cancel()
            deadline = time.monotonic() + HANDSHAKE_S
            while True:
                if not connection.poll(0.2):
                    if peer.owner is None and time.monotonic() > deadline:
                        break
                    continue
                message = json.loads(connection.recv_bytes(1048576).decode("utf-8"))
                if message.get("op") == "hello":
                    if not self.hello(peer, message):
                        continue
                elif message.get("op") == "bye":
                    break
                else:
                    if message.get("op") == "acquire" and peer.owner:
                        with self.lock:
                            self._specs[(peer.owner[0], message["hash"])] = message
                    self.dispatch(peer, message)
        except (OSError, EOFError, ValueError, AuthenticationError):
            pass
        finally:
            timer.cancel()
            self.disconnected(peer)
            connection.close()
            slots.release()

    def run(self):
        """Run independent accept, reaper and renewal loops (P2 spec 3.1-3.4)."""
        slots = threading.BoundedSemaphore(8)
        listener = Listener(self.address, authkey=self.key)

        def accept():
            """Keep inline Listener authentication off the accept path (review_r1 P3-5)."""
            while True:
                try:
                    connection = listener._listener.accept()
                except OSError:
                    return
                if slots.acquire(blocking=False):
                    threading.Thread(target=self.serve_peer, args=(connection, slots), daemon=True).start()
                else:
                    connection.close()

        threading.Thread(target=accept, daemon=True).start()
        last_reap = last_renew = float("-inf")
        try:
            while True:
                now = clock()
                if now - last_reap >= REAP_S:
                    self.reap()
                    last_reap = now
                if now - last_renew >= RENEW_S:
                    self.renew()
                    last_renew = now
                with self.lock:
                    if self.peers:
                        self.last_home = now
                    if not self.peers and not any(row["state"] != "ghost" for row in self.rows):
                        if now - self.last_home >= EMPTY_S:
                            return
                time.sleep(0.05)
        finally:
            listener.close()


def main(directory=None):
    """Elect before writing identity files or the log (P2 spec 3.1)."""
    directory = Path(directory or sys.argv[1]).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    try:
        with xlock.held(directory / "broker.lock", timeout=0.5):
            import qm_saas
            Broker(directory, qm_saas).run()
    except xlock.LockTimeout:
        print(json.dumps(dict(ev="already_running")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
