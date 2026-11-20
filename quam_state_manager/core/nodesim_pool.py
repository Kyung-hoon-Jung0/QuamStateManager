"""SM-owned warm workers and broker transport (D-11..D-19, P2 spec 2-4)."""
from __future__ import annotations

import atexit
import hashlib
import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

import psutil

from quam_state_manager.core import lab_worker, nodesim_spawn
from quam_state_manager.generator import nodesim_proto as proto

GENERATOR = Path(__file__).resolve().parents[1] / "generator"
KEEP_LABEL = "Keep the cloud simulator warm"
KEEP_NOTE = ("Starts a cloud instance when this panel opens; it uses one of the account's 3 instances "
             "until 10 min pass without a click. Off by default.")


def clean(value, secrets):
    """Scrub recursively before anything leaves memory (D-11, spec 7.6)."""
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(str(secret), "***")
        return value
    if isinstance(value, dict):
        return {clean(k, secrets): clean(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [clean(v, secrets) for v in value]
    return value


def worker_key(python, library, live):
    """Return the normalized three-path key (D-12, spec 2.1)."""
    parts = [os.path.normcase(str(Path(p).resolve())) for p in (python, library, live)]
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()[:16]


def remaining(lease):
    """Use the broker's server expiry, never a local lifetime guess (D-17, spec 3.4)."""
    from datetime import datetime, timezone
    return (datetime.fromisoformat(lease["expires_at"].replace("Z", "+00:00")) -
            datetime.now(timezone.utc)).total_seconds()


#: D-51: the remedy per login source, named in D-47's refusal
VERSION_ENV_LINE = ("No QOP version: the cloud login comes from SM's SAAS_* environment variables. Set SAAS_VERSION "
                    "(for example v3_7_3) in the environment SM was started from, then start SM again.")
VERSION_SAVED_LINE = "The saved Simulator login has no QOP version. Add one under Settings, Simulator login."


def version_refusal(credentials):
    """D-47 / D-51: the refusal line when the login carries no QOP version, else None.

    ``version_source`` names where the login came from ("env", "env_saved" or "saved"); a login without it is an
    environment login when it has no ``version`` key at all (the port-test ladder adds the key only when saved).
    """
    credentials = credentials or {}
    version = credentials.get("version")
    if isinstance(version, str) and version.strip():
        return None
    source = credentials.get("version_source")
    if source is None:
        source = "saved" if "version" in credentials else "env"
    return VERSION_SAVED_LINE if source == "saved" else VERSION_ENV_LINE


class PoolError(RuntimeError):
    """A server verdict, with separate state and scrubbed detail (spec 1)."""

    def __init__(self, state, **fields):
        """Keep the verdict fields for Manager.answer (spec 1)."""
        self.state, self.fields = state, fields
        super().__init__(state)


def kill(proc):
    """Kill only a recorded child PID and close its owned Job (spec 2.5)."""
    try:
        if proc.poll() is None:
            lab_worker._kill_tree(psutil.Process(proc.pid))
        proc.wait(timeout=5)
    except (psutil.Error, OSError, subprocess.TimeoutExpired):
        proc.kill()
    finally:
        lab_worker._close_owner(proc)


#: W F3: the window line when the chip copy's FEM layout cannot be generated
CLUSTER_FAILED = "The chip copy's FEM layout could not be read from its config: "
#: review S P3-8: the line when the worker could not read a message from SM
PROTOCOL_ERROR = ("The simulator process could not read a message from SM and stopped; "
                  "the next click starts a fresh one.")


class WorkerClient:
    """One protocol reader and one lock-owning execution thread (D-18)."""

    def __init__(self, python, library, live, saas_host, *, root=None, spawn=None,
                 lock=None, ready_s=60, lock_s=30, build_s=30, idle_s=1200, pid=None):
        """Spawn through the owned process path, without worker registry records (spec 2.1)."""
        self.key = worker_key(python, library, live)
        parent = nodesim_spawn.resolved(root or Path(tempfile.gettempdir()) / "sm_nodesim_warm")
        self.scratch = parent / f"{self.key}-{pid or os.getpid()}"
        self.chip = self.scratch / "chip"
        self.library = Path(library)
        self.live = Path(live).resolve()
        nodesim_spawn.prepare_scratch(self.scratch, self.chip)
        base = {k: v for k, v in os.environ.items() if not k.startswith("SAAS_")}
        env = nodesim_spawn.pinned_env(self.scratch, library, self.chip, base=base)
        self.proc = (spawn or lab_worker._spawn)(
            [str(python), "-B", str(GENERATOR / "nodesim_warm.py")], prepend_lab=False,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", cwd=str(self.scratch), env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.events, self.tasks = queue.Queue(), queue.Queue()
        self.write_lock = threading.Lock()
        self.machine_lock = lock or lab_worker.machine_lock
        self.ready_s, self.lock_s, self.build_s = ready_s, lock_s, build_s
        self.task_s = 610
        self.sources, self.lease, self.lease_seq = {}, None, 0
        self.used, self.busy = time.monotonic(), False
        self.closed = threading.Event()
        self.retired = False
        self.bye = None
        self.rss_at = 0.0
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.thread = threading.Thread(target=self._work, daemon=True)
        self.reader.start()
        self.thread.start()
        try:
            self.send(dict(op="hello", proto=proto.PROTO, key=self.key, scratch=str(self.scratch),
                           chip_dir=str(self.chip), library=str(self.library), idle_s=idle_s, saas_host=saas_host))
            ready = self.receive(ready_s)
            if ready["ev"] != "ready":
                raise PoolError("worker_crashed", code=self.proc.poll())
            nodesim_spawn.check_library({"library_file": ready["quam_config_file"]}, library)
            self.sources.update(ready["sources"])
        except BaseException:
            self.close()
            raise

    def _read(self):
        """Reject all foreign or malformed stdout, rather than ignoring it (spec 4.1)."""
        try:
            for line in self.proc.stdout:
                message = proto.decode(line)
                if message is None or message.get("ev") not in proto.WORKER_EVENTS:
                    self.events.put(None)
                    kill(self.proc)
                    break
                if message["ev"] == "bye":
                    self.bye = message["why"]  # review S P2-2: a worker that said bye is never reused.
                self.events.put(message)
        except (OSError, ValueError, UnicodeError):
            self.events.put(None)
            kill(self.proc)
        finally:
            self.events.put(None)

    def send(self, message):
        """Write only validated protocol messages, without logging (D-11, spec 4)."""
        with self.write_lock:
            self.proc.stdin.write(proto.encode_request(message))
            self.proc.stdin.flush()

    def receive(self, cap):
        """Bound protocol waits and detect EOF (spec 7.11)."""
        try:
            event = self.events.get(timeout=max(.001, cap))
        except queue.Empty:
            raise PoolError("worker_unready") from None
        if event is None:
            raise PoolError("worker_crashed", code=self.proc.poll())
        return event

    def changed(self):
        """Compare first-seen module stats, leaving node text to each click (D-5)."""
        for path, expected in self.sources.items():
            try:
                stat = Path(path).stat()
                if [stat.st_mtime_ns, stat.st_size] == expected:
                    continue
            except OSError:
                pass
            return Path(path).name
        return None

    def snapshot(self, copies, click_id):
        """Rewrite the fixed chip copy only between tasks (D-14, spec 2.2)."""
        for name in ("state.json", "wiring.json"):
            shutil.copyfile(Path(copies.after) / name, self.chip / name)
        identity = dict(seq=copies.seq, store_id=str(copies.store_id), click_id=click_id)
        (self.chip / "_snapshot.json").write_text(json.dumps(identity), encoding="ascii")
        return identity

    def submit(self, command, stop, on_event, lease_provider=None):
        """Queue execution on this worker's single lock thread (D-18)."""
        done, box = threading.Event(), {}
        self.tasks.put((command, stop, on_event, lease_provider, done, box))
        return done, box

    def _work(self):
        """Acquire, hold and release machine_lock on this thread alone (spec 2.4)."""
        while not self.closed.is_set():
            try:
                task = self.tasks.get(timeout=.1)
            except queue.Empty:
                continue
            command, stop, on_event, lease_provider, done, box = task
            held = False
            sent = False
            parked = None
            try:
                deadline = time.monotonic() + self.lock_s
                on_event(dict(ev="phase", phase="lock", phases=[]))
                while not stop.is_set() and not self.closed.is_set():
                    if self.machine_lock.acquire(blocking=False):
                        held = True
                        break
                    if time.monotonic() >= deadline:
                        raise PoolError("lab_busy")
                    stop.wait(.02)
                if stop.is_set() or self.closed.is_set():
                    raise PoolError("stopped_kept")
                self.send(command)
                sent = True
                deadline = time.monotonic() + self.build_s
                total_deadline = time.monotonic() + min(self.task_s,
                                                       float(command.get("wait_cap_s", 300)) + 310)
                sent_stop = False
                while time.monotonic() < total_deadline:
                    if stop.is_set() and not sent_stop:
                        self.send(dict(op="stop", click_id=command.get("click_id", "")))
                        sent_stop = True
                        total_deadline = min(total_deadline, time.monotonic() + 5)
                    if held and time.monotonic() >= deadline:
                        kill(self.proc)
                        raise PoolError("build_watchdog")
                    try:
                        event = self.events.get(timeout=.02)
                    except queue.Empty:
                        if time.monotonic() - self.rss_at >= .5:  # review S P3-6: a cap needs no 20 ms poll.
                            self.rss_at = time.monotonic()
                            try:
                                if psutil.Process(self.proc.pid).memory_info().rss > 2 * 1024**3:
                                    kill(self.proc)
                            except psutil.Error:
                                pass
                        continue
                    if event is None:
                        raise PoolError("worker_crashed", code=self.proc.poll())
                    ev = event["ev"]
                    if ev in ("build_done", "result", "cluster", "cluster_failed", "guard", "bye") and held:
                        self.machine_lock.release()
                        held = False
                    if ev == "build_done":
                        for path, stat in event["sources_new"].items():
                            self.sources.setdefault(path, stat)
                    on_event(event)
                    if ev == "need_lease":
                        try:
                            lease_provider(event)
                        except PoolError as exc:
                            if exc.state != "stopped_spawning":
                                raise
                            # Review S P3-1: the worker gets a stop and answers; it is not killed.
                            parked = exc
                            continue
                    if ev == "guard":
                        raise PoolError("guard", guard_line=event["line"])
                    if ev == "lease_failed":
                        raise PoolError("lease_failed", message=event["message"])
                    if ev == "cluster":
                        box["result"] = event
                        break
                    if ev == "cluster_failed":
                        # W F3: the worker stays up; the window shows the build line, no respawn.
                        box["error"] = PoolError("failed", message=CLUSTER_FAILED + event["message"])
                        break
                    if ev == "result":
                        try:
                            proto.check_snapshot(command, event)
                            box["result"] = event
                        except ValueError:
                            if proto.snapshot_unread(event) and event.get("kind") != "ok":
                                box["result"] = event  # W failed before reading the copy: its own line shows
                            else:
                                # Review int r1 P3-3: a different copy was loaded; the worker stays up.
                                box["error"] = PoolError("chip_changed")
                    if ev == "idle":
                        if parked:
                            box.pop("result", None)
                            box["error"] = parked
                        break
                    if ev == "bye":
                        if event["why"] == "protocol_error":
                            raise PoolError("failed", message=PROTOCOL_ERROR)
                        raise PoolError("worker_crashed", code=self.proc.poll())
                else:
                    kill(self.proc)
                    raise PoolError("worker_crashed", code=self.proc.poll())
            except BaseException as exc:
                if sent:
                    kill(self.proc)
                box["error"] = exc
            finally:
                if held:
                    self.machine_lock.release()
                self.used = time.monotonic()
                done.set()

    def set_lease(self, lease, active=False):
        """Validate before relay; obtain acknowledgement between clicks (D-11, spec 3.4)."""
        if not proto.is_allowed_saas_host(lease["host"], lease["port"]):
            raise PoolError("lease_failed", message="The instance address was refused.")
        self.lease_seq += 1
        self.send(dict(op="lease", lease_seq=self.lease_seq, **{k: lease[k] for k in proto.LEASE}))
        if not active:
            event = self.receive(30)
            if event["ev"] != "lease_ok":
                raise PoolError("lease_failed", message=event.get("message", "Lease refused."))
        self.lease = lease

    def close(self):
        """Retire by protocol then recorded PID and remove only owned scratch (spec 2.5)."""
        if self.retired:
            return
        self.retired = True
        self.closed.set()
        try:
            self.send(dict(op="exit"))
            self.proc.wait(timeout=5)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            kill(self.proc)
        finally:
            lab_worker._close_owner(self.proc)
        if threading.current_thread() is not self.thread:
            self.thread.join(timeout=6)
        self.reader.join(timeout=1)
        for stream in (self.proc.stdin, self.proc.stdout):
            try:
                stream.close()
            except OSError:
                pass
        shutil.rmtree(self.scratch, ignore_errors=True)


def broker_dir():
    """Return the per-user broker location, independently of instance (D-17)."""
    return Path(os.environ.get("QSM_SAAS_BROKER_DIR") or
                Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "quam-sm" / "saas-broker")


def start_broker(python, directory, popen=subprocess.Popen):
    """Start an independent broker with no inherited std handles (spec 3.1, Q-12)."""
    flags = (getattr(subprocess, "CREATE_NO_WINDOW", 0) |
             getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    extra = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
    kw = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
              env={k: v for k, v in os.environ.items() if not k.startswith("SAAS_")}, close_fds=True)
    argv = [str(python), "-I", "-B", str(GENERATOR / "nodesim_broker.py"), str(directory)]
    try:
        return popen(argv, creationflags=flags | extra, **kw)
    except OSError as exc:
        if getattr(exc, "winerror", None) != 5:
            raise
        return popen(argv, creationflags=flags, **kw)


class BrokerClient:
    """Authenticated bytes-only broker connection, serialized requests (spec 3-4)."""

    def __init__(self, directory, python, credentials, *, connector=None, starter=start_broker):
        """Connect only to a record with matching PID creation time (spec 3.1)."""
        from multiprocessing.connection import Client
        self.directory, self.python = Path(directory), python
        self.credentials = dict(credentials)
        self.connector, self.starter = connector or Client, starter
        self.lock, self.messages = threading.Lock(), queue.Queue()
        self.write_lock = threading.Lock()
        self.pushes, self.secrets = [], [credentials.get("password")]
        self.connection = None
        self.closed = threading.Event()
        self.disconnected = threading.Event()
        try:
            self.connect()
        except BaseException:
            self.close()
            raise

    def connect(self):
        """Start if needed, then authenticate with a bounded handshake (spec 3.1)."""
        record, started = None, False
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                candidate = json.loads((self.directory / "broker.json").read_text(encoding="utf-8"))
                process = psutil.Process(candidate["pid"])
                if proto.same_created(process.create_time(), candidate["created"]) and process.is_running():
                    record = candidate
                    break
            except (OSError, ValueError, KeyError, psutil.Error):
                pass
            if not started:
                self.starter(self.python, self.directory)
                started = True
            self.closed.wait(.05)
        if record is None:
            raise PoolError("broker_unstarted")
        if record.get("proto") != proto.PROTO:
            raise PoolError("broker_failed", message="Protocol version differs.")
        key = (self.directory / "broker.key").read_bytes()
        box, finished = {}, threading.Event()

        def open_pipe():
            """Bound the caller even when the OS pipe handshake stalls (spec 7.11)."""
            try:
                connection = self.connector(record["pipe"], authkey=key)
                if self.closed.is_set():
                    connection.close()
                else:
                    box["conn"] = connection
            except Exception as exc:
                box["error"] = exc
            finally:
                finished.set()
        threading.Thread(target=open_pipe, daemon=True).start()
        if not finished.wait(5) or "error" in box or "conn" not in box:
            raise PoolError("broker_unstarted")
        self.connection = box["conn"]
        self.reader = threading.Thread(target=self._read, args=(self.connection,), daemon=True)
        self.reader.start()
        creds = {k: self.credentials.get(k) for k in ("email", "password", "host")}
        creds["port"] = int(self.credentials.get("port") or 443)
        self.send(dict(op="hello", proto=proto.PROTO, sm_pid=os.getpid(),
                       sm_created=psutil.Process().create_time(), creds=creds))
        event = self.next(30)
        if event["ev"] != "hello_ok":
            self.connection.close()
            raise PoolError("broker_failed", message=event.get("line", event["ev"]))

    def _read(self, connection):
        """Read JSON bytes, never pickle, and route unsolicited events (spec 4.5)."""
        try:
            while not self.closed.is_set():
                if not connection.poll(.2):
                    continue
                event = proto.validate(json.loads(connection.recv_bytes(1024 * 1024).decode("utf-8")))
                if event["ev"] in ("renew_offer", "replaced", "closed"):
                    self.pushes.append(event)
                else:
                    self.messages.put(event)
        except (EOFError, OSError, ValueError):
            self.disconnected.set()
            self.messages.put(None)

    def send(self, message):
        """Send JSON bytes with no logging (D-11, spec 4.1)."""
        proto.validate(message)
        with self.write_lock:
            self.connection.send_bytes((json.dumps(message) + "\n").encode("utf-8"))

    def next(self, cap):
        """Bound every broker response wait (spec 7.11)."""
        try:
            event = self.messages.get(timeout=max(.001, cap))
        except queue.Empty:
            raise PoolError("broker_failed", message="The manager did not answer.") from None
        if event is None:
            raise PoolError("broker_disconnected")
        return event

    def request(self, message, *, stop=None, on_event=lambda event: None, cap=300):
        """Serialize acquire/status answers while allowing cancellation (D-19, spec 4.5)."""
        with self.lock:
            self.send(message)
            deadline, cancelled = time.monotonic() + cap, False
            while time.monotonic() < deadline:
                if stop is not None and stop.is_set() and not cancelled:
                    self.send(dict(op="cancel", req=message["req"]))
                    cancelled = True
                    raise PoolError("stopped_spawning")
                try:
                    event = self.messages.get(timeout=.05)
                except queue.Empty:
                    continue
                if event is None:
                    raise PoolError("broker_disconnected")
                if "req" not in message:
                    # A status call takes only the status answer (review S P2-1).
                    if event["ev"] == "status":
                        return event
                    continue
                if event.get("req") != message["req"]:
                    # Any answer to a req this client never sent is dropped (D-48, review S P2-1, P3-7).
                    continue
                if event["ev"] == "spawning":
                    on_event(event)
                    continue
                if event["ev"] == "cancelled":
                    continue
                return event
        raise PoolError("broker_failed", message="The manager did not answer.")

    def close(self):
        """Close this connection without terminating another owner's broker (D-17)."""
        self.closed.set()
        if self.connection is not None:
            self.connection.close()


class WarmPool:
    """At most two warm workers with leases retained across restarts (D-12, D-17, D-18)."""

    def __init__(self, instance, *, worker_factory=WorkerClient, broker_factory=BrokerClient,
                 known=None, probe=None, root=None, directory=None):
        """Inject transports and caps for offline verdict tests (spec 5, Piece S)."""
        from quam_state_manager.core.config_generator import known_interpreter
        from quam_state_manager.core.nodesim_click import probe_qm_saas
        self.instance, self.root = Path(instance), root
        self.worker_factory, self.broker_factory = worker_factory, broker_factory
        self.known = known or (lambda python: known_interpreter(python, instance))
        self.probe = probe or probe_qm_saas
        self.directory = Path(directory or broker_dir())
        self.workers, self.leases, self.secrets = {}, {}, []
        self.waiting = {}
        self.spawning = set()
        self.notices = {}
        self.lock = threading.RLock()
        self.broker = None
        self.credentials, self.broker_python = None, None
        self.pending, self.broker_error = [], None
        self.broker_lock = threading.Lock()
        self.closed = threading.Event()
        self.keep_warm = False
        try:
            self.keep_warm = json.loads(self.settings.read_text(encoding="utf-8")).get("keep_warm") is True
        except (OSError, ValueError):
            pass
        self.monitor = threading.Thread(target=self._monitor, daemon=True)
        self.monitor.start()
        atexit.register(self.close)

    @property
    def settings(self):
        """The settings file contains only the keep-warm boolean (D-11, spec 5)."""
        return self.instance / "nodesim_settings.json"

    def setting(self, value=None):
        """Persist only an explicit boolean (spec 1 Settings)."""
        if value is not None:
            if type(value) is not bool:
                raise ValueError("keep_warm must be a boolean")
            self.keep_warm = value
            self.instance.mkdir(parents=True, exist_ok=True)
            tmp = self.settings.with_suffix(".tmp")
            tmp.write_text(json.dumps(dict(keep_warm=value)), encoding="ascii")
            os.replace(tmp, self.settings)
        return dict(keep_warm=self.keep_warm, label=KEEP_LABEL, note=KEEP_NOTE)

    def ensure_broker(self, python, credentials):
        """Use only an interpreter that passed the environment gates (D-16, spec 3.3)."""
        with self.broker_lock:
            if self.broker is None or self.broker.disconnected.is_set():
                # A lost manager is replaced on the next use even with no lease held (review S P1-4); the old
                # client stays in place until the new one connects, so its pending messages are not dropped.
                fresh = self.broker_factory(self.directory, python, credentials)
                if self.broker is not None:
                    self.broker.close()
                self.broker = fresh
                self.flush_pending()
            self.credentials, self.broker_python = dict(credentials), python
        self.add_secret(credentials.get("password"))
        return self.broker

    def flush_pending(self):
        """Deliver occupancy/release messages kept while the manager was away (D-17, spec 3.3)."""
        # Review int r1 P3-6: through broker_send, so a client that died right after connecting keeps the
        # message pending and is marked disconnected instead of raising a bare OSError.
        pending, self.pending = self.pending, []
        for index, message in enumerate(pending):
            self.broker_send(message)
            if self.broker.disconnected.is_set():
                self.pending.extend(pending[index + 1:])
                return
        self.broker_error = None

    def start_reaper(self, credentials):
        """Validate the recorded interpreter before startup reaping (spec 3.3, review r2 P2-D)."""
        from quam_state_manager.core.nodesim_click import version_ok
        try:
            table = json.loads((self.directory / "leases.json").read_text(encoding="utf-8"))
            record = json.loads((self.directory / "broker.json").read_text(encoding="utf-8"))
            python = record["env"]
            if table.get("rows") and self.known(python) and version_ok(self.probe(python)) and credentials:
                self.ensure_broker(python, credentials)
        except (OSError, ValueError, KeyError, PoolError):
            return

    def broker_send(self, message):
        """Retain occupancy/release messages during reconnection (D-17, spec 3.3)."""
        if not self.broker:
            return
        try:
            if self.broker.disconnected.is_set():
                self.pending.append(message)
            else:
                self.broker.send(message)
        except (OSError, EOFError):
            self.broker.disconnected.set()
            self.pending.append(message)

    def worker(self, python, library, live, credentials, notify, stop=None):
        """Reserve a key and retire only idle LRU workers (D-12, spec 2.5)."""
        from quam_state_manager.core.nodesim_click import version_ok
        if not self.known(python):
            raise PoolError("no_env")
        found = self.probe(python)
        if not version_ok(found):
            raise PoolError("blocked", found=found, python=python, env=Path(python).parent.name)
        if not Path(library, "quam_config").is_dir():
            raise PoolError("no_library")
        key = worker_key(python, library, live)
        ticket = uuid.uuid4().hex
        with self.lock:
            self.waiting[key] = ticket
        deadline = time.monotonic() + 300
        gone = []
        while time.monotonic() < deadline:
            with self.lock:
                if self.waiting[key] != ticket:
                    raise PoolError(self.stop_state(key))
                existing = self.workers.get(key)
                if existing:
                    if not existing.busy:
                        existing.busy = True  # review S P3-3: reserved in the same locked step as the check
                        break
                elif key not in self.spawning:
                    # Review int r1 P2-1: spawning keys count against the cap of two, and the idle worker this
                    # key retires is taken out in the same locked step, so two new keys never retire one worker.
                    idle = [w for w in self.workers.values() if not w.busy]
                    if len(self.workers) + len(self.spawning) < 2 or idle:
                        if len(self.workers) + len(self.spawning) >= 2:
                            same = [w for w in idle if w.live == Path(live).resolve() and
                                    w.library.resolve() == Path(library).resolve()]
                            retired = (same or [min(idle, key=lambda w: w.used)])[0]
                            retired.busy = True
                            self.workers.pop(retired.key)
                            if same and retired.key in self.leases:
                                self.leases[key] = self.leases.pop(retired.key)  # an env switch keeps the lease
                            gone.append(retired)
                        self.spawning.add(key)
                        break
            notify("busy_chip", name=Path(live).name)
            if self.closed.wait(.05) or (stop is not None and stop.is_set()):
                raise PoolError(self.stop_state(key))
        else:
            raise PoolError("busy_chip", name=Path(live).name)
        old = worker = None
        try:
            with self.lock:
                worker = self.workers.get(key)
                if worker is None:
                    for candidate in list(self.workers.values()):
                        if (not candidate.busy and candidate.live == Path(live).resolve() and
                                candidate.library.resolve() == Path(library).resolve()):
                            old = candidate
                            self.workers.pop(candidate.key)
                            if candidate.key in self.leases:
                                self.leases[key] = self.leases.pop(candidate.key)
                            break
                if worker:
                    changed = worker.changed()
                    if changed or worker.proc.poll() is not None or worker.bye:
                        old, worker = worker, None
                        self.workers.pop(key)
                        self.spawning.add(key)
                        if changed:
                            notify("worker_restart", file=changed)
                if worker is None and len(self.workers) + len(self.spawning - {key}) >= 2:
                    idle = [w for w in self.workers.values() if not w.busy]
                    if not idle:
                        raise PoolError("busy_chip", name=Path(live).name)
                    retired = min(idle, key=lambda w: w.used)
                    retired.busy = True
                    self.workers.pop(retired.key)
                    gone.append(retired)
                if old:
                    gone.append(old)
            # Review S P3-2: closing and spawning (up to 60 s) run outside the pool lock.
            while gone:
                gone.pop().close()
            if worker is None:
                notify("warming", env=Path(python).parent.name)
                worker = self.worker_factory(python, library, live, credentials["host"], root=self.root)
                worker.busy = True
                with self.lock:
                    self.workers[key] = worker
                if key in self.leases:
                    worker.set_lease(self.leases[key])
                notify("ready")
        except BaseException:
            if worker is not None:
                worker.busy = False
            raise
        finally:
            while gone:  # a worker taken out for retirement is closed on every path
                gone.pop().close()
            with self.lock:
                self.spawning.discard(key)
        return worker

    def acquire(self, worker, python, credentials, event, stop, notify, reason="click"):
        """Request by hash and relay only a validated lease (D-13, spec 3.5)."""
        refused = version_refusal(credentials)
        if refused:
            # D-47 / D-51: never let the broker guess a version; refuse before any acquire, naming the source.
            raise PoolError("spawn_failed", message=refused)
        version = (credentials.get("version") or "").strip()
        broker = self.ensure_broker(python, credentials)
        old = self.leases.get(worker.key)
        if old and old["cluster_hash"] != event["hash"]:
            notify("needs_instance")
            raise PoolError("needs_instance")
        response = broker.request(dict(op="acquire", req=uuid.uuid4().hex,
                                       owner=dict(sm_pid=os.getpid(), worker_pid=worker.proc.pid),
                                       version=version, spec=event["spec"],
                                       hash=event["hash"], reason=reason), stop=stop,
                                  on_event=lambda e: notify("starting", in_use_here=e["in_use_here"]))
        ev = response["ev"]
        if ev == "no_slot":
            raise PoolError("no_slot", **{k: response[k] for k in ("in_use_here", "earliest_close")})
        if ev != "lease":
            raise PoolError("spawn_failed", message=response.get("line", ev))
        lease = {k: response[k] for k in proto.LEASE}
        self.add_secret(lease["headers"]["simulation-auth"])
        if not proto.is_allowed_saas_host(lease["host"], lease["port"]):
            broker.send(dict(op="release", instance_id=lease["instance_id"], why="host_refused"))
            raise PoolError("lease_failed", message="The instance address was refused.")
        self.leases[worker.key] = lease
        return lease

    def run(self, *, python, library, live, credentials, copies, request, stop, notify, retried=False):
        """One click with lock ownership, Stop policy and crash cleanup (D-18, spec 2.5)."""
        worker = self.worker(python, library, live, credentials, notify, stop)
        phase, spawned = "build", False
        try:
            if worker.lease and worker.key not in self.leases:
                worker.send(dict(op="drop_lease", lease_seq=worker.lease_seq))
                acknowledgement = worker.receive(5)
                if acknowledgement["ev"] != "lease_dropped":
                    raise PoolError("lease_failed", message="The previous instance could not be dropped.")
                worker.lease = None
            identity = worker.snapshot(copies, request["click_id"])
            command = dict(request, **identity, op="click")

            def events(event):
                """Record measured phases and job occupancy (D-19, spec 4.3)."""
                nonlocal phase
                if event["ev"] == "phase":
                    phase = event["phase"]
                    notify("running", phase=phase, phases=event["phases"])
                    lease = self.leases.get(worker.key)
                    if lease and self.broker and phase in ("submit", "wait", "report"):
                        self.broker_send(dict(op="in_job", instance_id=lease["instance_id"],
                                              click_id=command["click_id"] if phase != "report" else None))

            def lease_provider(event):
                """Keep lease material exclusively in memory (D-11)."""
                nonlocal spawned, phase
                phase = "lease"
                lease = self.acquire(worker, python, credentials, event, stop, notify)
                spawned = True
                worker.set_lease(lease, active=True)

            lease = self.leases.get(worker.key)
            if lease:
                from quam_state_manager.core.nodesim_click import family
                fit = family(request["node"])
                estimate = .281 + fit[1] + fit[2] * request["window_ns"] / 1000 if fit else request["wait_cap_s"]
                if remaining(lease) < estimate + 20:
                    raise PoolError("renew_offer", expires_at=lease["expires_at"])
            if lease and self.broker:
                self.broker_send(dict(op="touch", instance_id=lease["instance_id"], click_at=time.time()))
            done, box = worker.submit(command, stop, events, lease_provider)
            if not done.wait(float(request["wait_cap_s"]) + 350):
                kill(worker.proc)
                raise PoolError("worker_crashed", code=worker.proc.poll())
            if "error" in box:
                raise box["error"]
            result = box["result"]
            lease = self.leases.get(worker.key) or worker.lease
            if (not retried and not stop.is_set() and lease and remaining(lease) <= 5 and
                    phase in ("submit", "wait") and result.get("kind") in ("failed", "expired")):
                notify("expired_retry")
                self.release(worker.key, "expired")
                kill(worker.proc)
                worker.busy = False
                return self.run(python=python, library=library, live=live, credentials=credentials,
                                copies=copies, request=request, stop=stop, notify=notify, retried=True)
            if stop.is_set() or result.get("kind") == "timeout":
                if phase in ("submit", "wait"):
                    self.release(worker.key, "stopped_in_job")
                    raise PoolError("stopped")
                if spawned:
                    self.release(worker.key, "stop_spawned")
                    raise PoolError("stopped")
                raise PoolError(self.stop_state(worker.key))
            if lease:
                self.broker_send(dict(op="in_job", instance_id=lease["instance_id"], click_id=None))
            return clean(result, self.secrets)
        except PoolError as exc:
            if exc.state in ("worker_crashed", "guard") and phase in ("submit", "wait"):
                self.release(worker.key, "worker_died_in_job")
            if exc.state == "guard":
                kill(worker.proc)
            state = self.stop_state(worker.key) if exc.state == "stopped_kept" else exc.state
            raise PoolError(state, **clean(exc.fields, self.secrets)) from None
        finally:
            worker.busy = False

    def renew(self, *, python, library, live, credentials, copies, notify=lambda *a, **k: None):
        """Swap only after worker acknowledgement; keep the old instance on failure (D-17, spec 3.4)."""
        worker = self.worker(python, library, live, credentials, notify)
        old = self.leases.get(worker.key)
        new = None
        try:
            notify("renewing")
            worker.snapshot(copies, uuid.uuid4().hex)
            done, box = worker.submit(dict(op="cluster", seq=copies.seq), threading.Event(), lambda e: None)
            if not done.wait(worker.lock_s + worker.build_s + 5):
                raise PoolError("build_watchdog")
            if "error" in box:
                raise box["error"]
            new = self.acquire(worker, python, credentials, box["result"], threading.Event(), notify, "renew")
            worker.set_lease(new)
            if old:
                self.broker_send(dict(op="swapped", old=old["instance_id"], new=new["instance_id"]))
            self.notices.pop(worker.key, None)
        except (PoolError, OSError):
            if new:
                self.broker_send(dict(op="release", instance_id=new["instance_id"], why="swap_failed"))
            if old:
                self.leases[worker.key] = old
            raise
        finally:
            worker.busy = False

    def add_secret(self, value):
        """Scrub list without duplicates, so a re-queued push does not grow it (review S P3-4)."""
        if value and value not in self.secrets:
            self.secrets.append(value)

    def drop_instance(self, key):
        """Close this key's instance and clear the worker's lease while holding its job (review S P2-6)."""
        with self.lock:
            worker = self.workers.get(key)
            if worker and worker.busy:
                raise PoolError("busy")
            if worker:
                worker.busy = True
        try:
            self.release(key, "hash_changed")
            if worker and worker.lease:
                worker.send(dict(op="drop_lease", lease_seq=worker.lease_seq))
                if worker.receive(5)["ev"] != "lease_dropped":
                    raise PoolError("lease_failed", message="The previous instance could not be dropped.")
                worker.lease = None
        except OSError:
            kill(worker.proc)
            raise PoolError("worker_crashed", code=worker.proc.poll()) from None
        except PoolError as exc:
            if exc.state == "worker_unready":
                kill(worker.proc)
                raise PoolError("worker_crashed", code=worker.proc.poll()) from None
            raise
        finally:
            if worker:
                worker.busy = False

    def stop_state(self, key):
        """A Stop names a kept instance only when one is held for this key (review S P2-4, contract 11)."""
        return "stopped_kept" if key in self.leases else "stopped_none"

    def release(self, key, why):
        """Close only this owner's named instance (D-17, spec 4.5)."""
        lease = self.leases.pop(key, None)
        if lease and self.broker:
            self.broker_send(dict(op="release", instance_id=lease["instance_id"], why=why))

    def intent(self, *, python, library, live, credentials, copies, explicit=False, notify=lambda *a, **k: None):
        """Warm imports on panel open; acquire only on explicit intent or the setting (D-12)."""
        worker = self.worker(python, library, live, credentials, notify)
        try:
            if not explicit and not self.keep_warm:
                return
            worker.snapshot(copies, uuid.uuid4().hex)
            stop = threading.Event()
            done, box = worker.submit(dict(op="cluster", seq=copies.seq), stop, lambda event: None)
            if not done.wait(worker.lock_s + worker.build_s + 5):
                raise PoolError("build_watchdog")
            if "error" in box:
                raise box["error"]
            lease = self.acquire(worker, python, credentials, box["result"], stop, notify, "intent")
            worker.set_lease(lease)
        finally:
            worker.busy = False

    def instances(self):
        """Return token-free broker rows only (D-11, spec 4.5)."""
        if self.broker_error:
            raise PoolError("broker_failed", message=self.broker_error)
        if not self.broker:
            return dict(rows=[], in_use_here=0, limit=3)
        event = self.broker.request(dict(op="status"))
        allowed = {"state", "instance_id", "expires_at", "version", "cluster_hash", "owner_pid", "worker_pid",
                   "broker_pid", "last_click_at", "spawned_at", "in_job", "purpose"}
        event["rows"] = [{k: v for k, v in row.items() if k in allowed} for row in event["rows"]]
        return clean(event, self.secrets)

    def allow_cold(self, trigger, key):
        """Permit only the two fallback triggers and reserve a local slot (spec 2.7)."""
        if trigger not in ("worker_unready", "broker_unstarted"):
            return False
        self.release(key, "cold_fallback")
        if self.broker and not self.broker.disconnected.is_set():
            status = self.instances()
            return status["in_use_here"] <= 2
        return True

    def _monitor(self):
        """Handle idle workers, broker reconnection and replacement pushes (spec 2.5, 3.3-3.4)."""
        while not self.closed.wait(.5):
            for worker in list(self.workers.values()):
                if not worker.busy and time.monotonic() - worker.used > 1200:
                    with self.lock:
                        self.workers.pop(worker.key, None)
                    worker.close()
            if (self.broker and (self.leases or self.pending) and self.broker.disconnected.is_set() and
                    not self.broker_error):
                for attempt in range(3):
                    if self.closed.is_set():
                        break
                    try:
                        with self.broker_lock:
                            if not self.broker or not self.broker.disconnected.is_set():
                                break
                            self.broker.close()
                            self.broker = self.broker_factory(self.directory, self.broker_python,
                                                              self.credentials)
                            self.flush_pending()
                        break
                    except (PoolError, OSError):
                        if attempt == 2:
                            self.broker_error = "The manager could not reconnect."
                        self.closed.wait(.1)
            if self.broker:
                while self.broker.pushes:
                    event = self.broker.pushes.pop(0)
                    if event["ev"] == "closed":
                        for key, lease in list(self.leases.items()):
                            if lease["instance_id"] == event["instance_id"]:
                                self.leases.pop(key, None)
                                if event.get("why") == "idle":
                                    self.notices[key] = dict(state="idle_closed")
                    elif event["ev"] == "renew_offer":
                        for key, lease in self.leases.items():
                            if lease["instance_id"] == event["instance_id"]:
                                self.notices[key] = dict(state="renew_offer", expires_at=event["expires_at"])
                    elif event["ev"] == "replaced":
                        self.add_secret(event["lease"]["headers"]["simulation-auth"])
                        for key, old in list(self.leases.items()):
                            worker = self.workers.get(key)
                            if old["instance_id"] != event["old"]:
                                continue
                            if not worker or worker.busy:
                                self.broker.pushes.append(event)
                                break
                            with self.lock:
                                if worker.busy:
                                    self.broker.pushes.append(event)
                                    break
                                worker.busy = True
                            try:
                                worker.set_lease(event["lease"])
                                self.leases[key] = event["lease"]
                                self.broker.send(dict(op="swapped", old=event["old"],
                                                      new=event["lease"]["instance_id"]))
                            except (PoolError, OSError):
                                self.broker.send(dict(op="release", instance_id=event["lease"]["instance_id"],
                                                      why="swap_failed"))
                            finally:
                                worker.busy = False
                            break
                        break

    def close(self):
        """Release our leases and stop our workers, with bounded joins (D-17, spec 2.5)."""
        if self.closed.is_set():
            return
        self.closed.set()
        for key in list(self.leases):
            try:
                self.release(key, "sm_exit")
            except (OSError, PoolError):
                pass
        for worker in list(self.workers.values()):
            worker.close()
        self.workers.clear()
        if self.broker:
            self.broker.close()
        self.monitor.join(timeout=2)
        atexit.unregister(self.close)
